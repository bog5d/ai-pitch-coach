"""
Partner 级投资人画像测试 — V10.3 P3.2
运行：pytest tests/test_partner_profiler.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import partner_profiler as pp


def _make_analytics(
    tmp_path: Path,
    institution_id: str,
    investor_name: str,
    company_id: str = "公司A",
    score: int = 75,
    risk_types: dict | None = None,
    idx: int = 0,
) -> Path:
    payload = {
        "session_id": f"sess-{institution_id}-{investor_name}-{idx}",
        "status": "locked",
        "company_id": company_id,
        "institution_id": institution_id,
        "institution_canonical": "迪策资本",
        "investor_name": investor_name,
        "total_score": score,
        "total_risk_count": 3,
        "risk_breakdown": {"严重": {"count": 1}, "一般": {"count": 1}, "轻微": {"count": 1}},
        "risk_type_counts": risk_types or {"估值回避": 2},
        "generated_at": f"2026-04-{10+idx:02d}T12:00:00Z",
    }
    safe = f"{institution_id}_{investor_name}_{idx}".replace(" ", "_")
    p = tmp_path / f"{safe}_analytics.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# ── 画像构建 ──────────────────────────────────────────────────────────────────

def test_build_partner_profile_returns_required_fields(tmp_path):
    """build_partner_profile 返回必须字段。"""
    _make_analytics(tmp_path, "inst-001", "李合伙人")
    profile = pp.build_partner_profile("inst-001", "李合伙人", tmp_path)
    assert profile["institution_id"] == "inst-001"
    assert profile["investor_name"] == "李合伙人"
    assert "total_sessions" in profile
    assert "avg_score" in profile
    assert "top_risk_types" in profile


def test_build_partner_profile_counts_sessions(tmp_path):
    """session 数量正确统计。"""
    for i in range(3):
        _make_analytics(tmp_path, "inst-001", "李合伙人", idx=i)
    profile = pp.build_partner_profile("inst-001", "李合伙人", tmp_path)
    assert profile["total_sessions"] == 3


def test_build_partner_profile_excludes_other_partners(tmp_path):
    """只统计指定 partner 的数据。"""
    for i in range(3):
        _make_analytics(tmp_path, "inst-001", "李合伙人", idx=i)
    for i in range(2):
        _make_analytics(tmp_path, "inst-001", "王总监", idx=i+10)
    profile = pp.build_partner_profile("inst-001", "李合伙人", tmp_path)
    assert profile["total_sessions"] == 3


def test_build_partner_profile_no_sessions(tmp_path):
    """无 session 时返回空结构，不崩溃。"""
    profile = pp.build_partner_profile("inst-999", "李合伙人", tmp_path)
    assert profile["total_sessions"] == 0
    assert profile["avg_score"] == 0.0


def test_build_partner_profile_avg_score(tmp_path):
    """avg_score 正确计算。"""
    _make_analytics(tmp_path, "inst-001", "李合伙人", score=80, idx=0)
    _make_analytics(tmp_path, "inst-001", "李合伙人", score=60, idx=1)
    profile = pp.build_partner_profile("inst-001", "李合伙人", tmp_path)
    assert profile["total_sessions"] == 2
    assert abs(profile["avg_score"] - 70.0) < 0.1


# ── 机构内所有 Partner 列表 ───────────────────────────────────────────────────

def test_list_partners_for_institution(tmp_path):
    """list_partners_for_institution 返回该机构所有出现的 partner 名字列表。"""
    _make_analytics(tmp_path, "inst-001", "李合伙人", idx=0)
    _make_analytics(tmp_path, "inst-001", "王总监", idx=1)
    _make_analytics(tmp_path, "inst-001", "李合伙人", idx=2)
    partners = pp.list_partners_for_institution("inst-001", tmp_path)
    assert "李合伙人" in partners
    assert "王总监" in partners
    assert len(partners) == 2  # 去重


def test_list_partners_excludes_other_institutions(tmp_path):
    """不混入其他机构的 partner。"""
    _make_analytics(tmp_path, "inst-001", "李合伙人", idx=0)
    _make_analytics(tmp_path, "inst-002", "赵副总", idx=1)
    partners = pp.list_partners_for_institution("inst-001", tmp_path)
    assert "赵副总" not in partners


def test_list_partners_empty_investor_name_excluded(tmp_path):
    """空 investor_name 的 session 不纳入 partner 统计。"""
    _make_analytics(tmp_path, "inst-001", "", idx=0)   # 空名字
    _make_analytics(tmp_path, "inst-001", "李合伙人", idx=1)
    partners = pp.list_partners_for_institution("inst-001", tmp_path)
    assert "" not in partners
    assert "李合伙人" in partners


# ── analytics_exporter 字段 ───────────────────────────────────────────────────

def test_analytics_exporter_writes_investor_name(tmp_path):
    """export_analytics 在 ctx 含 investor_name 时写入 analytics JSON。"""
    sys.path.insert(0, str(ROOT / "src"))
    import analytics_exporter as ae
    from schema import AnalysisReport, SceneAnalysis

    report = AnalysisReport(
        scene_analysis=SceneAnalysis(scene_type="VC路演", speaker_roles="创始人 vs 投资人"),
        total_score=80, total_score_deduction_reason="", risk_points=[],
    )
    analysis_json = tmp_path / "test.json"
    analysis_json.write_text("{}", encoding="utf-8")
    ctx = {
        "analysis_json": str(analysis_json),
        "company_id": "公司A", "interviewee": "张总", "biz_type": "",
        "investor_name": "李合伙人",
    }
    result = ae.export_analytics(report, ctx, status="locked")
    assert result is not None
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["investor_name"] == "李合伙人"


def test_analytics_exporter_investor_name_default_empty(tmp_path):
    """ctx 无 investor_name 时，默认空字符串（向后兼容）。"""
    sys.path.insert(0, str(ROOT / "src"))
    import analytics_exporter as ae
    from schema import AnalysisReport, SceneAnalysis

    report = AnalysisReport(
        scene_analysis=SceneAnalysis(scene_type="VC路演", speaker_roles="创始人 vs 投资人"),
        total_score=75, total_score_deduction_reason="", risk_points=[],
    )
    analysis_json = tmp_path / "test2.json"
    analysis_json.write_text("{}", encoding="utf-8")
    ctx = {
        "analysis_json": str(analysis_json),
        "company_id": "公司A", "interviewee": "张总", "biz_type": "",
    }
    result = ae.export_analytics(report, ctx, status="locked")
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data.get("investor_name", "") == ""
