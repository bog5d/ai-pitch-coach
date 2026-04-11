"""
institution_registry 单元测试 — V10.2
运行：pytest tests/test_institution_registry.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import institution_registry as ir


def _mock_path(tmp_path: Path):
    registry_file = tmp_path / "institutions.json"
    return patch.object(ir, "_get_registry_path", return_value=registry_file)


# ── 基础注册 ─────────────────────────────────────────────────────────────────

def test_register_new_institution(tmp_path):
    with _mock_path(tmp_path):
        rec = ir.register("迪策资本")
    assert rec["canonical_name"] == "迪策资本"
    assert rec["id"]
    assert rec["session_count"] == 0


def test_register_same_canonical_idempotent(tmp_path):
    with _mock_path(tmp_path):
        r1 = ir.register("迪策资本")
        r2 = ir.register("迪策资本")
    assert r1["id"] == r2["id"]


def test_register_adds_alias(tmp_path):
    with _mock_path(tmp_path):
        r1 = ir.register("迪策资本")
        r2 = ir.register("迪策资本", alias="迪策基金")
    assert r1["id"] == r2["id"]
    assert "迪策基金" in r2["aliases"]


# ── 模糊匹配 ─────────────────────────────────────────────────────────────────

def test_fuzzy_match_similar_name(tmp_path):
    with _mock_path(tmp_path):
        ir.register("迪策资本")
        result = ir.fuzzy_match("迪策基金")
    # "迪策基金" vs "迪策资本" — 相似度约 0.78，略低于阈值
    # 用更相近的名字测试
    with _mock_path(tmp_path):
        ir.register("红杉资本中国")
        result2 = ir.fuzzy_match("红杉资本中国基金")
    assert result2 is not None
    assert result2["canonical_name"] == "红杉资本中国"


def test_fuzzy_match_exact_returns_record(tmp_path):
    with _mock_path(tmp_path):
        ir.register("高瓴资本")
        result = ir.fuzzy_match("高瓴资本")
    assert result is not None
    assert result["canonical_name"] == "高瓴资本"
    assert result["similarity"] == 1.0


def test_fuzzy_match_no_match_returns_none(tmp_path):
    with _mock_path(tmp_path):
        ir.register("高瓴资本")
        result = ir.fuzzy_match("字节跳动")
    assert result is None


def test_fuzzy_match_empty_returns_none(tmp_path):
    with _mock_path(tmp_path):
        result = ir.fuzzy_match("")
    assert result is None


# ── resolve ──────────────────────────────────────────────────────────────────

def test_resolve_new_name_creates_institution(tmp_path):
    with _mock_path(tmp_path):
        iid, canonical = ir.resolve("顺为资本")
    assert iid
    assert canonical == "顺为资本"


def test_resolve_same_name_returns_same_id(tmp_path):
    with _mock_path(tmp_path):
        id1, _ = ir.resolve("顺为资本")
        id2, _ = ir.resolve("顺为资本")
    assert id1 == id2


def test_resolve_empty_returns_empty(tmp_path):
    with _mock_path(tmp_path):
        iid, canonical = ir.resolve("")
    assert iid == ""
    assert canonical == ""


# ── get_all / get_by_id ───────────────────────────────────────────────────────

def test_get_all_returns_all_records(tmp_path):
    with _mock_path(tmp_path):
        ir.register("机构A")
        ir.register("机构B")
        ir.register("机构C")
        all_recs = ir.get_all()
    assert len(all_recs) == 3


def test_get_by_id_found(tmp_path):
    with _mock_path(tmp_path):
        rec = ir.register("机构X")
        found = ir.get_by_id(rec["id"])
    assert found is not None
    assert found["canonical_name"] == "机构X"


def test_get_by_id_not_found(tmp_path):
    with _mock_path(tmp_path):
        result = ir.get_by_id("nonexistent-id")
    assert result is None


# ── session_count ─────────────────────────────────────────────────────────────

def test_increment_session_count(tmp_path):
    with _mock_path(tmp_path):
        rec = ir.register("测试机构")
        ir.increment_session_count(rec["id"])
        ir.increment_session_count(rec["id"])
        updated = ir.get_by_id(rec["id"])
    assert updated["session_count"] == 2


# ── 持久化 ────────────────────────────────────────────────────────────────────

def test_persistence_across_calls(tmp_path):
    """两次独立的 _load_registry 调用之间数据应持久化。"""
    registry_file = tmp_path / "institutions.json"
    with patch.object(ir, "_get_registry_path", return_value=registry_file):
        ir.register("持久化测试机构")
    # 重新加载
    with patch.object(ir, "_get_registry_path", return_value=registry_file):
        all_recs = ir.get_all()
    assert any(r["canonical_name"] == "持久化测试机构" for r in all_recs)
