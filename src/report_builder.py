# 依赖：pip install pydub jinja2 pydantic imageio-ffmpeg
# 说明：优先通过 imageio-ffmpeg 注入 ffmpeg 目录到 PATH，避免 Windows 未单独安装 ffmpeg。
"""
终极报告拼装：真实 m4a + 词级时间戳 + AnalysisReport → 单文件 Base64 内嵌 MP3 的 HTML。
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

try:
    import imageio_ffmpeg

    os.environ["PATH"] += os.pathsep + os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
except ImportError:
    pass

from jinja2 import Environment, select_autoescape
from pydantic import ValidationError
from pydub import AudioSegment

from schema import AnalysisReport, TranscriptionWord
from runtime_paths import get_project_root, get_writable_app_root

# ---------------------------------------------------------------------------
_PROJ = get_project_root()
_WRITABLE = get_writable_app_root()

# 非对称缓冲：开头短切以贴近提问，结尾略长以保留答句余韵
PAD_START_SEC = 1.5
PAD_END_SEC = 8.0

TRANSCRIPTION_JSON = _WRITABLE / "output" / "real_transcription.json"
ANALYSIS_JSON = _WRITABLE / "output" / "real_analysis_report.json"
AUDIO_PATH = _PROJ / "tests" / "real_pitch.m4a"
OUTPUT_HTML = _WRITABLE / "output" / "final_pitch_report.html"


def slice_audio_to_base64(
    audio_segment: AudioSegment,
    start_sec: float,
    end_sec: float,
) -> str:
    """
    在词级锚定的 [start_sec, end_sec] 上应用非对称缓冲：
    开头仅回退 PAD_START_SEC（精准切入提问），结尾延长 PAD_END_SEC（保留信息冗余）。
    导出 MP3 至内存，返回 Base64 字符串（不含 data URI 前缀）。
    """
    duration_ms = len(audio_segment)
    start_ms = max(0, int((float(start_sec) - PAD_START_SEC) * 1000))
    end_ms = min(duration_ms, int((float(end_sec) + PAD_END_SEC) * 1000))
    if start_ms >= end_ms:
        end_ms = min(duration_ms, start_ms + 300)

    chunk = audio_segment[start_ms:end_ms]

    buf = io.BytesIO()
    try:
        chunk.export(buf, format="mp3", bitrate="128k")
    except Exception as e:
        raise RuntimeError(
            "MP3 导出失败（请确认已安装 ffmpeg 且在 PATH 中）: " + str(e)
        ) from e
    buf.seek(0)
    raw = buf.read()
    return base64.b64encode(raw).decode("ascii")


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


def _render_html(
    report: AnalysisReport,
    cards: list[dict],
) -> str:
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    tpl = env.from_string(_HTML_TEMPLATE)
    return tpl.render(
        scene=report.scene_analysis,
        total_score=report.total_score,
        cards=cards,
    )


def generate_html_report(
    audio_path: str | Path,
    words_list: List[TranscriptionWord],
    report_obj: AnalysisReport,
    output_html_path: str | Path,
) -> Path:
    """
    动态拼装：根据磁盘上的录音文件 + 内存中的转写与报告对象，生成 Base64 内嵌 MP3 的单文件 HTML。
    """
    ap = Path(audio_path)
    if not ap.is_file():
        raise FileNotFoundError(f"缺少录音文件: {ap}")

    by_index = _words_to_index_map(words_list)
    audio_seg = AudioSegment.from_file(str(ap))

    cards: list[dict] = []
    for idx, rp in enumerate(report_obj.risk_points, start=1):
        t0, t1 = _risk_time_range(
            by_index, rp.start_word_index, rp.end_word_index
        )
        b64 = slice_audio_to_base64(audio_seg, t0, t1)
        data_uri = f"data:audio/mp3;base64,{b64}"
        cards.append(
            {
                "index": idx,
                "risk_level": rp.risk_level,
                "tier1": rp.tier1_general_critique,
                "tier2": rp.tier2_qa_alignment,
                "improvement": rp.improvement_suggestion,
                "time_label": f"{t0:.2f}s — {t1:.2f}s（词 {rp.start_word_index}–{rp.end_word_index}）",
                "audio_data_uri": data_uri,
            }
        )

    html = _render_html(report_obj, cards)
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
        footer {
            text-align: center;
            margin-top: 40px;
            font-size: 0.8rem;
            color: var(--muted);
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
                    <div class="hint">以下每个翻车片段均可独立试听（开头约 1.5s、结尾约 8s 非对称物理缓冲）</div>
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

            <div class="player">
                <span>翻车片段试听（MP3）</span>
                <audio controls preload="metadata" src="{{ c.audio_data_uri }}"></audio>
            </div>
        </article>
        {% endfor %}

        <footer>AI 路演教练与复盘系统 · report_builder · 词级索引零误差切割</footer>
    </div>
</body>
</html>
"""


if __name__ == "__main__":
    print("正在切割真实音频并渲染终极报告...", flush=True)
    try:
        path = build_html_report()
    except (OSError, ValidationError, ValueError, KeyError, RuntimeError) as e:
        print(f"构建失败: {e}", file=sys.stderr, flush=True)
        raise SystemExit(1) from e
    print(f"完成。请用浏览器双击打开: {path}", flush=True)
