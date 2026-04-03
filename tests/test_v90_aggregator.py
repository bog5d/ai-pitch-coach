"""
V9.0 机构画像聚合：get_company_dashboard_stats（仅 company_id 域，无数据安全）。

运行：pytest tests/test_v90_aggregator.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from memory_engine import get_company_dashboard_stats, save_executive_memories  # noqa: E402
from schema import ExecutiveMemory  # noqa: E402


def test_empty_company_returns_zero_structure(tmp_path):
    s = get_company_dashboard_stats("", store_dir=tmp_path)
    assert s["total_memories"] == 0
    assert s["active_executives"] == 0
    assert s["risk_distribution"] == {}
    assert s["executive_hit_trends"]["by_executive"] == []
    assert s["executive_hit_trends"]["daily_activity"] == []
    assert s["total_hit_count"] == 0
    assert s["last_updated_at"] == ""


def test_new_placeholder_company_empty(tmp_path):
    s = get_company_dashboard_stats("__new__", store_dir=tmp_path)
    assert s["total_memories"] == 0


def test_aggregation_two_executives_isolated_by_company(tmp_path):
    """A/B 公司目录隔离：只统计传入的 company_id。"""
    a_items = [
        ExecutiveMemory(
            tag="张总",
            raw_text="a1",
            correction="c",
            weight=1.0,
            risk_type="严重",
            hit_count=2,
            updated_at="2026-04-01T10:00:00Z",
        ),
        ExecutiveMemory(
            tag="张总",
            raw_text="a2",
            correction="c",
            weight=1.0,
            risk_type="一般",
            hit_count=1,
            updated_at="2026-04-02T12:00:00Z",
        ),
    ]
    b_items = [
        ExecutiveMemory(
            tag="李总",
            raw_text="b1",
            correction="c",
            weight=1.0,
            risk_type="轻微",
            hit_count=5,
            updated_at="2026-04-03T08:00:00Z",
        ),
    ]
    save_executive_memories("co_a", "张总", a_items, store_dir=tmp_path)
    save_executive_memories("co_b", "李总", b_items, store_dir=tmp_path)

    sa = get_company_dashboard_stats("co_a", store_dir=tmp_path)
    assert sa["total_memories"] == 2
    assert sa["active_executives"] == 1
    assert sa["risk_distribution"] == {"严重": 1, "一般": 1}
    assert sa["total_hit_count"] == 3
    assert sa["last_updated_at"] == "2026-04-02T12:00:00Z"
    be = {row["tag"]: row for row in sa["executive_hit_trends"]["by_executive"]}
    assert be["张总"]["total_hits"] == 3
    assert be["张总"]["memory_count"] == 2
    dates = {d["date"]: d["count"] for d in sa["executive_hit_trends"]["daily_activity"]}
    assert dates["2026-04-01"] == 1
    assert dates["2026-04-02"] == 1

    sb = get_company_dashboard_stats("co_b", store_dir=tmp_path)
    assert sb["total_memories"] == 1
    assert sb["active_executives"] == 1
    assert sb["risk_distribution"]["轻微"] == 1
    assert sb["total_hit_count"] == 5


def test_unknown_company_empty_dir(tmp_path):
    s = get_company_dashboard_stats("no_such_co", store_dir=tmp_path)
    assert s["total_memories"] == 0
