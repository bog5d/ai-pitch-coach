"""
单文件复盘编排：转写 → 敏感词打码 → LLM 打分 → JSON 落盘 → HTML 报告。
仓库发版 V7.5（与根目录 build_release.py → CURRENT_VERSION 对齐）。
Streamlit 可在调用本流水线前对大文件做音频网关压缩，再将 `audio_path` 指向网关产物。
HTML 内嵌音频由 report_builder 调用 imageio_ffmpeg 定位的 ffmpeg 子进程切片（Base64 MP3，
Windows 下隐藏控制台，失败时报告中降级为文字提示）。
供 Streamlit、CLI、自动化脚本共用；不含任何 UI 依赖。
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from llm_judge import evaluate_pitch
from report_builder import (
    HtmlExportOptions,
    apply_asr_original_text_override,
    generate_html_report,
)
from schema import AnalysisReport, TranscriptionWord
from transcriber import transcribe_audio

logger = logging.getLogger(__name__)

# 业务场景与默认双方角色（与 app 侧边栏一致）
SCENE_MAP: dict[str, str] = {
    "01_机构路演": "被尽调企业的投融资负责人 vs 投资机构",
    "02_高管访谈": "被尽调企业的高管 vs 投资机构",
    "03_客户访谈": "被尽调企业的客户 vs 投资机构",
    "04_供应商访谈": "被尽调企业的供应商 vs 投资机构",
    "05_其他(需手动输入)": "自定义",
}

OTHER_SCENE_KEY = "05_其他(需手动输入)"

# 外发 HTML 文件名默认脱敏（长键优先替换，避免短词误伤）
DEFAULT_HTML_FILENAME_MASKS: dict[str, str] = {
    "迪策资本": "DC资本",
    "邓勇": "DY",
}


def safe_fs_segment(name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", name.strip())
    return s or "未命名批次"


def apply_html_filename_masks(stem: str, masks: dict[str, str]) -> str:
    """对音频主文件名做脱敏，仅用于外发 HTML 文件名。"""
    if not masks:
        return stem
    s = stem
    for old in sorted(masks.keys(), key=len, reverse=True):
        new = masks[old]
        if old in s:
            s = s.replace(old, new)
    return s or "report"


def build_explicit_context(
    category: str,
    project_name: str,
    interviewee: str,
    *,
    session_notes: str = "",
    sniper_targets_json: str = "[]",
    recording_label: str = "",
    custom_roles_other: str = "",
) -> dict[str, str]:
    if category == OTHER_SCENE_KEY:
        roles = (custom_roles_other or "").strip()
    else:
        roles = SCENE_MAP.get(category, "未指定")
    sj = (sniper_targets_json or "").strip() or "[]"
    return {
        "biz_type": category,
        "exact_roles": roles or "未指定",
        "project_name": (project_name or "").strip() or "未指定",
        "interviewee": (interviewee or "").strip() or "未指定",
        "session_notes": (session_notes or "").strip(),
        "sniper_targets_json": sj,
        "recording_label": (recording_label or "").strip(),
    }


def mask_words_for_llm(
    words: list[TranscriptionWord],
    sensitive_words: list[str],
) -> list[TranscriptionWord]:
    if not sensitive_words:
        return words
    kws = [str(kw).strip() for kw in sensitive_words if kw and str(kw).strip()]
    if not kws:
        return words
    # 长词优先，避免「华为」先替换导致「华为云」无法整词匹配
    kws = sorted(set(kws), key=len, reverse=True)
    out: list[TranscriptionWord] = []
    for w in words:
        t = w.text
        for kw in kws:
            if kw in t:
                t = t.replace(kw, "***")
        if t != w.text:
            out.append(w.model_copy(update={"text": t}))
        else:
            out.append(w)
    return out


@dataclass(frozen=True)
class PitchFileJobParams:
    transcription_json_path: Path
    analysis_json_path: Path
    html_output_path: Path
    sensitive_words: list[str]
    explicit_context: dict[str, str]
    qa_text: str
    model_choice: str = "deepseek"
    html_export_options: HtmlExportOptions | None = None


def run_pitch_file_job(
    audio_path: Path,
    params: PitchFileJobParams,
    *,
    on_status: Callable[[str], None] | None = None,
    skip_html_export: bool = False,
    cached_words: list[TranscriptionWord] | None = None,
) -> tuple[list[TranscriptionWord], AnalysisReport]:
    """
    执行单条音频的完整流水线。失败时抛出异常，由调用方（如 Streamlit）捕获汇总。
    on_status 可选，用于 Streamlit st.status.update(label=...) 等 UI 进度。
    skip_html_export=True 时仅写 analysis JSON（初稿），不生成 HTML，供 V3 审查台人工确认后再导出。
    cached_words 非 None 时直接复用，跳过云端 ASR，同时仍将词列表写入转写 JSON 供归档。
    返回 (原始词级转写列表, AnalysisReport)（与送 LLM 的脱敏稿不同，HTML 与落盘 JSON 使用未脱敏 words）。
    """
    def _line(msg: str) -> None:
        logger.info("pipeline: %s", msg)
        if on_status:
            on_status(msg)

    if cached_words is not None:
        words = cached_words
        char_est = sum(len(w.text or "") for w in words)
        _line(
            f"✅ 已复用本条录音的转写缓存（约 {char_est} 字 / {len(words)} 个词级锚点），"
            "跳过云端 ASR，节省资源。正在执行商业机密脱敏打码…"
        )
        # 缓存命中时仍需落盘转写 JSON，保持归档完整性
        if params.transcription_json_path is not None:
            params.transcription_json_path.write_text(
                json.dumps([w.model_dump() for w in words], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    else:
        _line(f"⏱️ 正在提取音频特征：{audio_path.name}（耗时可能较长，请耐心等待）…")
        words = transcribe_audio(audio_path, out_json_path=params.transcription_json_path)
        char_est = sum(len(w.text or "") for w in words)
        _line(
            f"✅ 转写完成，共计约 {char_est} 字（{len(words)} 个词级锚点）。正在执行商业机密脱敏打码…"
        )
    words_for_llm = mask_words_for_llm(words, params.sensitive_words)
    _line(
        f"⏱️ {params.model_choice} 正在进行多维度 QA 对齐与痛点审查（结构化 JSON，最耗时一步）…"
    )
    report = evaluate_pitch(
        words_for_llm,
        model_choice=params.model_choice,
        explicit_context=params.explicit_context,
        qa_text=params.qa_text,
        on_notice=_line,
    )
    if skip_html_export:
        _line("✂️ AI 初稿已生成，等待人工审查台确认后再导出 HTML...")
    else:
        _line("✂️ 找茬完毕！正在疯狂裁剪原声音频，为您装订绝美复盘报告...")
    report_for_disk = apply_asr_original_text_override(report, words)
    params.analysis_json_path.write_text(
        json.dumps(report_for_disk.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not skip_html_export:
        generate_html_report(
            audio_path,
            words,
            report_for_disk,
            params.html_output_path,
            export_options=params.html_export_options,
        )
    return words, report_for_disk
