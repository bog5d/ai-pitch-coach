"""
单文件复盘编排：转写 → 敏感词打码 → LLM 打分 → JSON 落盘 → HTML 报告。
供 Streamlit、CLI、自动化脚本共用；不含任何 UI 依赖。
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from llm_judge import evaluate_pitch
from report_builder import HtmlExportOptions, generate_html_report
from schema import TranscriptionWord
from transcriber import transcribe_audio

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
    recording_label: str = "",
    custom_roles_other: str = "",
) -> dict[str, str]:
    if category == OTHER_SCENE_KEY:
        roles = (custom_roles_other or "").strip()
    else:
        roles = SCENE_MAP.get(category, "未指定")
    return {
        "biz_type": category,
        "exact_roles": roles or "未指定",
        "project_name": (project_name or "").strip() or "未指定",
        "interviewee": (interviewee or "").strip() or "未指定",
        "session_notes": (session_notes or "").strip(),
        "recording_label": (recording_label or "").strip(),
    }


def mask_words_for_llm(
    words: list[TranscriptionWord],
    sensitive_words: list[str],
) -> list[TranscriptionWord]:
    if not sensitive_words:
        return words
    out: list[TranscriptionWord] = []
    for w in words:
        t = w.text
        for kw in sensitive_words:
            if kw and kw in t:
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
) -> None:
    """
    执行单条音频的完整流水线。失败时抛出异常，由调用方（如 Streamlit）捕获汇总。
    on_status 可选，用于 Streamlit st.status.update(label=...) 等 UI 进度。
    """
    def _line(msg: str) -> None:
        if on_status:
            on_status(msg)

    _line(
        f"🎧 正在竖起耳朵听 {audio_path.name} (音频转写中，请耐心喝口水)..."
    )
    words = transcribe_audio(audio_path, out_json_path=params.transcription_json_path)
    _line(
        f"🕵️ 听写完毕！共抓取 {len(words)} 个词汇。正在执行商业机密脱敏打码..."
    )
    words_for_llm = mask_words_for_llm(words, params.sensitive_words)
    _line("🧠 正在请出顶级 VC 大脑，戴上老花镜逐字找茬中 (最耗时的一步，让子弹飞一会儿)...")
    report = evaluate_pitch(
        words_for_llm,
        model_choice=params.model_choice,
        explicit_context=params.explicit_context,
        qa_text=params.qa_text,
    )
    _line("✂️ 找茬完毕！正在疯狂裁剪原声音频，为您装订绝美复盘报告...")
    params.analysis_json_path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    generate_html_report(
        audio_path,
        words,
        report,
        params.html_output_path,
        export_options=params.html_export_options,
    )
