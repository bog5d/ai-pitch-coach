"""
V10.0 飞轮速度看板测试 — get_company_dashboard_stats flywheel_metrics 字段。

验证：stats 返回值中新增 flywheel_metrics 子键，包含：
- hit_rate: 被命中过的记忆比例（hit_count > 0 的比例）
- top_memories: 按 hit_count 降序的 TOP-10 记忆列表（含 tag、raw_text_snippet、hit_count）
- monthly_new: 本月新增记忆数（updated_at 月份与当前月匹配）
- weight_distribution: 高/中/低权重分布（>1.5 为高，0.5-1.5 为中，<0.5 为低）

运行：pytest tests/test_v100_flywheel_metrics.py -v
所有测试 zero API cost，无外部依赖。
"""
from __future__ import annotations

import sys
import uuid as uuid_mod
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ── 辅助 ─────────────────────────────────────────────────────────────────────

def _now_month() -> str:
    """当前年月，格式 YYYY-MM。"""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _make_mem(
    tag: str = "李志新",
    raw_text: str = "营收口径前后不一致",
    hit_count: int = 0,
    weight: float = 1.0,
    updated_at: str | None = None,
    risk_type: str = "数据矛盾",
):
    from schema import ExecutiveMemory

    if updated_at is None:
        updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return ExecutiveMemory(
        uuid=str(uuid_mod.uuid4()),
        tag=tag,
        raw_text=raw_text,
        correction="标准口径",
        weight=weight,
        risk_type=risk_type,
        updated_at=updated_at,
        hit_count=hit_count,
    )


def _make_pairs(mems) -> list:
    """将 ExecutiveMemory 列表包装为 (stem_tag, mem) 对。"""
    return [(m.tag, m) for m in mems]


# ════════════════════════════════════════════════════════
# TestFlywheelMetricsPresence — flywheel_metrics 字段存在
# ════════════════════════════════════════════════════════

class TestFlywheelMetricsPresence:
    """get_company_dashboard_stats 返回值必须包含 flywheel_metrics 字段。"""

    def test_flywheel_metrics_key_exists(self, tmp_path):
        """返回值中有 flywheel_metrics 键。"""
        from memory_engine import get_company_dashboard_stats

        mems = [_make_mem("李志新", hit_count=3)]
        stats = get_company_dashboard_stats("测试机构", pre_loaded_pairs=_make_pairs(mems))
        assert "flywheel_metrics" in stats, "返回值缺少 flywheel_metrics 字段"

    def test_flywheel_metrics_subkeys(self, tmp_path):
        """flywheel_metrics 包含所有必要子键。"""
        from memory_engine import get_company_dashboard_stats

        mems = [_make_mem("李志新", hit_count=1)]
        stats = get_company_dashboard_stats("测试机构", pre_loaded_pairs=_make_pairs(mems))
        fm = stats["flywheel_metrics"]
        required = {"hit_rate", "top_memories", "monthly_new", "weight_distribution"}
        missing = required - set(fm.keys())
        assert not missing, f"flywheel_metrics 缺少子键：{missing}"

    def test_empty_pairs_flywheel_metrics_zero(self):
        """无记忆时，flywheel_metrics 所有指标为零/空。"""
        from memory_engine import get_company_dashboard_stats

        stats = get_company_dashboard_stats("空机构", pre_loaded_pairs=[])
        # 空机构直接返回 empty，不会有 flywheel_metrics
        # 但应有 flywheel_metrics 字段（零值）
        # 根据实现，empty 结构中也应加入 flywheel_metrics
        assert "flywheel_metrics" in stats


# ════════════════════════════════════════════════════════
# TestHitRate — 命中率计算
# ════════════════════════════════════════════════════════

class TestHitRate:
    """hit_rate：有命中记录的记忆占总记忆的比例。"""

    def test_all_hit(self):
        """全部记忆都被命中过，hit_rate = 1.0。"""
        from memory_engine import get_company_dashboard_stats

        mems = [_make_mem("A", hit_count=3), _make_mem("B", hit_count=1)]
        stats = get_company_dashboard_stats("测试", pre_loaded_pairs=_make_pairs(mems))
        assert stats["flywheel_metrics"]["hit_rate"] == pytest.approx(1.0)

    def test_none_hit(self):
        """没有记忆被命中过，hit_rate = 0.0。"""
        from memory_engine import get_company_dashboard_stats

        mems = [_make_mem("A", hit_count=0), _make_mem("B", hit_count=0)]
        stats = get_company_dashboard_stats("测试", pre_loaded_pairs=_make_pairs(mems))
        assert stats["flywheel_metrics"]["hit_rate"] == pytest.approx(0.0)

    def test_partial_hit(self):
        """3 条记忆中 2 条被命中，hit_rate ≈ 0.667。"""
        from memory_engine import get_company_dashboard_stats

        mems = [
            _make_mem("A", hit_count=5),
            _make_mem("B", hit_count=2),
            _make_mem("C", hit_count=0),
        ]
        stats = get_company_dashboard_stats("测试", pre_loaded_pairs=_make_pairs(mems))
        assert stats["flywheel_metrics"]["hit_rate"] == pytest.approx(2 / 3, rel=1e-3)

    def test_empty_memories_hit_rate_zero(self):
        """无记忆时 hit_rate = 0.0（不除以零）。"""
        from memory_engine import get_company_dashboard_stats

        stats = get_company_dashboard_stats("空机构", pre_loaded_pairs=[])
        assert stats["flywheel_metrics"]["hit_rate"] == pytest.approx(0.0)


