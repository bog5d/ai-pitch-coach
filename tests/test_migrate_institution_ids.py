"""
历史数据迁移脚本测试 — V10.3 P1.1
运行：pytest tests/test_migrate_institution_ids.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_institution_ids as mig


def _make_analytics(tmp_path: Path, filename: str, extra: dict | None = None) -> Path:
    """在 tmp_path 下创建一个模拟的 analytics JSON 文件。"""
    payload = {
        "session_id": "test-session-id",
        "status": "locked",
        "recording_label": filename.replace("_analytics.json", ""),
        "company_id": "测试公司_001",
        "total_score": 80,
    }
    if extra:
        payload.update(extra)
    p = tmp_path / filename
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# ── 机构名解析 ────────────────────────────────────────────────────────────────

def test_extract_institution_from_label_dash():
    """「迪策资本-李志新_...」→ 「迪策资本」。"""
    result = mig._extract_institution_hint("迪策资本-李志新_前1-5测试_analysis_report")
    assert result == "迪策资本"


def test_extract_institution_from_label_no_dash():
    """无短横线时返回 None（无法确定机构名）。"""
    result = mig._extract_institution_hint("李志新_前1-5测试_analysis_report")
    assert result is None


def test_extract_institution_too_short():
    """提取结果过短（单字）时返回 None，避免误匹配。"""
    result = mig._extract_institution_hint("A-李志新_report")
    assert result is None


def test_extract_institution_from_label_multiple_dashes():
    """「红杉资本中国-张总-20240101」→ 取第一段「红杉资本中国」。"""
    result = mig._extract_institution_hint("红杉资本中国-张总-20240101")
    assert result == "红杉资本中国"


# ── 迁移逻辑 ─────────────────────────────────────────────────────────────────

def test_migrate_file_adds_institution_id(tmp_path):
    """对缺失 institution_id 的文件，成功补写字段。"""
    f = _make_analytics(tmp_path, "迪策资本-李志新_report_analytics.json")
    with patch.object(mig.ir, "resolve", return_value=("inst-001", "迪策资本")):
        result = mig.migrate_file(f)
    assert result is True
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["institution_id"] == "inst-001"
    assert data["institution_canonical"] == "迪策资本"


def test_migrate_file_skips_if_already_has_id(tmp_path):
    """已有 institution_id 的文件跳过，不重写。"""
    f = _make_analytics(
        tmp_path, "迪策资本-李志新_report_analytics.json",
        extra={"institution_id": "existing-id", "institution_canonical": "迪策资本"},
    )
    with patch.object(mig.ir, "resolve") as mock_resolve:
        result = mig.migrate_file(f)
    mock_resolve.assert_not_called()
    assert result is False  # False = skipped


def test_migrate_file_skips_if_no_hint(tmp_path):
    """无法从 recording_label 提取机构名的文件跳过。"""
    f = _make_analytics(tmp_path, "李志新_report_analytics.json")
    with patch.object(mig.ir, "resolve") as mock_resolve:
        result = mig.migrate_file(f)
    mock_resolve.assert_not_called()
    assert result is False


def test_migrate_workspace_counts(tmp_path):
    """批量扫描：返回 (total, migrated, skipped) 正确计数。"""
    # 2 需要迁移，1 已有 id
    _make_analytics(tmp_path, "迪策资本-李志新_report_analytics.json")
    _make_analytics(tmp_path, "高瓴资本-王总_report_analytics.json")
    _make_analytics(
        tmp_path, "红杉资本-张总_report_analytics.json",
        extra={"institution_id": "existing", "institution_canonical": "红杉"},
    )

    def mock_resolve(name):
        return (f"id-{name}", name)

    with patch.object(mig.ir, "resolve", side_effect=mock_resolve):
        total, migrated, skipped = mig.migrate_workspace(tmp_path)

    assert total == 3
    assert migrated == 2
    assert skipped == 1


def test_migrate_workspace_empty(tmp_path):
    """空目录：三个计数都为 0。"""
    total, migrated, skipped = mig.migrate_workspace(tmp_path)
    assert total == 0 and migrated == 0 and skipped == 0


def test_migrate_file_broken_json(tmp_path):
    """损坏的 JSON 文件不崩溃，静默 skip。"""
    f = tmp_path / "broken_analytics.json"
    f.write_text("NOT JSON", encoding="utf-8")
    result = mig.migrate_file(f)
    assert result is False


def test_migrate_file_preserves_existing_fields(tmp_path):
    """迁移时不丢失原有字段（total_score 等）。"""
    f = _make_analytics(tmp_path, "迪策资本-李志新_report_analytics.json")
    with patch.object(mig.ir, "resolve", return_value=("inst-001", "迪策资本")):
        mig.migrate_file(f)
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["total_score"] == 80
    assert data["company_id"] == "测试公司_001"


# ── analytics_exporter 新字段测试 ──────────────────────────────────────────────

def _make_dummy_report(score: int = 80, reason: str = "test"):
    """构造满足 schema 约束的最小 AnalysisReport。"""
    from schema import AnalysisReport, SceneAnalysis
    return AnalysisReport(
        scene_analysis=SceneAnalysis(
            scene_type="VC路演",
            speaker_roles="创始人 vs 投资人",
        ),
        total_score=score,
        total_score_deduction_reason=reason,
        risk_points=[],
    )


def test_analytics_exporter_writes_institution_fields(tmp_path):
    """export_analytics 在 ctx 含 institution_id 时，写入 analytics JSON。"""
    import analytics_exporter as ae

    dummy_report = _make_dummy_report(80, "test")
    analysis_json = tmp_path / "test_session.json"
    analysis_json.write_text("{}", encoding="utf-8")
    ctx = {
        "analysis_json": str(analysis_json),
        "company_id": "company_001",
        "interviewee": "张总",
        "biz_type": "",
        "institution_id": "inst-abc",
        "institution_canonical": "迪策资本",
    }
    result = ae.export_analytics(dummy_report, ctx, status="locked")
    assert result is not None
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["institution_id"] == "inst-abc"
    assert data["institution_canonical"] == "迪策资本"


def test_analytics_exporter_no_institution_fields_empty_string(tmp_path):
    """ctx 无 institution_id 时，字段值为空字符串（向后兼容）。"""
    import analytics_exporter as ae

    dummy_report = _make_dummy_report(75, "")
    analysis_json = tmp_path / "test_session2.json"
    analysis_json.write_text("{}", encoding="utf-8")
    ctx = {
        "analysis_json": str(analysis_json),
        "company_id": "company_002",
        "interviewee": "李总",
        "biz_type": "",
    }
    result = ae.export_analytics(dummy_report, ctx, status="locked")
    assert result is not None
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data.get("institution_id", "") == ""
    assert data.get("institution_canonical", "") == ""
