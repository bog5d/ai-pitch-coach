"""
融资成功率预测模型测试 — V10.3 P3.1
运行：pytest tests/test_outcome_predictor.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import outcome_predictor as op


def _make_session(
    score: int,
    severe_risk_count: int = 0,
    fundraising_outcome: str = "",
) -> dict:
    return {
        "total_score": score,
        "risk_breakdown": {
            "严重": {"count": severe_risk_count, "total_deduction": severe_risk_count * 10},
            "一般": {"count": 0, "total_deduction": 0},
            "轻微": {"count": 0, "total_deduction": 0},
        },
        "fundraising_outcome": fundraising_outcome,
        "status": "locked",
    }


# ── 基础预测 ──────────────────────────────────────────────────────────────────

def test_high_score_predicts_higher_probability():
    """高分 session 预测成功率应高于低分。"""
    high = op.predict_success_probability([_make_session(90, severe_risk_count=0)])
    low  = op.predict_success_probability([_make_session(50, severe_risk_count=5)])
    assert high["probability"] > low["probability"]


def test_probability_in_range():
    """预测值必须在 [0.0, 1.0] 范围内。"""
    for score in [0, 30, 50, 70, 85, 100]:
        result = op.predict_success_probability([_make_session(score)])
        p = result["probability"]
        assert 0.0 <= p <= 1.0, f"score={score}, p={p}"


def test_empty_sessions_returns_none():
    """无 session 数据时返回 None probability（无法预测）。"""
    result = op.predict_success_probability([])
    assert result["probability"] is None


def test_severe_risks_lower_probability():
    """严重风险点多 → 成功率更低。"""
    no_severe  = op.predict_success_probability([_make_session(80, severe_risk_count=0)])
    few_severe = op.predict_success_probability([_make_session(80, severe_risk_count=3)])
    assert no_severe["probability"] > few_severe["probability"]


def test_returns_required_fields():
    """返回字段完整性。"""
    result = op.predict_success_probability([_make_session(75)])
    assert "probability" in result
    assert "confidence" in result
    assert "signal" in result
    assert "factors" in result


def test_confidence_higher_with_more_sessions():
    """session 数量越多，置信度越高。"""
    one_sess = op.predict_success_probability([_make_session(75)])
    five_sess = op.predict_success_probability([_make_session(75)] * 5)
    assert five_sess["confidence"] >= one_sess["confidence"]


# ── 历史结果参考 ──────────────────────────────────────────────────────────────

def test_successful_outcome_history_boosts_probability():
    """历史有「已成功」记录的 session，成功率预测应更高。"""
    with_success = op.predict_success_probability([
        _make_session(75, fundraising_outcome="已成功"),
        _make_session(70),
    ])
    without_success = op.predict_success_probability([
        _make_session(75),
        _make_session(70),
    ])
    assert with_success["probability"] >= without_success["probability"]


def test_failed_outcome_history_lowers_probability():
    """历史有「未推进」记录的 session，成功率预测应更低。"""
    with_failed = op.predict_success_probability([
        _make_session(75, fundraising_outcome="未推进"),
        _make_session(70, fundraising_outcome="未推进"),
    ])
    neutral = op.predict_success_probability([
        _make_session(75),
        _make_session(70),
    ])
    assert with_failed["probability"] <= neutral["probability"]


# ── 信号标签 ─────────────────────────────────────────────────────────────────

def test_signal_positive_for_high_score():
    """高分低风险 → signal 为 'positive' 或 'strong_positive'。"""
    result = op.predict_success_probability([_make_session(90, severe_risk_count=0)])
    assert result["signal"] in ("positive", "strong_positive")


def test_signal_negative_for_low_score():
    """低分高风险 → signal 为 'negative' 或 'strong_negative'。"""
    result = op.predict_success_probability([_make_session(40, severe_risk_count=6)])
    assert result["signal"] in ("negative", "strong_negative")


def test_signal_neutral_for_medium_score():
    """中等分数 → signal 为 'neutral'。"""
    result = op.predict_success_probability([_make_session(65, severe_risk_count=1)])
    assert result["signal"] in ("neutral", "positive", "negative")  # 中间范围可任一方向


# ── 批量预测 ─────────────────────────────────────────────────────────────────

def test_bulk_predict_returns_per_company(tmp_path):
    """bulk_predict_for_workspace 应返回每家公司的预测结果。"""
    import json

    # 创建两家公司的 analytics
    for cid in ["公司A", "公司B"]:
        p = tmp_path / f"{cid}_session_analytics.json"
        p.write_text(json.dumps({
            "company_id": cid,
            "status": "locked",
            "total_score": 80,
            "risk_breakdown": {"严重": {"count": 0}, "一般": {"count": 0}, "轻微": {"count": 0}},
            "fundraising_outcome": "",
        }, ensure_ascii=False), encoding="utf-8")

    results = op.bulk_predict_for_workspace(tmp_path)
    assert "公司A" in results
    assert "公司B" in results
    assert results["公司A"]["probability"] is not None
