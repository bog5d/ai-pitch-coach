"""
融资结果字段测试 — V10.3 P1.2
运行：pytest tests/test_fundraising_outcome.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import analytics_exporter as ae
from schema import AnalysisReport, SceneAnalysis


def _dummy_report(score: int = 80) -> AnalysisReport:
    return AnalysisReport(
        scene_analysis=SceneAnalysis(
            scene_type="VC路演",
            speaker_roles="创始人 vs 投资人",
        ),
        total_score=score,
        total_score_deduction_reason="测试",
        risk_points=[],
    )


def _make_ctx(tmp_path: Path, stem: str = "test_session", **extra) -> dict:
    analysis_json = tmp_path / f"{stem}.json"
    analysis_json.write_text("{}", encoding="utf-8")
    ctx = {
        "analysis_json": str(analysis_json),
        "company_id": "company_001",
        "interviewee": "张总",
        "biz_type": "",
    }
    ctx.update(extra)
    return ctx


# ── 新字段写入 ────────────────────────────────────────────────────────────────

def test_fundraising_outcome_written(tmp_path):
    """ctx 含 fundraising_outcome 时，字段正确写入 analytics JSON。"""
    ctx = _make_ctx(tmp_path, fundraising_outcome="已成功")
    result = ae.export_analytics(_dummy_report(), ctx, status="locked")
    assert result is not None
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["fundraising_outcome"] == "已成功"


def test_fundraising_amount_and_valuation_written(tmp_path):
    """融资金额和估值字段正确写入。"""
    ctx = _make_ctx(
        tmp_path,
        fundraising_outcome="已成功",
        fundraising_amount="5000",    # 万元，字符串存储
        fundraising_valuation="80000",
    )
    result = ae.export_analytics(_dummy_report(), ctx, status="locked")
    assert result is not None
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["fundraising_amount"] == "5000"
    assert data["fundraising_valuation"] == "80000"


def test_fundraising_fields_default_empty(tmp_path):
    """ctx 未提供融资字段时，默认为空字符串（向后兼容）。"""
    ctx = _make_ctx(tmp_path)
    result = ae.export_analytics(_dummy_report(), ctx, status="locked")
    assert result is not None
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data.get("fundraising_outcome", "") == ""
    assert data.get("fundraising_amount", "") == ""
    assert data.get("fundraising_valuation", "") == ""


def test_fundraising_outcome_ongoing(tmp_path):
    """「进行中」状态正确写入。"""
    ctx = _make_ctx(tmp_path, fundraising_outcome="进行中")
    result = ae.export_analytics(_dummy_report(), ctx, status="locked")
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["fundraising_outcome"] == "进行中"


def test_fundraising_outcome_not_proceeded(tmp_path):
    """「未推进」状态正确写入。"""
    ctx = _make_ctx(tmp_path, fundraising_outcome="未推进")
    result = ae.export_analytics(_dummy_report(), ctx, status="locked")
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["fundraising_outcome"] == "未推进"


def test_fundraising_fields_preserved_on_draft(tmp_path):
    """draft 状态也应写入融资字段（保持一致性）。"""
    ctx = _make_ctx(tmp_path, stem="draft_session", fundraising_outcome="进行中")
    result = ae.export_analytics(_dummy_report(), ctx, status="draft")
    assert result is not None
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["fundraising_outcome"] == "进行中"


def test_fundraising_fields_not_in_existing_analytics_backward_compat(tmp_path):
    """
    旧文件（无融资字段）通过 analytics_exporter 读取后，
    向后兼容：访问 .get() 不报错。
    """
    # 模拟旧格式 analytics JSON（没有融资字段）
    old_data = {
        "session_id": "old-session",
        "status": "locked",
        "total_score": 75,
    }
    ctx = _make_ctx(tmp_path, stem="old_format")
    analysis_json = Path(ctx["analysis_json"])
    analytics_path = analysis_json.parent / (analysis_json.stem + "_analytics.json")
    analytics_path.write_text(json.dumps(old_data), encoding="utf-8")

    # 向后兼容访问不报错
    data = json.loads(analytics_path.read_text(encoding="utf-8"))
    assert data.get("fundraising_outcome", "") == ""
    assert data.get("fundraising_amount", "") == ""
