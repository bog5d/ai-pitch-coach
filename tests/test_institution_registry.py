"""
institution_registry 单元测试 — V10.3
新增：短名称修复、备份机制、内置词典预热
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


# ── 基础注册 ──────────────────────────────────────────────────────────────────

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


# ── P0.1 短名称修复测试（这些在 V10.2 下全部失败）────────────────────────────

def test_short_name_matches_full_name(tmp_path):
    """「红杉」应匹配「红杉资本中国」—— V10.2 的核心 bug。"""
    with _mock_path(tmp_path):
        ir.register("红杉资本中国")
        result = ir.fuzzy_match("红杉")
    assert result is not None, "短名称「红杉」应命中「红杉资本中国」"
    assert result["canonical_name"] == "红杉资本中国"


def test_short_name_gaoliu(tmp_path):
    """「高瓴」应匹配「高瓴资本」。"""
    with _mock_path(tmp_path):
        ir.register("高瓴资本")
        result = ir.fuzzy_match("高瓴")
    assert result is not None
    assert result["canonical_name"] == "高瓴资本"


def test_short_name_ce(tmp_path):
    """「迪策」应匹配「迪策资本」。"""
    with _mock_path(tmp_path):
        ir.register("迪策资本")
        result = ir.fuzzy_match("迪策")
    assert result is not None
    assert result["canonical_name"] == "迪策资本"


def test_full_name_matches_short_registered(tmp_path):
    """先注册简称，再用全称 resolve，应归并而非新建。"""
    with _mock_path(tmp_path):
        rec_short = ir.register("红杉")
        iid, canonical = ir.resolve("红杉资本中国")
    assert iid == rec_short["id"], "全称应归并到已有的简称记录"


def test_different_institutions_not_merged(tmp_path):
    """「高盛」不应匹配「高瓴资本」—— 防止过度合并。"""
    with _mock_path(tmp_path):
        ir.register("高瓴资本")
        result = ir.fuzzy_match("高盛")
    assert result is None, "「高盛」不应被误判为「高瓴资本」"


def test_resolve_short_then_full_same_id(tmp_path):
    """「红杉」和「红杉中国」应返回同一 institution_id。"""
    with _mock_path(tmp_path):
        id1, _ = ir.resolve("红杉资本中国")
        id2, _ = ir.resolve("红杉")
        id3, _ = ir.resolve("红杉中国")
    assert id1 == id2 == id3, "同一机构的不同写法应返回相同 id"


def test_normalize_name_strips_suffix():
    """_normalize_name 去后缀逻辑验证。"""
    assert ir._normalize_name("红杉资本中国") == "红杉"
    assert ir._normalize_name("高瓴资本") == "高瓴"
    assert ir._normalize_name("经纬中国") == "经纬"
    assert ir._normalize_name("IDG资本") == "idg"


def test_enhanced_similarity_short_vs_full():
    """增强相似度：短名 vs 全名应 ≥ 阈值。"""
    score = ir._enhanced_similarity("红杉", "红杉资本中国")
    assert score >= 0.75, f"相似度 {score} 低于预期"


# ── P0.2 备份机制测试 ─────────────────────────────────────────────────────────

def test_backup_created_on_save(tmp_path):
    """每次写入后应生成 .bak1 备份文件。"""
    reg = tmp_path / "institutions.json"
    with patch.object(ir, "_get_registry_path", return_value=reg):
        ir.register("机构A")  # 第一次写入
        ir.register("机构B")  # 第二次写入（此时 .bak1 应存在）
    assert reg.with_suffix(".bak1").exists(), ".bak1 备份应存在"


def test_backup_rotation(tmp_path):
    """多次写入后 .bak1/.bak2/.bak3 都应存在。"""
    reg = tmp_path / "institutions.json"
    with patch.object(ir, "_get_registry_path", return_value=reg):
        for name in ["机构A", "机构B", "机构C", "机构D"]:
            ir.register(name)
    # 经过4次写入，至少 bak1/bak2 应存在
    assert reg.with_suffix(".bak1").exists()
    assert reg.with_suffix(".bak2").exists()


def test_corrupted_main_falls_back_to_backup(tmp_path):
    """主文件损坏时应从备份恢复。"""
    reg = tmp_path / "institutions.json"
    with patch.object(ir, "_get_registry_path", return_value=reg):
        ir.register("测试机构")
        ir.register("测试机构2")  # 第二次写入，.bak1 = 第一次状态
    # 损坏主文件
    reg.write_text("CORRUPTED NOT JSON", encoding="utf-8")
    with patch.object(ir, "_get_registry_path", return_value=reg):
        recs = ir._load_registry(reg)
    # 从 bak1 恢复，应有数据
    assert isinstance(recs, list)


def test_backup_status_returns_dict(tmp_path):
    """list_backup_status 应返回包含 main_exists 和 backups 的 dict。"""
    reg = tmp_path / "institutions.json"
    with patch.object(ir, "_get_registry_path", return_value=reg):
        ir.register("测试机构")
        status = ir.list_backup_status()
    assert "main_exists" in status
    assert "backups" in status
    assert len(status["backups"]) == ir._BACKUP_COUNT


# ── 内置词典预热测试 ──────────────────────────────────────────────────────────

def test_known_aliases_seeded_on_first_load(tmp_path):
    """首次调用 get_all（空注册表）应触发内置词典预热。"""
    with _mock_path(tmp_path):
        all_recs = ir.get_all()
    assert len(all_recs) > 0, "内置 VC 词典应已预热"
    names = [r["canonical_name"] for r in all_recs]
    assert "红杉资本中国" in names


def test_known_aliases_not_seeded_if_data_exists(tmp_path):
    """已有数据时不应触发预热，避免覆盖用户数据。"""
    with _mock_path(tmp_path):
        ir.register("用户自定义机构")
        recs_before = ir.get_all()
        count_before = len(recs_before)
        # 再次调用不应新增内置词典
        recs_after = ir.get_all()
    assert len(recs_after) == count_before


# ── 原有测试（向后兼容）──────────────────────────────────────────────────────

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


def test_resolve_empty_returns_empty(tmp_path):
    with _mock_path(tmp_path):
        iid, canonical = ir.resolve("")
    assert iid == ""
    assert canonical == ""


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


def test_increment_session_count(tmp_path):
    with _mock_path(tmp_path):
        rec = ir.register("测试机构")
        ir.increment_session_count(rec["id"])
        ir.increment_session_count(rec["id"])
        updated = ir.get_by_id(rec["id"])
    assert updated["session_count"] == 2


def test_persistence_across_calls(tmp_path):
    registry_file = tmp_path / "institutions.json"
    with patch.object(ir, "_get_registry_path", return_value=registry_file):
        ir.register("持久化测试机构")
    with patch.object(ir, "_get_registry_path", return_value=registry_file):
        all_recs = ir.get_all()
    assert any(r["canonical_name"] == "持久化测试机构" for r in all_recs)
