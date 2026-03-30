# 依赖：pip install jinja2 pydantic imageio-ffmpeg pypinyin（切片：ffmpeg 子进程，无 pydub）
# 说明：ffmpeg 路径仅来自 imageio_ffmpeg.get_ffmpeg_exe()，不依赖系统 PATH。
"""
终极报告拼装：真实 m4a + 词级时间戳 + AnalysisReport → 单文件 Base64 内嵌 MP3 的 HTML。
仓库发版 V6.2（与 build_release.CURRENT_VERSION 对齐）。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None  # type: ignore[assignment]

try:
    from pypinyin import Style, lazy_pinyin
except ImportError:
    lazy_pinyin = None  # type: ignore[assignment]
    Style = None  # type: ignore[assignment]

from jinja2 import Environment, select_autoescape
from pydantic import ValidationError

from schema import AnalysisReport, RiskPoint, SceneAnalysis, TranscriptionWord
from runtime_paths import get_project_root, get_writable_app_root

logger = logging.getLogger(__name__)

# 机构名常见后缀：先匹配长串，前缀取汉字拼音首字母大写后与后缀拼接（如 迪策资本 → DC资本）
_ORG_SUFFIXES: tuple[str, ...] = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "资本",
    "基金",
    "投资",
)


def _han_initials_segment(s: str) -> str:
    """将连续汉字转为拼音首字母大写；非汉字原样保留。"""
    if lazy_pinyin is None or Style is None:
        return re.sub(r"[\u4e00-\u9fff]", "*", s)
    parts: list[str] = []
    for ch in s:
        if "\u4e00" <= ch <= "\u9fff":
            py = lazy_pinyin(ch, style=Style.FIRST_LETTER)
            if py and py[0]:
                parts.append(str(py[0]).upper())
        else:
            parts.append(ch)
    return "".join(parts)


def desensitize_text(text: str, *, is_person: bool = False) -> str:
    """
    企业级 DLP 轻量脱敏（依赖 pypinyin）。
    - is_person=True：人名统一为 XXX。
    - is_person=False：机构/混合字符串——常见组织后缀前的汉字取首字母，后缀保留。
    """
    raw = (text or "").strip()
    if not raw:
        return "未命名"
    if is_person:
        return "XXX"
    for suf in sorted(_ORG_SUFFIXES, key=len, reverse=True):
        if raw.endswith(suf) and len(raw) > len(suf):
            prefix = raw[: -len(suf)]
            body = _han_initials_segment(prefix)
            return (body + suf) if body.strip() else suf
    out = _han_initials_segment(raw)
    return out if out.strip() else "机构"


@dataclass
class HtmlExportOptions:
    """
    仅影响生成的 HTML 展示：不修改磁盘上的 analysis JSON。
    content_replace_map 与文件名脱敏规则相同时，可传入同一 dict（长键优先替换）。
    """

    footer_watermark: str = ""
    content_replace_map: dict[str, str] | None = None
    show_generated_timestamp: bool = True


def _apply_text_masks(s: str, masks: dict[str, str]) -> str:
    if not masks:
        return s
    out = s
    for old in sorted(masks.keys(), key=len, reverse=True):
        out = out.replace(old, masks[old])
    return out


def _report_for_html_display(
    report: AnalysisReport,
    masks: dict[str, str] | None,
) -> AnalysisReport:
    if not masks:
        return report
    sa = report.scene_analysis
    scene = SceneAnalysis(
        scene_type=_apply_text_masks(sa.scene_type, masks),
        speaker_roles=_apply_text_masks(sa.speaker_roles, masks),
    )
    new_risks: List[RiskPoint] = []
    for rp in report.risk_points:
        new_risks.append(
            RiskPoint(
                risk_level=rp.risk_level,
                tier1_general_critique=_apply_text_masks(rp.tier1_general_critique, masks),
                tier2_qa_alignment=_apply_text_masks(rp.tier2_qa_alignment, masks),
                improvement_suggestion=_apply_text_masks(rp.improvement_suggestion, masks),
                original_text=_apply_text_masks(rp.original_text, masks),
                start_word_index=rp.start_word_index,
                end_word_index=rp.end_word_index,
                score_deduction=rp.score_deduction,
                deduction_reason=_apply_text_masks(rp.deduction_reason, masks),
                is_manual_entry=rp.is_manual_entry,
            )
        )
    return AnalysisReport(
        scene_analysis=scene,
        total_score=report.total_score,
        total_score_deduction_reason=_apply_text_masks(
            report.total_score_deduction_reason, masks
        ),
        risk_points=new_risks,
    )


# ---------------------------------------------------------------------------
_PROJ = get_project_root()
_WRITABLE = get_writable_app_root()

# 非对称缓冲：开头短切以贴近提问，结尾略长以保留答句余韵
PAD_START_SEC = 1.5
PAD_END_SEC = 8.0
# 单段内嵌 Base64 MP3 物理上限（秒），防止异常大索引撑爆 HTML
PHYSICAL_MAX_DURATION = 180.0

TRANSCRIPTION_JSON = _WRITABLE / "output" / "real_transcription.json"
ANALYSIS_JSON = _WRITABLE / "output" / "real_analysis_report.json"
AUDIO_PATH = _PROJ / "tests" / "real_pitch.m4a"
OUTPUT_HTML = _WRITABLE / "output" / "final_pitch_report.html"


def _get_ffmpeg_exe() -> str | None:
    if imageio_ffmpeg is None:
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _get_ffprobe_exe() -> str | None:
    ff = _get_ffmpeg_exe()
    if not ff:
        return None
    p = Path(ff)
    parent = p.parent
    name = p.name.lower()
    if name == "ffmpeg.exe":
        probe = parent / "ffprobe.exe"
    elif name == "ffmpeg":
        probe = parent / "ffprobe"
    else:
        probe = parent / str(p.name).replace("ffmpeg", "ffprobe")
    if probe.is_file():
        return str(probe)
    return None


def _subprocess_stealth_kwargs() -> dict:
    """Windows：隐藏控制台窗口，降低杀软/用户心理干扰。"""
    kw: dict = {}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kw["startupinfo"] = si
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def _ffprobe_duration_sec(audio_path: Path) -> float | None:
    exe = _get_ffprobe_exe()
    if not exe or not audio_path.is_file():
        return None
    cmd = [
        exe,
        "-hide_banner",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path.resolve()),
    ]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            **_subprocess_stealth_kwargs(),
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        return max(0.0, float((r.stdout or "").strip()))
    except Exception as e:
        logger.warning("ffprobe failed for %s: %s", audio_path, e)
        return None


def _padded_window_sec(
    start_word_t: float,
    end_word_t: float,
    media_duration: float | None,
) -> tuple[float, float]:
    """返回 (ss, duration) 供 ffmpeg -ss / -t 使用。"""
    t0 = max(0.0, float(start_word_t) - PAD_START_SEC)
    t1 = float(end_word_t) + PAD_END_SEC
    if media_duration is not None and media_duration > 0:
        t1 = min(t1, media_duration)
    dur = t1 - t0
    if dur <= 0:
        t1 = min((media_duration or t0 + 1.0), t0 + 0.35)
        dur = max(0.05, t1 - t0)
    elif dur > PHYSICAL_MAX_DURATION:
        logger.warning(
            "[Safety Guard] 检测到超长索引（%.2fs），已物理截断至 180s 极限以保护报告体积。",
            dur,
        )
        dur = PHYSICAL_MAX_DURATION
    return t0, dur


def _ffmpeg_slice_to_mp3_bytes(audio_path: Path, start_word_t: float, end_word_t: float) -> bytes | None:
    """
    使用 imageio_ffmpeg 提供的 ffmpeg 绝对路径，子进程截取 [ss, end_abs] 秒并输出 MP3（libmp3lame）。
    写入临时文件再读回，供 data:audio/mpeg;base64 内嵌；任意失败返回 None。
    """
    tmp_path: Path | None = None
    try:
        exe = _get_ffmpeg_exe()
        if not exe or not audio_path.is_file():
            return None
        media_d = _ffprobe_duration_sec(audio_path)
        ss, dur = _padded_window_sec(start_word_t, end_word_t, media_d)
        end_abs = float(ss) + float(dur)
        fd, tmp = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        tmp_path = Path(tmp)
        cmd = [
            exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(audio_path.resolve()),
            "-ss",
            f"{ss:.6f}",
            "-to",
            f"{end_abs:.6f}",
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-f",
            "mp3",
            str(tmp_path),
        ]
        logger.info(
            "ffmpeg mp3 slice: exe=%s -ss=%.6f -to=%.6f input=%s",
            exe,
            ss,
            end_abs,
            audio_path.name,
        )
        r = subprocess.run(
            cmd,
            capture_output=True,
            timeout=300,
            check=False,
            **_subprocess_stealth_kwargs(),
        )
        if r.returncode != 0:
            err = (r.stderr or b"")[:800].decode("utf-8", errors="replace")
            logger.warning("ffmpeg mp3 slice failed rc=%s: %s", r.returncode, err)
            return None
        out = tmp_path.read_bytes()
        if len(out) < 32:
            logger.warning("ffmpeg mp3 slice produced empty/short output")
            return None
        return out
    except Exception as e:
        logger.warning("ffmpeg mp3 slice exception: %s", e)
        return None
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def slice_audio_file_to_base64(
    audio_path: str | Path,
    start_sec: float,
    end_sec: float,
) -> str:
    """
    词级时间 [start_sec, end_sec] + 非对称缓冲，经 ffmpeg 导出 MP3 片段，返回纯 Base64 ASCII。
    失败时返回空字符串（不抛异常）。
    """
    try:
        raw = _ffmpeg_slice_to_mp3_bytes(Path(audio_path), start_sec, end_sec)
        if not raw:
            return ""
        return base64.b64encode(raw).decode("ascii")
    except Exception as e:
        logger.warning("slice_audio_file_to_base64: %s", e)
        return ""


def _words_to_index_map(words_list: List[TranscriptionWord]) -> Dict[int, TranscriptionWord]:
    """由内存中的词列表建立 word_index -> TranscriptionWord 映射。"""
    m: Dict[int, TranscriptionWord] = {}
    for w in words_list:
        m[w.word_index] = w
    return m


def _load_transcription_index(path: Path) -> Dict[int, TranscriptionWord]:
    """从 JSON 文件加载 word_index -> TranscriptionWord。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("转写 JSON 根节点须为数组")
    words: List[TranscriptionWord] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"转写第 {i} 项不是对象")
        words.append(TranscriptionWord.model_validate(item))
    return _words_to_index_map(words)


