"""
Fix 4 — 空壳 RiskPoint 过滤器测试。
所有测试 zero API cost：无任何真实 LLM / ASR 调用。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from schema import RiskPoint, AnalysisReport, SceneAnalysis
from llm_judge import _is_valid_risk_point, salvage_truncated_analysis_report


def _make_rp(tier1: str = "营收预测与财务口径存在分歧", improvement: str = "建议下次准备口径对齐表") -> RiskPoint:
    return RiskPoint(
        risk_level="一般",
        tier1_general_critique=tier1,
        tier2_qa_alignment="未提供内部QA",
        improvement_suggestion=improvement,
        start_word_index=0,
        end_word_index=5,
        score_deduction=5,
    )


def test_empty_tier1_is_invalid():
    rp = _make_rp(tier1="")
    assert _is_valid_risk_point(rp) is False


def test_whitespace_only_tier1_is_invalid():
    rp = _make_rp(tier1="   ")
    assert _is_valid_risk_point(rp) is False


def test_empty_improvement_is_invalid():
    rp = _make_rp(improvement="")
    assert _is_valid_risk_point(rp) is False


def test_valid_risk_point_passes():
    rp = _make_rp()
    assert _is_valid_risk_point(rp) is True


def test_all_empty_risks_gives_none():
    """全部空壳时 salvage 应返回 None（无可用风险点）。"""
    raw = (
        '[{"risk_level":"一般","tier1_general_critique":"","tier2_qa_alignment":"x",'
        '"improvement_suggestion":"","start_word_index":0,"end_word_index":1,"score_deduction":5}]'
    )
    result = salvage_truncated_analysis_report(raw)
    assert result is None


def test_valid_risks_preserved_by_salvage():
    """有效条目在 salvage 中不被过滤（使用 LLM 完整输出格式，含 risk_points 键）。"""
    import json
    payload = {
        "scene_analysis": {"scene_type": "路演", "speaker_roles": "创始人 vs 投资人"},
        "total_score": 90,
        "total_score_deduction_reason": "轻微偏差",
        "risk_points": [
            {
                "risk_level": "严重",
                "tier1_general_critique": "营收预测与口径分歧",
                "tier2_qa_alignment": "x",
                "improvement_suggestion": "建议准备对齐表",
                "start_word_index": 0,
                "end_word_index": 5,
                "score_deduction": 10,
                "deduction_reason": "",
                "is_manual_entry": False,
                "needs_refinement": False,
                "refinement_note": "",
            }
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False)
    result = salvage_truncated_analysis_report(raw)
    assert result is not None
    assert len(result.risk_points) == 1
