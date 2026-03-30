"""
阶段 2：无脑物理切割机 — 独立验证链路
词级索引映射 -> 音频切割（wave）-> Base64 注入 HTML
零 ffmpeg / pydub，仅依赖 Python 标准库 + Pydantic。
仓库发版 V7.0（与 build_release.CURRENT_VERSION 对齐；开发/回归工具链，非 Streamlit 主路径）。
"""
from __future__ import annotations

import base64
import io
import json
import math
import struct
import wave
from html import escape
from pathlib import Path

from pydantic import ValidationError

# 与 schema 同包：从项目根运行时需保证能 import src.schema
from schema import AnalysisReport, TranscriptionWord
from runtime_paths import get_project_root, get_writable_app_root

# ---------------------------------------------------------------------------
# 路径：项目根（内置 tests）与可写 output 分离，兼容 PyInstaller
# ---------------------------------------------------------------------------
_PROJ = get_project_root()
_WRITABLE = get_writable_app_root()
TESTS_DIR = _PROJ / "tests"
OUTPUT_DIR = _WRITABLE / "output"
DUMMY_WAV = TESTS_DIR / "dummy.wav"
DUMMY_JSON = TESTS_DIR / "dummy_data.json"
REPORT_HTML = OUTPUT_DIR / "test_report.html"

# 与生成 dummy.wav 时完全一致的音频参数（切割时必须一致）
SAMPLE_RATE = 44100
N_CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM
DURATION_SEC = 60
NUM_WORDS = 60