def _risk_time_range(
    by_index: Dict[int, TranscriptionWord],
    start_word_index: int,
    end_word_index: int,
) -> tuple[float, float]:
    if start_word_index not in by_index or end_word_index not in by_index:
        raise KeyError(
            f"词索引不在转写中: {start_word_index}–{end_word_index} "
            f"（请确认与 real_transcription.json 一致）"
        )
    if start_word_index > end_word_index:
        raise ValueError("start_word_index 不能大于 end_word_index")
    t0 = by_index[start_word_index].start_time
    t1 = by_index[end_word_index].end_time
    return t0, t1


def format_transcript_snippet(
    by_index: Dict[int, TranscriptionWord],
    start_word_index: int,
    end_word_index: int,
) -> str:
    """词索引闭区间内拼接转写文本，供审查台与说明使用。"""
    parts: List[str] = []
    lo, hi = start_word_index, end_word_index
    if lo > hi:
        lo, hi = hi, lo
    for idx in range(lo, hi + 1):
        w = by_index.get(idx)
        if w and (w.text or "").strip():
            parts.append(w.text.strip())
    return " ".join(parts) if parts else "（该范围内无转写词）"


def snippet_audio_mp3_bytes(
    audio_path: str | Path,
    words_list: List[TranscriptionWord],
    start_word_index: int,
    end_word_index: int,
) -> bytes | None:
    """导出与翻车片段对齐的 MP3 字节（供 Streamlit st.audio format=audio/mpeg）；失败返回 None。"""
    ap = Path(audio_path)
    if not ap.is_file():
        return None
    by_index = _words_to_index_map(words_list)
    try:
        t0, t1 = _risk_time_range(by_index, start_word_index, end_word_index)
        return _ffmpeg_slice_to_mp3_bytes(ap, t0, t1)
    except (KeyError, ValueError, OSError):
        return None