# ════════════════════════════════════════════════════════
# TestTopMemories — TOP 贡献记忆
# ════════════════════════════════════════════════════════

class TestTopMemories:
    """top_memories：按 hit_count 降序的 TOP-10，包含 tag/raw_text_snippet/hit_count。"""

    def test_sorted_by_hit_count_descending(self):
        """top_memories 按 hit_count 降序排列。"""
        from memory_engine import get_company_dashboard_stats

        mems = [
            _make_mem("A", raw_text="记忆A", hit_count=1),
            _make_mem("B", raw_text="记忆B", hit_count=10),
            _make_mem("C", raw_text="记忆C", hit_count=5),
        ]
        stats = get_company_dashboard_stats("测试", pre_loaded_pairs=_make_pairs(mems))
        top = stats["flywheel_metrics"]["top_memories"]
        counts = [m["hit_count"] for m in top]
        assert counts == sorted(counts, reverse=True), "top_memories 应按 hit_count 降序"

    def test_top_10_limit(self):
        """超过 10 条时，只返回 TOP-10。"""
        from memory_engine import get_company_dashboard_stats

        mems = [_make_mem(f"人物{i}", raw_text=f"记忆{i}", hit_count=i) for i in range(15)]
        stats = get_company_dashboard_stats("测试", pre_loaded_pairs=_make_pairs(mems))
        assert len(stats["flywheel_metrics"]["top_memories"]) <= 10

    def test_top_memory_item_structure(self):
        """每条 top_memory 有 tag、raw_text_snippet、hit_count 字段。"""
        from memory_engine import get_company_dashboard_stats

        mems = [_make_mem("李志新", raw_text="营收口径不一致，需要对齐", hit_count=5)]
        stats = get_company_dashboard_stats("测试", pre_loaded_pairs=_make_pairs(mems))
        item = stats["flywheel_metrics"]["top_memories"][0]
        assert "tag" in item
        assert "raw_text_snippet" in item
        assert "hit_count" in item
        assert item["hit_count"] == 5

    def test_raw_text_snippet_truncated_to_40(self):
        """raw_text_snippet 不超过 40 字。"""
        from memory_engine import get_company_dashboard_stats

        long_text = "这是一段非常非常非常长的记忆文本，超过了四十个字，应该被截断并加省略号展示"
        mems = [_make_mem("A", raw_text=long_text, hit_count=1)]
        stats = get_company_dashboard_stats("测试", pre_loaded_pairs=_make_pairs(mems))
        snippet = stats["flywheel_metrics"]["top_memories"][0]["raw_text_snippet"]
        assert len(snippet) <= 43  # 40 字 + 省略号


# ════════════════════════════════════════════════════════
# TestMonthlyNew — 本月新增数量
# ════════════════════════════════════════════════════════

class TestMonthlyNew:
    """monthly_new：updated_at 在当前月份的记忆数量。"""

    def test_all_this_month(self):
        """全部记忆都是本月更新的，monthly_new = 总数。"""
        from memory_engine import get_company_dashboard_stats

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        mems = [_make_mem("A", updated_at=now), _make_mem("B", updated_at=now)]
        stats = get_company_dashboard_stats("测试", pre_loaded_pairs=_make_pairs(mems))
        assert stats["flywheel_metrics"]["monthly_new"] == 2

    def test_old_months_not_counted(self):
        """上个月的记忆不算入 monthly_new。"""
        from memory_engine import get_company_dashboard_stats

        now_str = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        old_str = "2023-01-01T00:00:00Z"
        mems = [
            _make_mem("A", updated_at=now_str),
            _make_mem("B", updated_at=old_str),
        ]
        stats = get_company_dashboard_stats("测试", pre_loaded_pairs=_make_pairs(mems))
        assert stats["flywheel_metrics"]["monthly_new"] == 1


# ════════════════════════════════════════════════════════
# TestWeightDistribution — 权重分布
# ════════════════════════════════════════════════════════

class TestWeightDistribution:
    """weight_distribution：高(>1.5) / 中(0.5~1.5] / 低(<0.5) 各几条。"""

    def test_weight_buckets(self):
        """三个权重桶正确分类。"""
        from memory_engine import get_company_dashboard_stats

        mems = [
            _make_mem("A", weight=2.0),   # 高
            _make_mem("B", weight=1.5),   # 中（临界值归中）
            _make_mem("C", weight=1.0),   # 中
            _make_mem("D", weight=0.5),   # 中（临界值归中）
            _make_mem("E", weight=0.3),   # 低
        ]
        stats = get_company_dashboard_stats("测试", pre_loaded_pairs=_make_pairs(mems))
        wd = stats["flywheel_metrics"]["weight_distribution"]
        assert wd["high"] == 1   # weight > 1.5
        assert wd["medium"] == 3  # 0.5 <= weight <= 1.5
        assert wd["low"] == 1    # weight < 0.5

    def test_weight_distribution_keys(self):
        """weight_distribution 必须包含 high/medium/low 三个键。"""
        from memory_engine import get_company_dashboard_stats

        mems = [_make_mem("A", weight=1.0)]
        stats = get_company_dashboard_stats("测试", pre_loaded_pairs=_make_pairs(mems))
        wd = stats["flywheel_metrics"]["weight_distribution"]
        assert set(wd.keys()) == {"high", "medium", "low"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
