"""
V7.2 后端 original_text 物理覆写：毒药数据与越界压测。
运行：python -m pytest tests/test_v72_backend_override.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from report_builder import (  # noqa: E402
    apply_asr_original_text_override,
    verbatim_original_text_from_word_indices,
)
from schema import AnalysisReport, RiskPoint, SceneAnalysis, TranscriptionWord  # noqa: E402


POISON_LLM_ORIGINAL = "关于竞争对手，我方认为其在市场占有率上具有显著优势。"


def _tw(i: int, text: str, speaker: str = "spk_a") -> TranscriptionWord:
    t = float(i) * 0.1
    return TranscriptionWord(
        word_index=i,
        text=text,
        start_time=t,
        end_time=t + 0.09,
        speaker_id=speaker,
    )


def _make_asr_words_10() -> list[TranscriptionWord]:
    texts = ["我", "嗯", "觉得", "这个", "啊", "竞品", "确实", "非常", "那个", "强"]
    return [_tw(i, texts[i]) for i in range(10)]


def test_poison_qa_text_is_physically_replaced_by_asr_slice() -> None:
    """大模型 original_text 为 QA 洗稿时，覆写后必须与 ASR 索引切片一致，绝不能保留毒药句。"""
    words_list = _make_asr_words_10()
    by_index = {w.word_index: w for w in words_list}
    # V7.2 场记拼接：同说话人连续词用 "" 连接，首路说话人标为 [投资人]
    expected_asr_block = "[投资人]：竞品确实非常那个强"
    assert (
        verbatim_original_text_from_word_indices(by_index, 5, 9) == expected_asr_block
    )

    report = AnalysisReport(
        scene_analysis=SceneAnalysis(scene_type="压测", speaker_roles="测试"),
        total_score=100,
        total_score_deduction_reason="",
        risk_points=[
            RiskPoint(
                risk_level="轻微",
                tier1_general_critique="x",
                tier2_qa_alignment="y",
                improvement_suggestion="z",
                original_text=POISON_LLM_ORIGINAL,
                start_word_index=5,
                end_word_index=9,
            )
        ],
    )
    fixed = apply_asr_original_text_override(report, words_list)
    rp0 = fixed.risk_points[0]
    assert POISON_LLM_ORIGINAL not in rp0.original_text
    assert "关于竞争对手" not in rp0.original_text
    assert rp0.original_text == expected_asr_block
    # 与空格版 format_transcript_snippet 语义等价：同一段词，无 QA 渗入
    assert "竞品" in rp0.original_text and "强" in rp0.original_text


def test_out_of_range_end_index_does_not_crash_and_truncates_to_existing_words() -> None:
    """end_word_index 远超词表时：覆写路径跳过空洞索引，不抛异常，效果等价于切到末尾。"""
    words_list = _make_asr_words_10()
    by_index = {w.word_index: w for w in words_list}
    sane_hi = 9
    crazy_hi = 999

    out_sane = verbatim_original_text_from_word_indices(by_index, 5, sane_hi)
    out_crazy = verbatim_original_text_from_word_indices(by_index, 5, crazy_hi)
    assert out_sane == out_crazy
    assert out_crazy == "[投资人]：竞品确实非常那个强"

    report = AnalysisReport(
        scene_analysis=SceneAnalysis(scene_type="压测", speaker_roles="测试"),
        total_score=100,
        total_score_deduction_reason="",
        risk_points=[
            RiskPoint(
                risk_level="轻微",
                tier1_general_critique="a",
                tier2_qa_alignment="b",
                improvement_suggestion="c",
                original_text=POISON_LLM_ORIGINAL,
                start_word_index=5,
                end_word_index=crazy_hi,
            )
        ],
    )
    fixed = apply_asr_original_text_override(report, words_list)
    assert fixed.risk_points[0].original_text == out_crazy
    assert POISON_LLM_ORIGINAL not in fixed.risk_points[0].original_text


def test_empty_range_when_all_indices_missing_returns_placeholder() -> None:
    """区间内无任何有效词时返回占位，不崩溃。"""
    words_list = [_tw(0, "仅一词")]
    by_index = {w.word_index: w for w in words_list}
    out = verbatim_original_text_from_word_indices(by_index, 50, 999)
    assert out == "（该范围内无转写词）"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