def generate_dummy_wav(path: Path) -> None:
    """
    使用 wave + math 生成约 60 秒单声道 16bit WAV。
    每秒使用略有变化的正弦频率，形成可辨的「嘟嘟」测试音，便于人耳确认切片是否正确。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = SAMPLE_RATE * DURATION_SEC
    # 先在内存中批量生成 PCM（小端 int16），再一次性 writeframes，避免数百万次磁盘写入
    buf = bytearray(n_frames * SAMPLE_WIDTH)
    for i in range(n_frames):
        t = i / SAMPLE_RATE
        sec = int(t) % NUM_WORDS
        freq_hz = 380.0 + (sec % 7) * 35.0
        amp = 0.22 * (0.85 + 0.15 * math.sin(2 * math.pi * 0.5 * t))
        sample = int(max(-32767, min(32767, 32767.0 * amp * math.sin(2 * math.pi * freq_hz * t))))
        struct.pack_into("<h", buf, i * SAMPLE_WIDTH, sample)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(N_CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.setcomptype("NONE", "not compressed")
        wf.writeframes(bytes(buf))


def build_transcription_mock() -> list[TranscriptionWord]:
    """
    伪造 60 个词的转写：第 i 个词占用 [i, i+1) 秒，与 dummy.wav 时长对齐。
    """
    words: list[TranscriptionWord] = []
    for i in range(NUM_WORDS):
        words.append(
            TranscriptionWord(
                word_index=i,
                text=f"词_{i}",
                start_time=float(i),
                end_time=float(i + 1),
                speaker_id="路演方" if i % 2 == 0 else "投资方",
            )
        )
    return words


def load_analysis_report(json_path: Path) -> AnalysisReport:
    """从 tests/dummy_data.json 解析为 AnalysisReport（Pydantic v2）。"""
    raw = json_path.read_text(encoding="utf-8")
    return AnalysisReport.model_validate_json(raw)


def word_index_to_time_range(
    words: list[TranscriptionWord],
    start_word_index: int,
    end_word_index: int,
) -> tuple[float, float]:
    """
    通过词级索引得到切片起止时间（秒）：
    - 起点：start_word_index 对应词的 start_time
    - 终点：end_word_index 对应词的 end_time（含该词整段）
    """
    by_index = {w.word_index: w for w in words}
    if start_word_index not in by_index or end_word_index not in by_index:
        raise ValueError(
            f"索引越界或缺失: start={start_word_index}, end={end_word_index}, "
            f"有效范围 0..{NUM_WORDS - 1}"
        )
    if start_word_index > end_word_index:
        raise ValueError("start_word_index 不能大于 end_word_index")
    start_sec = by_index[start_word_index].start_time
    end_sec = by_index[end_word_index].end_time
    return start_sec, end_sec


def slice_wav_pcm_from_file(
    wav_path: Path,
    start_sec: float,
    end_sec: float,
) -> bytes:
    """
    用标准库 wave 从整段 WAV 中按时间截取 PCM 帧（不依赖 ffmpeg）。
    返回原始 PCM 字节（不含 WAV 头）。
    """
    start_frame = int(start_sec * SAMPLE_RATE)
    end_frame = int(math.ceil(end_sec * SAMPLE_RATE))

    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != N_CHANNELS or wf.getsampwidth() != SAMPLE_WIDTH:
            raise RuntimeError("dummy.wav 格式与脚本常量不一致，请重新生成。")
        if wf.getframerate() != SAMPLE_RATE:
            raise RuntimeError("采样率不一致")

        total = wf.getnframes()
        start_frame = max(0, min(start_frame, total))
        end_frame = max(start_frame, min(end_frame, total))
        n_read = end_frame - start_frame

        wf.rewind()
        if start_frame > 0:
            wf.readframes(start_frame)  # 丢弃前导帧
        return wf.readframes(n_read)


def pcm_bytes_to_wav_file_bytes(pcm: bytes) -> bytes:
    """
    将裸 PCM 包装成标准 WAV 字节流，便于浏览器 data:audio/wav;base64,... 播放。
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(N_CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.setcomptype("NONE", "not compressed")
        wf.writeframes(pcm)
    return buf.getvalue()


def wav_segment_to_data_uri(wav_path: Path, start_sec: float, end_sec: float) -> str:
    """切割 -> 封装 WAV -> Base64 -> data URI。"""
    pcm = slice_wav_pcm_from_file(wav_path, start_sec, end_sec)
    wav_blob = pcm_bytes_to_wav_file_bytes(pcm)
    b64 = base64.b64encode(wav_blob).decode("ascii")
    return f"data:audio/wav;base64,{b64}"


def build_html_report(report: AnalysisReport, words: list[TranscriptionWord], data_uris: list[str]) -> str:
    """
    现代 SaaS 风格单页 HTML：卡片、阴影、清晰排版；用户文案一律 escape 防 XSS。
    data_uris 与 report.risk_points 顺序一一对应。
    """
    score = report.total_score
    sa = report.scene_analysis
    cards_html = []
    for idx, (rp, data_uri) in enumerate(zip(report.risk_points, data_uris), start=1):
        level_class = {"严重": "badge-severe", "一般": "badge-medium", "轻微": "badge-mild"}.get(
            rp.risk_level, "badge-medium"
        )
        t0, t1 = word_index_to_time_range(words, rp.start_word_index, rp.end_word_index)
        cards_html.append(
            f"""
            <article class="card">
                <div class="card-head">
                    <span class="card-index">#{idx}</span>
                    <span class="badge {level_class}">{escape(rp.risk_level)}</span>
                    <span class="time-range">{t0:.1f}s — {t1:.1f}s（词索引 {rp.start_word_index}–{rp.end_word_index}）</span>
                </div>
                <h3 class="card-title">Tier 1 · 顶尖VC视角</h3>
                <p class="card-body">{escape(rp.tier1_general_critique)}</p>
                <h3 class="card-title subtle">Tier 2 · QA 对齐</h3>
                <p class="card-body">{escape(rp.tier2_qa_alignment)}</p>
                <h3 class="card-title subtle">改进建议</h3>
                <p class="card-body suggestion">{escape(rp.improvement_suggestion)}</p>
                <div class="player-wrap">
                    <span class="player-label">翻车片段试听</span>
                    <audio controls preload="metadata" src="{data_uri}">您的浏览器不支持 audio 标签。</audio>
                </div>
            </article>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AI 路演教练 · 阶段2 测试报告</title>
    <style>
        :root {{
            --bg: #0f1419;
            --surface: #1a2332;
            --card: #232f3e;
            --text: #e8eef4;
            --muted: #8b9cb3;
            --accent: #3b82f6;
            --accent-soft: rgba(59, 130, 246, 0.15);
            --severe: #f87171;
            --severe-bg: rgba(248, 113, 113, 0.12);
            --medium: #fbbf24;
            --medium-bg: rgba(251, 191, 36, 0.12);
            --mild: #34d399;
            --mild-bg: rgba(52, 211, 153, 0.12);
            --shadow: 0 20px 50px rgba(0, 0, 0, 0.45);
            --radius: 16px;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            min-height: 100vh;
            font-family: "Segoe UI", system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
            background: radial-gradient(1200px 600px at 10% -10%, rgba(59, 130, 246, 0.18), transparent),
                        radial-gradient(900px 500px at 100% 0%, rgba(52, 211, 153, 0.1), transparent),
                        var(--bg);
            color: var(--text);
            line-height: 1.65;
        }}
        .wrap {{
            max-width: 880px;
            margin: 0 auto;
            padding: 48px 24px 64px;
        }}
        header.hero {{
            background: linear-gradient(135deg, var(--surface) 0%, var(--card) 100%);
            border-radius: var(--radius);
            padding: 32px 36px;
            box-shadow: var(--shadow);
            border: 1px solid rgba(255, 255, 255, 0.06);
            margin-bottom: 32px;
        }}
        .hero-eyebrow {{
            font-size: 0.75rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: var(--muted);
            margin-bottom: 8px;
        }}
        .hero h1 {{
            margin: 0 0 12px;
            font-size: 1.75rem;
            font-weight: 700;
            letter-spacing: -0.02em;
        }}
        .hero p {{
            margin: 0;
            color: var(--muted);
            font-size: 0.95rem;
        }}
        .score-row {{
            display: flex;
            align-items: center;
            gap: 20px;
            margin-top: 24px;
            flex-wrap: wrap;
        }}
        .score-ring {{
            width: 100px;
            height: 100px;
            border-radius: 50%;
            background: conic-gradient(var(--accent) calc({score} * 3.6deg), var(--card) 0);
            display: grid;
            place-items: center;
            box-shadow: inset 0 0 0 6px var(--surface);
        }}
        .score-inner {{
            width: 76px;
            height: 76px;
            border-radius: 50%;
            background: var(--surface);
            display: grid;
            place-items: center;
            font-size: 1.5rem;
            font-weight: 800;
            color: var(--text);
        }}
        .score-meta {{
            flex: 1;
            min-width: 200px;
        }}
        .score-meta strong {{
            color: var(--accent);
        }}
        .cards {{
            display: flex;
            flex-direction: column;
            gap: 20px;
        }}
        .card {{
            background: var(--card);
            border-radius: var(--radius);
            padding: 24px 28px;
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);
            border: 1px solid rgba(255, 255, 255, 0.05);
        }}
        .card-head {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 10px;
            margin-bottom: 16px;
        }}
        .card-index {{
            font-weight: 700;
            color: var(--muted);
            font-size: 0.9rem;
        }}
        .badge {{
            padding: 4px 12px;
            border-radius: 999px;
            font-size: 0.8rem;
            font-weight: 600;
        }}
        .badge-severe {{ background: var(--severe-bg); color: var(--severe); }}
        .badge-medium {{ background: var(--medium-bg); color: var(--medium); }}
        .badge-mild {{ background: var(--mild-bg); color: var(--mild); }}
        .time-range {{
            font-size: 0.8rem;
            color: var(--muted);
            margin-left: auto;
        }}
        .card-title {{
            margin: 0 0 8px;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}
        .card-title.subtle {{ margin-top: 16px; color: var(--accent); }}
        .card-body {{
            margin: 0;
            font-size: 0.95rem;
            color: var(--text);
        }}
        .card-body.suggestion {{
            background: var(--accent-soft);
            padding: 14px 16px;
            border-radius: 12px;
            border-left: 3px solid var(--accent);
        }}
        .player-wrap {{
            margin-top: 20px;
            padding-top: 16px;
            border-top: 1px solid rgba(255, 255, 255, 0.08);
        }}
        .player-label {{
            display: block;
            font-size: 0.8rem;
            color: var(--muted);
            margin-bottom: 8px;
        }}
        audio {{
            width: 100%;
            height: 40px;
            border-radius: 8px;
        }}
        footer {{
            text-align: center;
            margin-top: 40px;
            font-size: 0.8rem;
            color: var(--muted);
        }}
    </style>
</head>
<body>
    <div class="wrap">
        <header class="hero">
            <div class="hero-eyebrow">Phase 2 · 物理链路自测</div>
            <h1>路演复盘报告（离线测试页）</h1>
            <p>本页由 <code>run_phase2.py</code> 生成，音频为词级索引切割后的 Base64 内嵌 WAV，可完全离线打开。</p>
            <p style="margin-top:12px;color:var(--text);font-size:0.95rem;"><strong>场景推断：</strong>{escape(sa.scene_type)}<br/>
            <strong>角色与氛围：</strong>{escape(sa.speaker_roles)}</p>
            <div class="score-row">
                <div class="score-ring" aria-hidden="true">
                    <div class="score-inner">{score}</div>
                </div>
                <div class="score-meta">
                    <div><strong>综合得分</strong> {score} / 100</div>
                    <div style="margin-top:6px;color:var(--muted);font-size:0.9rem;">共 {len(report.risk_points)} 个踩坑片段，均可点击下方播放器试听。</div>
                </div>
            </div>
        </header>
        <section class="cards">
            {"".join(cards_html)}
        </section>
        <footer>AI 路演教练与复盘系统 · 阶段2 无脑物理切割机</footer>
    </div>
</body>
</html>
"""


def main() -> None:
    print("[1/5] 生成 tests/dummy.wav（60s 正弦测试音）…")
    generate_dummy_wav(DUMMY_WAV)

    print("[2/5] 构造 60 个 TranscriptionWord 伪造转写…")
    words = build_transcription_mock()

    print("[3/5] 读取 tests/dummy_data.json -> AnalysisReport…")
    try:
        report = load_analysis_report(DUMMY_JSON)
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        raise SystemExit(f"解析打分 JSON 失败: {e}") from e

    print("[4/5] 按 risk_points 词级索引切割 WAV 并转 Base64…")
    data_uris: list[str] = []
    for rp in report.risk_points:
        t0, t1 = word_index_to_time_range(words, rp.start_word_index, rp.end_word_index)
        print(f"      片段: 词 {rp.start_word_index}–{rp.end_word_index} -> [{t0}, {t1}] 秒")
        data_uris.append(wav_segment_to_data_uri(DUMMY_WAV, t0, t1))

    html = build_html_report(report, words, data_uris)

    print("[5/5] 写入 output/test_report.html …")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_HTML.write_text(html, encoding="utf-8")

    print(f"完成。请用浏览器打开: {REPORT_HTML}")


if __name__ == "__main__":
    main()
