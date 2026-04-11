"""
V10.1 个人成长引擎测试。

覆盖三大核心功能：
1. build_growth_curve  — 个人历次得分成长曲线
2. build_weakness_radar — 弱点维度雷达图数据
3. get_practice_recommendations — 今天要重点练什么

所有测试零 API 成本，无外部依赖。
运行：pytest tests/test_v101_growth_engine.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ── 测试数据构造器 ─────────────────────────────────────────────────────────────

def _make_session(
    interviewee: str = "李志新",
    company_id: str = "迪策资本",
    total_score: int = 72,
    risk_breakdown: dict | None = None,
    risk_type_counts: dict | None = None,
    refinement_count: int = 0,
    ai_miss_count: int = 0,
    generated_at: str = "2026-01-01T10:00:00Z",
    stage1_truncated: bool = False,
) -> dict:
    if risk_breakdown is None:
        risk_breakdown = {
            "严重": {"count": 1, "total_deduction": 15},
            "一般": {"count": 2, "total_deduction": 10},
            "轻微": {"count": 1, "total_deduction": 4},
        }
    if risk_type_counts is None:
        risk_type_counts = {"估值回避": 1, "数据含糊": 2, "逻辑断裂": 1}
    return {
        "session_id": "test-uuid",
        "generated_at": generated_at,
        "version": "V10.1",
        "company_id": company_id,
        "interviewee": interviewee,
        "biz_type": "01_机构路演",
        "total_score": total_score,
        "total_risk_count": sum(v["count"] for v in risk_breakdown.values()),
        "risk_breakdown": risk_breakdown,
        "risk_type_counts": risk_type_counts,
        "refinement_count": refinement_count,
        "ai_miss_count": ai_miss_count,
        "stage1_truncated": stage1_truncated,
    }


def _write_analytics(directory: Path, stem: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{stem}_analytics.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# ════════════════════════════════════════════════════════
# TestGetPersonSessions — 按人筛选会话
# ════════════════════════════════════════════════════════

class TestGetPersonSessions:
    def test_returns_only_matching_person(self, tmp_path):
        from growth_engine import get_person_sessions

        _write_analytics(tmp_path, "s1", _make_session(interviewee="李志新", company_id="迪策"))
        _write_analytics(tmp_path, "s2", _make_session(interviewee="张三", company_id="迪策"))
        _write_analytics(tmp_path, "s3", _make_session(interviewee="李志新", company_id="迪策"))

        results = get_person_sessions(tmp_path, "迪策", "李志新")
        assert len(results) == 2
        assert all(r["interviewee"] == "李志新" for r in results)

    def test_sessions_sorted_by_date_asc(self, tmp_path):
        from growth_engine import get_person_sessions

        _write_analytics(tmp_path, "s1", _make_session(generated_at="2026-03-01T10:00:00Z"))
        _write_analytics(tmp_path, "s2", _make_session(generated_at="2026-01-01T10:00:00Z"))
        _write_analytics(tmp_path, "s3", _make_session(generated_at="2026-02-01T10:00:00Z"))

        results = get_person_sessions(tmp_path, "迪策资本", "李志新")
        dates = [r["generated_at"] for r in results]
        assert dates == sorted(dates)

    def test_empty_if_no_match(self, tmp_path):
        from growth_engine import get_person_sessions

        _write_analytics(tmp_path, "s1", _make_session(company_id="其他公司"))
        assert get_person_sessions(tmp_path, "迪策资本", "李志新") == []

    def test_nonexistent_workspace(self, tmp_path):
        from growth_engine import get_person_sessions

        assert get_person_sessions(tmp_path / "ghost", "迪策资本", "李志新") == []


# ════════════════════════════════════════════════════════
# TestBuildGrowthCurve — 成长曲线
# ════════════════════════════════════════════════════════

class TestBuildGrowthCurve:
    def test_basic_curve_fields(self):
        from growth_engine import build_growth_curve

        sessions = [
            _make_session(total_score=65, generated_at="2026-01-01T10:00:00Z"),
            _make_session(total_score=72, generated_at="2026-02-01T10:00:00Z"),
            _make_session(total_score=81, generated_at="2026-03-01T10:00:00Z"),
        ]
        curve = build_growth_curve(sessions)
        assert "dates" in curve
        assert "scores" in curve
        assert "trend" in curve  # 上升/下降/平稳

    def test_scores_correct_order(self):
        from growth_engine import build_growth_curve

        sessions = [
            _make_session(total_score=65, generated_at="2026-01-01T10:00:00Z"),
            _make_session(total_score=72, generated_at="2026-02-01T10:00:00Z"),
            _make_session(total_score=81, generated_at="2026-03-01T10:00:00Z"),
        ]
        curve = build_growth_curve(sessions)
        assert curve["scores"] == [65, 72, 81]

    def test_trend_rising(self):
        from growth_engine import build_growth_curve

        sessions = [_make_session(total_score=s, generated_at=f"2026-0{i+1}-01T10:00:00Z")
                    for i, s in enumerate([60, 70, 80])]
        assert build_growth_curve(sessions)["trend"] == "上升"

    def test_trend_falling(self):
        from growth_engine import build_growth_curve

        sessions = [_make_session(total_score=s, generated_at=f"2026-0{i+1}-01T10:00:00Z")
                    for i, s in enumerate([80, 70, 60])]
        assert build_growth_curve(sessions)["trend"] == "下降"

    def test_trend_stable(self):
        from growth_engine import build_growth_curve

        sessions = [_make_session(total_score=72, generated_at=f"2026-0{i+1}-01T10:00:00Z")
                    for i in range(3)]
        assert build_growth_curve(sessions)["trend"] == "平稳"

    def test_single_session(self):
        from growth_engine import build_growth_curve

        curve = build_growth_curve([_make_session(total_score=75)])
        assert curve["scores"] == [75]
        assert curve["trend"] == "首次"

    def test_empty_sessions(self):
        from growth_engine import build_growth_curve

        curve = build_growth_curve([])
        assert curve["scores"] == []
        assert curve["trend"] == "暂无数据"

    def test_score_delta_calculation(self):
        from growth_engine import build_growth_curve

        sessions = [
            _make_session(total_score=65, generated_at="2026-01-01T10:00:00Z"),
            _make_session(total_score=80, generated_at="2026-02-01T10:00:00Z"),
        ]
        curve = build_growth_curve(sessions)
        assert curve["score_delta"] == 15  # 最新 - 最早

    def test_avg_risk_per_session(self):
        """平均每场严重风险点数量。"""
        from growth_engine import build_growth_curve

        rb_heavy = {"严重": {"count": 3, "total_deduction": 30},
                    "一般": {"count": 1, "total_deduction": 5},
                    "轻微": {"count": 0, "total_deduction": 0}}
        rb_light = {"严重": {"count": 1, "total_deduction": 10},
                    "一般": {"count": 1, "total_deduction": 5},
                    "轻微": {"count": 0, "total_deduction": 0}}
        sessions = [
            _make_session(risk_breakdown=rb_heavy, generated_at="2026-01-01T10:00:00Z"),
            _make_session(risk_breakdown=rb_light, generated_at="2026-02-01T10:00:00Z"),
        ]
        curve = build_growth_curve(sessions)
        assert curve["avg_severe_per_session"] == pytest.approx(2.0)


# ════════════════════════════════════════════════════════
# TestBuildWeaknessRadar — 弱点雷达图
# ════════════════════════════════════════════════════════

class TestBuildWeaknessRadar:
    def _benchmark(self) -> dict:
        return {
            "avg_score": 73.0,
            "risk_type_frequency": {"估值回避": 10, "数据含糊": 8, "逻辑断裂": 5, "口径偏离": 3},
            "total_sessions": 20,
            "refinement_rate": 0.3,
        }

    def test_required_fields(self):
        from growth_engine import build_weakness_radar

        sessions = [_make_session(total_score=72)]
        radar = build_weakness_radar(sessions, self._benchmark())
        assert "dimensions" in radar
        assert "person_values" in radar
        assert "benchmark_values" in radar
        assert len(radar["dimensions"]) == len(radar["person_values"])
        assert len(radar["dimensions"]) == len(radar["benchmark_values"])

    def test_dimensions_include_key_metrics(self):
        """雷达图必须包含得分、严重率、精炼率等核心维度。"""
        from growth_engine import build_weakness_radar

        sessions = [_make_session()]
        radar = build_weakness_radar(sessions, self._benchmark())
        dim_names = radar["dimensions"]
        assert "综合得分" in dim_names
        assert "严重风险率" in dim_names
        assert "AI纠错率" in dim_names  # refinement_count / total_risk

    def test_empty_sessions_returns_zeros(self):
        from growth_engine import build_weakness_radar

        radar = build_weakness_radar([], self._benchmark())
        assert all(v == 0 for v in radar["person_values"])

    def test_top_weakness_risk_types(self):
        """返回该人最高频的风险类型（相对基准的差距最大的）。"""
        from growth_engine import build_weakness_radar

        sessions = [
            _make_session(risk_type_counts={"估值回避": 5, "数据含糊": 1}),
            _make_session(risk_type_counts={"估值回避": 4, "数据含糊": 1}),
        ]
        radar = build_weakness_radar(sessions, self._benchmark())
        assert "top_weakness_types" in radar
        assert "估值回避" in radar["top_weakness_types"]


# ════════════════════════════════════════════════════════
# TestGetPracticeRecommendations — 今天练什么
# ════════════════════════════════════════════════════════

class TestGetPracticeRecommendations:
    def test_returns_list_of_recommendations(self):
        from growth_engine import get_practice_recommendations

        sessions = [
            _make_session(risk_type_counts={"估值回避": 3, "数据含糊": 2, "逻辑断裂": 1}),
            _make_session(risk_type_counts={"估值回避": 2, "口径偏离": 2, "逻辑断裂": 2}),
        ]
        recs = get_practice_recommendations(sessions)
        assert isinstance(recs, list)
        assert len(recs) > 0

    def test_top3_at_most(self):
        from growth_engine import get_practice_recommendations

        sessions = [_make_session(risk_type_counts={"A": 5, "B": 4, "C": 3, "D": 2, "E": 1})]
        recs = get_practice_recommendations(sessions, top_n=3)
        assert len(recs) <= 3

    def test_recommendations_sorted_by_frequency(self):
        """优先推荐出现频率最高的问题类型。"""
        from growth_engine import get_practice_recommendations

        sessions = [_make_session(risk_type_counts={"数据含糊": 1, "估值回避": 5, "逻辑断裂": 2})]
        recs = get_practice_recommendations(sessions, top_n=3)
        assert recs[0]["risk_type"] == "估值回避"  # 频率最高优先

    def test_recommendation_has_required_fields(self):
        from growth_engine import get_practice_recommendations

        sessions = [_make_session(risk_type_counts={"估值回避": 3})]
        recs = get_practice_recommendations(sessions)
        rec = recs[0]
        assert "risk_type" in rec
        assert "count" in rec
        assert "suggestion" in rec  # 配套练习建议文案

    def test_empty_sessions_returns_empty(self):
        from growth_engine import get_practice_recommendations

        assert get_practice_recommendations([]) == []

    def test_no_risk_types_returns_empty(self):
        from growth_engine import get_practice_recommendations

        sessions = [_make_session(risk_type_counts={})]
        recs = get_practice_recommendations(sessions)
        assert recs == []

    def test_recent_sessions_weighted_more(self):
        """近期问题权重更高（近3次比历史早期更重要）。"""
        from growth_engine import get_practice_recommendations

        # 早期出现很多"数据含糊"，最近3次都是"估值回避"
        early = [_make_session(
            risk_type_counts={"数据含糊": 5},
            generated_at=f"2025-0{i+1}-01T10:00:00Z"
        ) for i in range(4)]
        recent = [_make_session(
            risk_type_counts={"估值回避": 2},
            generated_at=f"2026-0{i+1}-01T10:00:00Z"
        ) for i in range(3)]
        all_sessions = sorted(early + recent, key=lambda x: x["generated_at"])
        recs = get_practice_recommendations(all_sessions, top_n=1)
        # 近期加权后"估值回避"应排第一
        assert recs[0]["risk_type"] == "估值回避"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
