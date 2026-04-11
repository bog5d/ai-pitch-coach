"""
institution_profiler 单元测试 — V10.2
运行：pytest tests/test_institution_profiler.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from institution_profiler import build_institution_profile, list_all_institution_profiles


def _write_analytics(directory: Path, filename: str, data: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _make_analytics(
    institution_id: str,
    canonical: str,
    company_id: str,
    score: int,
    risk_types: dict,
    severe_count: int = 0,
    total_risk: int = 3,
    locked_at: str = "2026-01-01T00:00:00Z",
) -> dict:
    return {
        "institution_id": institution_id,
        "institution_canonical": canonical,
        "company_id": company_id,
        "total_score": score,
        "total_risk_count": total_risk,
        "risk_breakdown": {"严重": {"count": severe_count, "total_deduction": severe_count * 5}},
        "risk_type_counts": risk_types,
        "locked_at": locked_at,
        "killer_questions": [],
    }


IID_A = "inst-aaa-111"
IID_B = "inst-bbb-222"


def test_empty_workspace_returns_zero_profile(tmp_path):
    profile = build_institution_profile(IID_A, tmp_path)
    assert profile["total_sessions"] == 0
    assert profile["avg_score"] == 0.0


def test_single_session_profile(tmp_path):
    _write_analytics(
        tmp_path / "proj1",
        "session1_analytics.json",
        _make_analytics(IID_A, "迪策资本", "泽天智航", 82, {"估值回避": 2}),
    )
    profile = build_institution_profile(IID_A, tmp_path)
    assert profile["total_sessions"] == 1
    assert profile["avg_score"] == 82.0
    assert profile["total_companies"] == 1
    assert profile["top_risk_types"][0]["risk_type"] == "估值回避"


def test_multi_session_avg_score(tmp_path):
    for i, score in enumerate([70, 80, 90]):
        _write_analytics(
            tmp_path,
            f"s{i}_analytics.json",
            _make_analytics(IID_A, "高瓴资本", f"company_{i}", score, {}),
        )
    profile = build_institution_profile(IID_A, tmp_path)
    assert profile["total_sessions"] == 3
    assert profile["avg_score"] == 80.0


def test_multi_company_count(tmp_path):
    for company in ["A公司", "B公司", "C公司"]:
        _write_analytics(
            tmp_path,
            f"{company}_analytics.json",
            _make_analytics(IID_A, "红杉资本", company, 75, {}),
        )
    profile = build_institution_profile(IID_A, tmp_path)
    assert profile["total_companies"] == 3


def test_top_risk_types_sorted(tmp_path):
    _write_analytics(tmp_path, "s1_analytics.json",
        _make_analytics(IID_A, "机构X", "公司1", 70, {"逻辑断裂": 3, "数据含糊": 1}))
    _write_analytics(tmp_path, "s2_analytics.json",
        _make_analytics(IID_A, "机构X", "公司2", 75, {"逻辑断裂": 2, "估值回避": 4}))
    profile = build_institution_profile(IID_A, tmp_path)
    types = [r["risk_type"] for r in profile["top_risk_types"]]
    # 逻辑断裂 5次 > 估值回避 4次 > 数据含糊 1次
    assert types[0] == "逻辑断裂"
    assert types[1] == "估值回避"


def test_severe_risk_ratio(tmp_path):
    _write_analytics(tmp_path, "s1_analytics.json",
        _make_analytics(IID_A, "机构Y", "公司1", 60,
                        {}, severe_count=2, total_risk=4))
    profile = build_institution_profile(IID_A, tmp_path)
    assert profile["severe_risk_ratio"] == 0.5  # 2/4


def test_other_institution_not_included(tmp_path):
    _write_analytics(tmp_path, "a_analytics.json",
        _make_analytics(IID_A, "机构A", "公司1", 80, {}))
    _write_analytics(tmp_path, "b_analytics.json",
        _make_analytics(IID_B, "机构B", "公司2", 70, {}))
    profile = build_institution_profile(IID_A, tmp_path)
    assert profile["total_sessions"] == 1


def test_list_all_institution_profiles(tmp_path):
    _write_analytics(tmp_path, "a1_analytics.json",
        _make_analytics(IID_A, "机构A", "公司1", 80, {}))
    _write_analytics(tmp_path, "a2_analytics.json",
        _make_analytics(IID_A, "机构A", "公司2", 75, {}))
    _write_analytics(tmp_path, "b1_analytics.json",
        _make_analytics(IID_B, "机构B", "公司3", 70, {}))
    profiles = list_all_institution_profiles(tmp_path)
    assert len(profiles) == 2
    # 按 session 数降序：机构A(2场) > 机构B(1场)
    assert profiles[0]["institution_id"] == IID_A


def test_corrupted_file_skipped(tmp_path):
    (tmp_path / "broken_analytics.json").write_text("not json", encoding="utf-8")
    _write_analytics(tmp_path, "good_analytics.json",
        _make_analytics(IID_A, "机构A", "公司1", 80, {}))
    profile = build_institution_profile(IID_A, tmp_path)
    assert profile["total_sessions"] == 1


def test_score_trend_max_20(tmp_path):
    for i in range(25):
        _write_analytics(tmp_path, f"s{i}_analytics.json",
            _make_analytics(IID_A, "机构A", f"公司{i}", 70 + i % 10,
                            {}, locked_at=f"2026-01-{i+1:02d}T00:00:00Z"))
    profile = build_institution_profile(IID_A, tmp_path)
    assert len(profile["score_trend"]) == 20
