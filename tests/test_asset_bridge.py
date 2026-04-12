"""
asset_bridge 单元测试 — 行动项三 TDD
覆盖：load_asset_index / find_related_assets / briefing_engine 集成
全程 Mock，零 API 费用。
运行：pytest tests/test_asset_bridge.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import asset_bridge
from asset_bridge import load_asset_index, find_related_assets


# ─── 测试夹具 ──────────────────────────────────────────────

SAMPLE_ASSETS = [
    {
        "filename": "2024年度审计报告.pdf",
        "relative_path": "01_财务文件/财报",
        "full_path": "/data/01_财务文件/财报/2024年度审计报告.pdf",
        "last_modified": "2024-12-31",
        "summary": "2024年度财务审计报告，德勤出具，含资产负债表和利润表",
        "tags": ["财务审计", "年度报告"],
    },
    {
        "filename": "股权结构图.pdf",
        "relative_path": "02_法务文件",
        "full_path": "/data/02_法务文件/股权结构图.pdf",
        "last_modified": "2024-06-01",
        "summary": "公司股权架构说明，含创始人持股比例",
        "tags": ["股权结构", "法务"],
    },
    {
        "filename": "商业计划书BP.pdf",
        "relative_path": "00_通用素材库",
        "full_path": "/data/00_通用素材库/商业计划书BP.pdf",
        "last_modified": "2025-01-15",
        "summary": "公司商业模式介绍和市场分析",
        "tags": ["商业模式", "市场分析"],
    },
]


def _make_index_file(tmp_path: Path, assets: list[dict] | None = None) -> Path:
    """在 tmp_path 写入 asset_index.json，返回文件路径。"""
    data = {
        "generated_at": "2026-04-12T10:00:00",
        "source_dir": "/data",
        "total_files": len(assets or SAMPLE_ASSETS),
        "assets": assets if assets is not None else SAMPLE_ASSETS,
    }
    p = tmp_path / "asset_index.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


# ─── load_asset_index 测试 ──────────────────────────────────

def test_load_returns_assets_when_file_exists(tmp_path):
    """正常路径：文件存在时返回 assets 列表。"""
    _make_index_file(tmp_path)
    with patch.dict(os.environ, {"FOS_DATA_DIR": str(tmp_path)}):
        assets = load_asset_index()
    assert len(assets) == 3
    assert assets[0]["filename"] == "2024年度审计报告.pdf"


def test_load_returns_empty_list_when_file_missing(tmp_path):
    """文件不存在时静默返回空列表，不抛异常。"""
    with patch.dict(os.environ, {"FOS_DATA_DIR": str(tmp_path)}):
        assets = load_asset_index()
    assert assets == []


def test_load_returns_empty_list_when_json_malformed(tmp_path):
    """JSON 损坏时静默返回空列表，不抛异常。"""
    (tmp_path / "asset_index.json").write_text("{ broken json !!!", encoding="utf-8")
    with patch.dict(os.environ, {"FOS_DATA_DIR": str(tmp_path)}):
        assets = load_asset_index()
    assert assets == []


def test_load_returns_empty_list_when_assets_key_missing(tmp_path):
    """assets 字段缺失时返回空列表。"""
    (tmp_path / "asset_index.json").write_text(
        json.dumps({"generated_at": "2026-01-01", "total_files": 0}),
        encoding="utf-8",
    )
    with patch.dict(os.environ, {"FOS_DATA_DIR": str(tmp_path)}):
        assets = load_asset_index()
    assert assets == []


# ─── find_related_assets 测试 ──────────────────────────────

def test_find_hits_by_filename(tmp_path):
    """关键词命中文件名时应返回该资产。"""
    results = find_related_assets("审计报告", SAMPLE_ASSETS)
    filenames = [r["filename"] for r in results]
    assert "2024年度审计报告.pdf" in filenames


def test_find_hits_by_summary(tmp_path):
    """关键词命中 summary 时应返回该资产。"""
    results = find_related_assets("资产负债", SAMPLE_ASSETS)
    filenames = [r["filename"] for r in results]
    assert "2024年度审计报告.pdf" in filenames


def test_find_hits_by_tags(tmp_path):
    """关键词命中 tags 时应返回该资产。"""
    results = find_related_assets("股权结构", SAMPLE_ASSETS)
    filenames = [r["filename"] for r in results]
    assert "股权结构图.pdf" in filenames


def test_find_returns_top_n_at_most(tmp_path):
    """结果数量不超过 top_n。"""
    results = find_related_assets("pdf 报告 文件 数据", SAMPLE_ASSETS, top_n=2)
    assert len(results) <= 2


def test_find_returns_empty_when_no_match(tmp_path):
    """无命中关键词时返回空列表。"""
    results = find_related_assets("完全不相关的内容xyz", SAMPLE_ASSETS)
    assert results == []


def test_find_returns_empty_when_assets_empty(tmp_path):
    """空资产列表时返回空列表。"""
    results = find_related_assets("审计", [])
    assert results == []


def test_find_returns_empty_when_keyword_empty(tmp_path):
    """空关键词时返回空列表。"""
    results = find_related_assets("", SAMPLE_ASSETS)
    assert results == []


def test_find_higher_hits_ranked_first(tmp_path):
    """命中次数更多的资产应排在前面。"""
    assets = [
        {
            "filename": "财务报告.pdf",
            "relative_path": "",
            "full_path": "/财务报告.pdf",
            "last_modified": "2024-01-01",
            "summary": "财务 审计",   # 2次命中
            "tags": ["财务审计"],
        },
        {
            "filename": "BP.pdf",
            "relative_path": "",
            "full_path": "/BP.pdf",
            "last_modified": "2024-01-01",
            "summary": "商业计划",    # 0次命中
            "tags": [],
        },
    ]
    results = find_related_assets("财务 审计", assets, top_n=3)
    assert results[0]["filename"] == "财务报告.pdf"


# ─── briefing_engine 集成测试 ──────────────────────────────

def _make_analytics(tmp_path: Path, filename: str, institution_id: str,
                    company_id: str, score: int, risk_types: dict) -> None:
    data = {
        "institution_id": institution_id,
        "institution_canonical": "测试机构",
        "company_id": company_id,
        "total_score": score,
        "total_risk_count": sum(risk_types.values()),
        "risk_breakdown": {"严重": {"count": 1, "total_deduction": 5}},
        "risk_type_counts": risk_types,
        "locked_at": "2026-01-01T00:00:00Z",
        "killer_questions": [],
    }
    (tmp_path / filename).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_briefing_text_appends_asset_section_when_files_exist(tmp_path):
    """有匹配资产时，会前简报末尾应含「库中相关资产」段落。"""
    from briefing_engine import generate_briefing_text

    _make_analytics(tmp_path, "s1_analytics.json", "inst-x", "cmp-x", 70,
                    {"财务审计": 3, "估值逻辑": 2})

    fos_dir = tmp_path / "fos_data"
    fos_dir.mkdir()
    _make_index_file(fos_dir)  # 含 "2024年度审计报告.pdf" (tags: 财务审计)

    with (
        patch("briefing_engine.OpenAI", side_effect=Exception("no llm")),
        patch.dict(os.environ, {"FOS_DATA_DIR": str(fos_dir)}),
    ):
        text = generate_briefing_text(
            "inst-x", "cmp-x", tmp_path,
            company_name="泽天智航", institution_name="迪策资本",
        )

    assert "库中相关资产" in text
    assert "2024年度审计报告.pdf" in text


def test_briefing_text_no_asset_section_when_index_missing(tmp_path):
    """asset_index.json 不存在时，简报正常生成，不含资产段落，不抛异常。"""
    from briefing_engine import generate_briefing_text

    _make_analytics(tmp_path, "s1_analytics.json", "inst-x", "cmp-x", 70,
                    {"财务审计": 3})

    empty_fos_dir = tmp_path / "fos_data_empty"
    empty_fos_dir.mkdir()
    # 不写 asset_index.json → 应静默降级

    with (
        patch("briefing_engine.OpenAI", side_effect=Exception("no llm")),
        patch.dict(os.environ, {"FOS_DATA_DIR": str(empty_fos_dir)}),
    ):
        text = generate_briefing_text("inst-x", "cmp-x", tmp_path)

    assert "会前简报" in text  # 主内容正常
    assert "库中相关资产" not in text  # 无资产索引时不追加


def test_briefing_text_no_history_no_asset_section(tmp_path):
    """无历史数据路径：也应正常生成，不因资产桥接崩溃。"""
    from briefing_engine import generate_briefing_text

    empty_fos_dir = tmp_path / "fos_data_empty"
    empty_fos_dir.mkdir()

    with (
        patch("briefing_engine.OpenAI"),
        patch.dict(os.environ, {"FOS_DATA_DIR": str(empty_fos_dir)}),
    ):
        text = generate_briefing_text("inst-x", "cmp-x", tmp_path)

    assert "暂无该机构的历史数据" in text
