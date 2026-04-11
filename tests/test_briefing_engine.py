"""
briefing_engine 单元测试 — V10.2
只测试纯数据层 generate_briefing_data()，不调用 LLM。
运行：pytest tests/test_briefing_engine.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from briefing_engine import generate_briefing_data, generate_briefing_text, _fallback_briefing

IID_A = "inst-aaa-001"
CID_A = "company-001"


def _make_analytics(tmp_path: Path, filename: str, institution_id: str,
                    company_id: str, score: int, risk_types: dict,
                    locked_at: str = "2026-01-01T00:00:00Z") -> None:
    data = {
        "institution_id": institution_id,
        "institution_canonical": "测试机构",
        "company_id": company_id,
        "total_score": score,
        "total_risk_count": sum(risk_types.values()),
        "risk_breakdown": {"严重": {"count": 1, "total_deduction": 5}},
        "risk_type_counts": risk_types,
        "locked_at": locked_at,
        "killer_questions": [],
    }
    (tmp_path / filename).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_no_history_has_history_false(tmp_path):
    data = generate_briefing_data(IID_A, CID_A, tmp_path)
    assert data["has_history"] is False
    assert data["total_sessions"] == 0


def test_with_history_has_history_true(tmp_path):
    _make_analytics(tmp_path, "s1_analytics.json", IID_A, CID_A, 75, {"逻辑断裂": 2})
    data = generate_briefing_data(IID_A, CID_A, tmp_path)
    assert data["has_history"] is True
    assert data["total_sessions"] == 1


def test_institution_top_risks_max_3(tmp_path):
    _make_analytics(tmp_path, "s1_analytics.json", IID_A, CID_A, 70,
                    {"逻辑断裂": 5, "数据含糊": 3, "估值回避": 2, "口径偏离": 1})
    data = generate_briefing_data(IID_A, CID_A, tmp_path)
    assert len(data["institution_top_risks"]) <= 3
    assert data["institution_top_risks"][0]["risk_type"] == "逻辑断裂"


def test_company_pits_reflects_company_history(tmp_path):
    # 公司A的坑
    _make_analytics(tmp_path, "s1_analytics.json", IID_A, CID_A, 70,
                    {"估值回避": 3})
    _make_analytics(tmp_path, "s2_analytics.json", IID_A, CID_A, 75,
                    {"估值回避": 2, "数据含糊": 1})
    # 另一家公司的分析（不应混入）
    _make_analytics(tmp_path, "s3_analytics.json", IID_A, "other-company", 80,
                    {"逻辑断裂": 10})
    data = generate_briefing_data(IID_A, CID_A, tmp_path)
    pit_types = [p["risk_type"] for p in data["company_pits"]]
    assert "估值回避" in pit_types
    assert "逻辑断裂" not in pit_types


def test_canonical_name_in_data(tmp_path):
    _make_analytics(tmp_path, "s1_analytics.json", IID_A, CID_A, 80, {})
    data = generate_briefing_data(IID_A, CID_A, tmp_path)
    assert data["canonical_name"] == "测试机构"


def test_fallback_briefing_no_llm(tmp_path):
    """verify fallback text is generated without LLM."""
    _make_analytics(tmp_path, "s1_analytics.json", IID_A, CID_A, 75,
                    {"逻辑断裂": 2})
    data = generate_briefing_data(IID_A, CID_A, tmp_path)
    text = _fallback_briefing(data, "泽天智航", "迪策资本")
    assert "泽天智航" in text
    assert "迪策资本" in text
    assert "会前简报" in text


def test_generate_briefing_text_no_history_skips_llm(tmp_path):
    """无历史数据时不应调用 LLM。"""
    with patch("briefing_engine.OpenAI") as mock_openai:
        text = generate_briefing_text(IID_A, CID_A, tmp_path)
    mock_openai.assert_not_called()
    assert "暂无该机构的历史数据" in text


def test_generate_briefing_text_llm_failure_degrades(tmp_path):
    """LLM 调用失败时降级到纯数据文本，不抛异常。"""
    _make_analytics(tmp_path, "s1_analytics.json", IID_A, CID_A, 75,
                    {"逻辑断裂": 2})
    with patch("briefing_engine.OpenAI", side_effect=Exception("network error")):
        text = generate_briefing_text(
            IID_A, CID_A, tmp_path,
            company_name="泽天智航", institution_name="迪策资本"
        )
    assert "会前简报" in text
    assert "泽天智航" in text
