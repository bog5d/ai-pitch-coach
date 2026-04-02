"""
V7.5：人类可读文字稿格式化（按说话人分段，禁止出现 LLM 用 [0][1] 词索引样式）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from report_builder import apply_asr_original_text_override  # noqa: E402
from schema import AnalysisReport, RiskPoint, SceneAnalysis, TranscriptionWord  # noqa: E402
from transcriber import format_transcript_plain_by_speaker  # noqa: E402


def _tw(
    idx: int,
    text: str,
    *,
    spk: str,
    t0: float = 0.0,
    t1: float = 0.1,
) -> TranscriptionWord:
    return TranscriptionWord(
        word_index=idx,
        text=text,
        start_time=t0,
        end_time=t1,
        speaker_id=spk,
    )


def test_format_transcript_plain_zh_speaker_labels_and_segments() -> None:
    words = [
        _tw(0, "你好", spk="spk_0", t0=0.0, t1=0.2),
        _tw(1, "世界", spk="spk_0", t0=0.2, t1=0.4),
        _tw(2, "收到", spk="spk_1", t0=0.5, t1=0.7),
        _tw(3, "明白", spk="spk_1", t0=0.7, t1=0.9),
    ]
    out = format_transcript_plain_by_speaker(words)
    assert out.startswith("[发言人 1]: ")
    assert "[发言人 2]: " in out
    assert "你好世界" in out.replace(" ", "")
    assert "收到明白" in out.replace(" ", "")
    parts = out.split("\n\n")
    assert len(parts) == 2
    assert parts[0].startswith("[发言人 1]:")
    assert parts[1].startswith("[发言人 2]:")


def test_format_transcript_no_numeric_llm_index_brackets() -> None:
    """输出中不得出现 [0]、[1] 这类词级索引标签（与 format_transcript_for_llm 区分）。"""
    words = [
        _tw(0, "词甲", spk="spk_0"),
        _tw(1, "词乙", spk="spk_1"),
    ]
    out = format_transcript_plain_by_speaker(words)
    for bad in ("[0]", "[1]", "[2]"):
        assert bad not in out


def test_apply_override_cleans_original_text_on_disk_shape() -> None:
    """落盘前物理覆写：LLM 带 [index] 的毒药串不得留在覆写后的 original_text。"""
    words = [
        _tw(0, "竞品", spk="spk_0"),
        _tw(1, "很强", spk="spk_0"),
    ]
    poison = "[0]竞[1]品很强"
    report = AnalysisReport(
        scene_analysis=SceneAnalysis(scene_type="t", speaker_roles="r"),
        total_score=90,
        risk_points=[
            RiskPoint(
                risk_level="轻微",
                tier1_general_critique="x",
                tier2_qa_alignment="y",
                improvement_suggestion="z",
                original_text=poison,
                start_word_index=0,
                end_word_index=1,
                score_deduction=2,
                deduction_reason="",
                is_manual_entry=False,
            )
        ],
    )
    fixed = apply_asr_original_text_override(report, words)
    ot = fixed.risk_points[0].original_text
    assert poison not in ot
    assert "竞品" in ot and "很强" in ot
    dumped = json.dumps(fixed.model_dump(), ensure_ascii=False)
    assert "[0]" not in dumped
    assert "[1]" not in dumped
