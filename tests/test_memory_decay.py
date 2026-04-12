"""
记忆权重衰减测试 — V10.3 P1.3
运行：pytest tests/test_memory_decay.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import memory_engine as me
from schema import ExecutiveMemory


def _iso_days_ago(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _make_memory(
    raw_text: str = "测试表述",
    correction: str = "标准口径",
    tag: str = "张总",
    weight: float = 1.0,
    updated_at: str | None = None,
    hit_count: int = 0,
) -> ExecutiveMemory:
    return ExecutiveMemory(
        tag=tag,
        raw_text=raw_text,
        correction=correction,
        weight=weight,
        updated_at=updated_at or _iso_days_ago(0),
        hit_count=hit_count,
    )


# ── 单公司衰减 ────────────────────────────────────────────────────────────────

def test_decay_old_memory_reduces_weight(tmp_path):
    """超过 90 天未 recall 的记忆应降权。"""
    mem = _make_memory(updated_at=_iso_days_ago(100), weight=1.0)
    me.save_executive_memories("公司A", "张总", [mem], store_dir=tmp_path)

    decayed = me.decay_executive_memories_for_company(
        "公司A", days_threshold=90, decay_factor=0.9, store_dir=tmp_path
    )
    assert decayed == 1
    loaded = me.load_executive_memories("公司A", "张总", store_dir=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].weight < 1.0
    assert abs(loaded[0].weight - 0.9) < 1e-6


def test_decay_recent_memory_unchanged(tmp_path):
    """30 天内的记忆不应被衰减。"""
    mem = _make_memory(updated_at=_iso_days_ago(30), weight=1.0)
    me.save_executive_memories("公司A", "张总", [mem], store_dir=tmp_path)

    decayed = me.decay_executive_memories_for_company(
        "公司A", days_threshold=90, decay_factor=0.9, store_dir=tmp_path
    )
    assert decayed == 0
    loaded = me.load_executive_memories("公司A", "张总", store_dir=tmp_path)
    assert abs(loaded[0].weight - 1.0) < 1e-6


def test_decay_weight_floor_zero(tmp_path):
    """连续衰减后 weight 不低于 0（下限保护）。"""
    mem = _make_memory(updated_at=_iso_days_ago(200), weight=0.01)
    me.save_executive_memories("公司A", "张总", [mem], store_dir=tmp_path)

    me.decay_executive_memories_for_company(
        "公司A", days_threshold=90, decay_factor=0.1, store_dir=tmp_path
    )
    loaded = me.load_executive_memories("公司A", "张总", store_dir=tmp_path)
    assert loaded[0].weight >= 0.0


def test_decay_exactly_at_threshold_not_decayed(tmp_path):
    """89 天（明确在阈值内）的记忆不触发衰减。"""
    mem = _make_memory(updated_at=_iso_days_ago(89), weight=1.0)
    me.save_executive_memories("公司A", "张总", [mem], store_dir=tmp_path)

    decayed = me.decay_executive_memories_for_company(
        "公司A", days_threshold=90, decay_factor=0.9, store_dir=tmp_path
    )
    assert decayed == 0


def test_decay_mixed_memories(tmp_path):
    """同一 tag：旧的降权，新的不变。"""
    old_mem = _make_memory("旧表述", "旧口径", updated_at=_iso_days_ago(120), weight=1.0)
    new_mem = _make_memory("新表述", "新口径", updated_at=_iso_days_ago(10), weight=1.0)
    me.save_executive_memories("公司A", "张总", [old_mem, new_mem], store_dir=tmp_path)

    decayed = me.decay_executive_memories_for_company(
        "公司A", days_threshold=90, decay_factor=0.9, store_dir=tmp_path
    )
    assert decayed == 1
    loaded = me.load_executive_memories("公司A", "张总", store_dir=tmp_path)
    weights = sorted([m.weight for m in loaded])
    assert abs(weights[0] - 0.9) < 1e-6   # 旧的降权
    assert abs(weights[1] - 1.0) < 1e-6   # 新的不变


def test_decay_multiple_tags(tmp_path):
    """多 tag 桶：两个 tag 各有一条过期，总计衰减 2 条。"""
    m1 = _make_memory(updated_at=_iso_days_ago(100), tag="张总")
    m2 = _make_memory(updated_at=_iso_days_ago(100), tag="李总")
    me.save_executive_memories("公司A", "张总", [m1], store_dir=tmp_path)
    me.save_executive_memories("公司A", "李总", [m2], store_dir=tmp_path)

    decayed = me.decay_executive_memories_for_company(
        "公司A", days_threshold=90, decay_factor=0.9, store_dir=tmp_path
    )
    assert decayed == 2


def test_decay_empty_company_returns_zero(tmp_path):
    """无记忆的公司衰减返回 0，不报错。"""
    decayed = me.decay_executive_memories_for_company(
        "空公司", days_threshold=90, decay_factor=0.9, store_dir=tmp_path
    )
    assert decayed == 0


# ── 全公司批量衰减 ────────────────────────────────────────────────────────────

def test_decay_all_companies(tmp_path):
    """decay_all_companies 返回含各公司衰减条数的 dict。"""
    m_a = _make_memory(updated_at=_iso_days_ago(100))
    m_b = _make_memory(updated_at=_iso_days_ago(200))
    me.save_executive_memories("公司A", "张总", [m_a], store_dir=tmp_path)
    me.save_executive_memories("公司B", "李总", [m_b], store_dir=tmp_path)

    result = me.decay_all_companies(days_threshold=90, decay_factor=0.9, store_dir=tmp_path)
    assert isinstance(result, dict)
    assert result.get("公司A", 0) == 1
    assert result.get("公司B", 0) == 1


def test_decay_all_companies_empty(tmp_path):
    """空工作区：返回空 dict，不报错。"""
    result = me.decay_all_companies(days_threshold=90, decay_factor=0.9, store_dir=tmp_path)
    assert isinstance(result, dict)
    assert len(result) == 0


# ── updated_at 缺失/异常容错 ─────────────────────────────────────────────────

def test_decay_missing_updated_at_not_crash(tmp_path):
    """updated_at 为空的记忆不导致崩溃（跳过衰减）。"""
    mem = _make_memory(updated_at="", weight=1.0)
    mem = mem.model_copy(update={"updated_at": ""})
    me.save_executive_memories("公司A", "张总", [mem], store_dir=tmp_path)

    decayed = me.decay_executive_memories_for_company(
        "公司A", days_threshold=90, decay_factor=0.9, store_dir=tmp_path
    )
    # 无法解析 updated_at，视为跳过，不崩溃
    loaded = me.load_executive_memories("公司A", "张总", store_dir=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].weight == 1.0  # 未被衰减