def _render_html(
    report: AnalysisReport,
    cards: list[dict],
    *,
    total_score_deduction: str = "",
    watermark_line: str = "",
    generated_footer_line: str = "",
) -> str:
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    tpl = env.from_string(_HTML_TEMPLATE)
    return tpl.render(
        scene=report.scene_analysis,
        total_score=report.total_score,
        total_score_deduction=total_score_deduction or "",
        cards=cards,
        watermark_line=watermark_line or "",
        generated_footer_line=generated_footer_line or "",
    )


def generate_html_report(
    audio_path: str | Path,
    words_list: List[TranscriptionWord],
    report_obj: AnalysisReport,
    output_html_path: str | Path,
    *,
    export_options: HtmlExportOptions | None = None,
) -> Path:
    """
    动态拼装：根据磁盘上的录音文件 + 内存中的转写与报告对象，生成 Base64 内嵌 MP3 的单文件 HTML。
    export_options 仅影响 HTML 正文/页脚展示；analysis JSON 由调用方另行落盘，保持完整口径。
    """
    ap = Path(audio_path)
    if not ap.is_file():
        raise FileNotFoundError(f"缺少录音文件: {ap}")

    opts = export_options or HtmlExportOptions()
    report_display = _report_for_html_display(
        report_obj, opts.content_replace_map
    )

    by_index = _words_to_index_map(words_list)

    cards: list[dict] = []
    for idx, rp in enumerate(report_display.risk_points, start=1):
        data_uri = ""
        time_label = "人工复盘点（无自动音频切片）"
        original_text = ""
        audio_extraction_failed = False
        if not rp.is_manual_entry:
            llm_ot = (rp.original_text or "").strip()
            original_text = (
                llm_ot
                if llm_ot
                else format_transcript_snippet(
                    by_index, rp.start_word_index, rp.end_word_index
                )
            )
            try:
                t0, t1 = _risk_time_range(
                    by_index, rp.start_word_index, rp.end_word_index
                )
                b64 = slice_audio_file_to_base64(ap, t0, t1)
                if b64:
                    data_uri = f"data:audio/mpeg;base64,{b64}"
                else:
                    audio_extraction_failed = True
                time_label = (
                    f"{t0:.2f}s — {t1:.2f}s（词 {rp.start_word_index}–{rp.end_word_index}）"
                )
            except (KeyError, ValueError):
                time_label = "无法对齐词索引（无音频切片）"
        else:
            original_text = "（人工增补条目：无词级时间锚，以下正文见 Tier 1 / Tier 2 / 改进建议。）"
        cards.append(
            {
                "index": idx,
                "risk_level": rp.risk_level,
                "tier1": rp.tier1_general_critique,
                "tier2": rp.tier2_qa_alignment,
                "improvement": rp.improvement_suggestion,
                "deduction_reason": rp.deduction_reason or "",
                "time_label": time_label,
                "audio_data_uri": data_uri,
                "has_audio": bool(data_uri),
                "audio_extraction_failed": audio_extraction_failed,
                "original_text": original_text or "",
                "is_manual": bool(rp.is_manual_entry),
            }
        )

    ts = ""
    if opts.show_generated_timestamp:
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    gen_line = "AI 路演教练与复盘系统 · report_builder · ffmpeg 词级 MP3 切片 · Base64 单文件"
    if ts:
        gen_line = f"{gen_line} · 生成 {ts}"

    html = _render_html(
        report_display,
        cards,
        total_score_deduction=report_display.total_score_deduction_reason or "",
        watermark_line=(opts.footer_watermark or "").strip(),
        generated_footer_line=gen_line,
    )
    out = Path(output_html_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def build_html_report(
    transcription_path: Path | None = None,
    analysis_path: Path | None = None,
    audio_path: Path | None = None,
    output_path: Path | None = None,
) -> Path:
    """
    从 JSON 文件路径读取转写与报告，再调用 generate_html_report（兼容旧 CLI）。
    """
    tpath = transcription_path or TRANSCRIPTION_JSON
    apath = analysis_path or ANALYSIS_JSON
    mpath = audio_path or AUDIO_PATH
    out = output_path or OUTPUT_HTML

    if not tpath.is_file():
        raise FileNotFoundError(f"缺少转写文件: {tpath}")
    if not apath.is_file():
        raise FileNotFoundError(f"缺少分析报告: {apath}")
    if not mpath.is_file():
        raise FileNotFoundError(f"缺少录音文件: {mpath}")

    data = json.loads(tpath.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("转写 JSON 根节点须为数组")
    words_list: List[TranscriptionWord] = [
        TranscriptionWord.model_validate(item) for item in data if isinstance(item, dict)
    ]
    report = AnalysisReport.model_validate_json(apath.read_text(encoding="utf-8"))
    return generate_html_report(mpath, words_list, report, out)


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>路演复盘 · 终极报告</title>
    <style>
        :root {
            --bg0: #0a0c10;
            --bg1: #12151c;
            --card: #181c26;
            --line: rgba(255,255,255,0.06);
            --text: #e9edf5;
            --muted: #8b95a8;
            --accent: #7c9cff;
            --accent2: #5eead4;
            --severe: #f87171;
            --warn: #fbbf24;
            --mild: #4ade80;
            --radius: 18px;
            --shadow: 0 24px 60px rgba(0,0,0,0.55);
            --font: "Segoe UI", system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            font-family: var(--font);
            color: var(--text);
            background:
                radial-gradient(1000px 500px at 15% -5%, rgba(124, 156, 255, 0.12), transparent 55%),
                radial-gradient(800px 400px at 95% 10%, rgba(94, 234, 212, 0.08), transparent 50%),
                linear-gradient(165deg, var(--bg0), var(--bg1));
            line-height: 1.65;
        }
        .shell { max-width: 900px; margin: 0 auto; padding: 48px 22px 72px; }
        .hero {
            background: linear-gradient(145deg, rgba(24,28,38,0.95), rgba(18,21,28,0.98));
            border: 1px solid var(--line);
            border-radius: var(--radius);
            padding: 36px 40px;
            box-shadow: var(--shadow);
            margin-bottom: 28px;
        }
        .eyebrow {
            font-size: 0.72rem;
            letter-spacing: 0.2em;
            text-transform: uppercase;
            color: var(--muted);
            margin-bottom: 10px;
        }
        h1 {
            margin: 0 0 8px;
            font-size: 1.85rem;
            font-weight: 700;
            letter-spacing: -0.03em;
        }
        .sub { margin: 0; color: var(--muted); font-size: 0.95rem; }
        .scene-grid {
            display: grid;
            gap: 14px;
            margin-top: 26px;
        }
        .scene-item {
            padding: 16px 18px;
            border-radius: 14px;
            background: rgba(124, 156, 255, 0.06);
            border-left: 3px solid var(--accent);
        }
        .scene-item strong { color: var(--accent2); font-size: 0.78rem; letter-spacing: 0.08em; }
        .scene-item p { margin: 8px 0 0; font-size: 0.98rem; }
        .score-row {
            display: flex;
            align-items: center;
            gap: 22px;
            margin-top: 28px;
            flex-wrap: wrap;
        }
        .score-ring {
            width: 108px; height: 108px;
            border-radius: 50%;
            background: conic-gradient(var(--accent) {{ (total_score * 3.6) }}deg, rgba(255,255,255,0.1) 0);
            display: grid; place-items: center;
            box-shadow: inset 0 0 0 7px rgba(10,12,16,0.85);
        }
        .score-inner {
            width: 80px; height: 80px;
            border-radius: 50%;
            background: var(--bg0);
            display: grid; place-items: center;
            font-size: 1.55rem;
            font-weight: 800;
        }
        .score-meta { flex: 1; min-width: 200px; }
        .score-meta .big { font-size: 1.1rem; color: var(--accent); font-weight: 600; }
        .score-meta .hint { margin-top: 6px; font-size: 0.88rem; color: var(--muted); }
        .score-deduction {
            margin-top: 14px;
            padding: 12px 14px;
            border-radius: 12px;
            background: rgba(251,191,36,0.08);
            border-left: 3px solid var(--warn);
            font-size: 0.9rem;
            color: var(--text);
            white-space: pre-wrap;
            word-break: break-word;
        }

        .card {
            background: var(--card);
            border: 1px solid var(--line);
            border-radius: var(--radius);
            padding: 26px 28px 24px;
            margin-bottom: 20px;
            box-shadow: 0 16px 48px rgba(0,0,0,0.35);
        }
        .card-head {
            display: flex; flex-wrap: wrap; align-items: center; gap: 12px;
            margin-bottom: 18px;
        }
        .card-idx { font-weight: 700; color: var(--muted); font-size: 0.88rem; }
        .badge {
            padding: 5px 14px;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.04em;
        }
        .badge-severe { background: rgba(248,113,113,0.15); color: var(--severe); }
        .badge-medium { background: rgba(251,191,36,0.12); color: var(--warn); }
        .badge-mild { background: rgba(74,222,128,0.12); color: var(--mild); }
        .time-pill {
            margin-left: auto;
            font-size: 0.8rem;
            color: var(--muted);
        }
        .block-title {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: var(--muted);
            margin: 0 0 8px;
        }
        .block-body { margin: 0 0 18px; font-size: 0.96rem; }
        .tier1 { border-left: 3px solid var(--accent); padding-left: 14px; }
        .tier2 { border-left: 3px solid var(--accent2); padding-left: 14px; }
        .improve-wrap {
            margin-top: 8px;
            padding: 16px 18px;
            border-radius: 14px;
            background: linear-gradient(120deg, rgba(124,156,255,0.1), rgba(94,234,212,0.06));
            border: 1px solid rgba(124,156,255,0.2);
        }
        .improve-wrap .block-title { color: var(--accent); letter-spacing: 0.06em; }
        .improve-wrap p { margin: 0; font-weight: 500; }
        .player {
            margin-top: 20px;
            padding-top: 16px;
            border-top: 1px solid var(--line);
        }
        .player span {
            display: block;
            font-size: 0.78rem;
            color: var(--muted);
            margin-bottom: 8px;
        }
        audio { width: 100%; height: 42px; border-radius: 10px; }
        .original-text {
            margin-top: 14px;
            padding: 14px 16px;
            border-radius: 12px;
            background: rgba(0,0,0,0.25);
            border: 1px solid var(--line);
            font-size: 0.92rem;
            line-height: 1.55;
            color: var(--text);
            white-space: pre-wrap;
            word-break: break-word;
        }
        .original-text .lbl {
            display: block;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--muted);
            margin-bottom: 8px;
        }
        .block-body.prewrap, .deduction-reason { white-space: pre-wrap; word-break: break-word; }
        footer {
            text-align: center;
            margin-top: 40px;
            font-size: 0.8rem;
            color: var(--muted);
        }
        .watermark {
            margin-bottom: 12px;
            padding: 12px 16px;
            border-radius: 12px;
            border: 1px dashed rgba(251,191,36,0.35);
            color: var(--warn);
            font-weight: 600;
        }
    </style>
</head>
<body>
    <div class="shell">
        <header class="hero">
            <div class="eyebrow">AI Pitch Coach · Final Report</div>
            <h1>路演复盘报告</h1>
            <p class="sub">单文件离线预览 · 词级锚定切片 · 双层诊断</p>

            <div class="scene-grid">
                <div class="scene-item">
                    <strong>场景推断</strong>
                    <p>{{ scene.scene_type }}</p>
                </div>
                <div class="scene-item" style="border-left-color: var(--accent2); background: rgba(94,234,212,0.05);">
                    <strong>身份与氛围</strong>
                    <p>{{ scene.speaker_roles }}</p>
                </div>
            </div>

            <div class="score-row">
                <div class="score-ring" aria-hidden="true">
                    <div class="score-inner">{{ total_score }}</div>
                </div>
                <div class="score-meta">
                    <div class="big">综合得分 {{ total_score }} / 100</div>
                    <div class="hint">以下每个翻车片段均可独立试听（Base64 内嵌 MP3；开头约 1.5s、结尾约 8s 非对称缓冲）；人工条目无切片。</div>
                    <div class="score-deduction"><strong>总分扣分说明</strong><br/>{{ total_score_deduction or "（未填写）" }}</div>
                </div>
            </div>
        </header>

        {% for c in cards %}
        <article class="card">
            <div class="card-head">
                <span class="card-idx">#{{ c.index }}</span>
                {% if c.risk_level == "严重" %}
                <span class="badge badge-severe">{{ c.risk_level }}</span>
                {% elif c.risk_level == "一般" %}
                <span class="badge badge-medium">{{ c.risk_level }}</span>
                {% else %}
                <span class="badge badge-mild">{{ c.risk_level }}</span>
                {% endif %}
                {% if c.is_manual %}
                <span class="badge badge-medium" style="background: rgba(248,113,113,0.25); color: #fca5a5; border: 1px solid rgba(248,113,113,0.45);">【人工发现】</span>
                {% endif %}
                <span class="time-pill">{{ c.time_label }}</span>
            </div>

            <p class="block-title">Tier 1 · 全球顶尖 VC 视角</p>
            <div class="block-body tier1">{{ c.tier1 }}</div>

            <p class="block-title">Tier 2 · 内部 QA 对齐视角</p>
            <div class="block-body tier2">{{ c.tier2 }}</div>

            <div class="improve-wrap">
                <p class="block-title">改进建议</p>
                <p>{{ c.improvement }}</p>
            </div>

            <p class="block-title">扣分 / QA 口径对照</p>
            <div class="block-body tier2 deduction-reason">{{ c.deduction_reason or "（未填写）" }}</div>

            <div class="player">
                <span>翻车片段试听（内嵌 Base64 · MP3）</span>
                {% if c.has_audio %}
                <audio controls preload="metadata" src="{{ c.audio_data_uri }}"></audio>
                {% elif c.audio_extraction_failed %}
                <p style="color:red; font-size:12px;">🔈 受限于当前电脑的安全拦截策略，该片段音频提取失败，请参考下方文字阅览。</p>
                {% elif c.is_manual %}
                <p class="sub" style="color: var(--muted); margin: 0;">本条目为【人工发现】，无自动音频切片。</p>
                {% else %}
                <p class="sub" style="color: var(--muted); margin: 0;">本条目无自动音频切片（索引无法对齐等）。</p>
                {% endif %}
                {% if c.original_text %}
                <div class="original-text"><span class="lbl">发言人口述实录</span>{{ c.original_text }}</div>
                {% endif %}
            </div>
        </article>
        {% endfor %}

        <footer>
            {% if watermark_line %}
            <p class="watermark">{{ watermark_line }}</p>
            {% endif %}
            <p>{{ generated_footer_line }}</p>
        </footer>
    </div>
</body>
</html>
"""


if __name__ == "__main__":
    print("正在切割真实音频并渲染终极报告...", flush=True)
    try:
        path = build_html_report()
    except (OSError, ValidationError, ValueError, KeyError) as e:
        print(f"构建失败: {e}", file=sys.stderr, flush=True)
        raise SystemExit(1) from e
    print(f"完成。请用浏览器双击打开: {path}", flush=True)
