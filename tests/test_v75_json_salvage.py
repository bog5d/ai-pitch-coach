"""
V7.5：LLM 截断 JSON 抢救（不得抛 JSONDecodeError；0 条完整 RiskPoint 时返回 None）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm_judge import (  # noqa: E402
    salvage_risk_point_dicts_from_truncated_llm_json,
    salvage_truncated_analysis_report,
)


def _minimal_risk_point_dict() -> dict:
    return {
        "risk_level": "轻微",
        "tier1_general_critique": "t1",
        "tier2_qa_alignment": "t2",
        "improvement_suggestion": "im",
        "original_text": "实录",
        "start_word_index": 0,
        "end_word_index": 2,
        "score_deduction": 3,
        "deduction_reason": "d",
        "is_manual_entry": False,
    }


def test_salvage_recover_one_complete_risk_point_when_second_truncated() -> None:
    """risk_points 数组内第二条未闭合时，应抢救出第一条完整对象。"""
    r0 = _minimal_risk_point_dict()
    r1 = {**_minimal_risk_point_dict(), "tier1_general_critique": "LONG" * 200}
    doc = {
        "scene_analysis": {"scene_type": "场景", "speaker_roles": "角色"},
        "total_score": 97,
        "total_score_deduction_reason": "理由",
        "risk_points": [r0, r1],
    }
    full = json.dumps(doc, ensure_ascii=False)
    marker = "LONG" * 50
    cut = full.find(marker)
    assert cut > 0
    raw = full[: cut + len(marker) // 2]

    got = salvage_risk_point_dicts_from_truncated_llm_json(raw)
    assert got is not None
    assert len(got) >= 1
    assert got[0]["risk_level"] == "轻微"
    assert got[0]["start_word_index"] == 0


def test_salvage_returns_none_when_no_complete_risk_point() -> None:
    """无任何完整 risk_point 对象时返回 None，且不抛异常。"""
    raw = '{"risk_points":[{"risk_level":"轻微","tier1_general_critique":"未闭合字符串'
    got = salvage_risk_point_dicts_from_truncated_llm_json(raw)
    assert got is None


def test_salvage_never_raises_json_decode_error() -> None:
    for garbage in ("", "{", "not json", '{"risk_points":not an array'):
        try:
            salvage_risk_point_dicts_from_truncated_llm_json(garbage)
        except json.JSONDecodeError as e:  # pragma: no cover
            pytest.fail(f"不应抛出 JSONDecodeError: {e}")


def test_salvage_truncated_analysis_report_builds_model_or_none() -> None:
    r0 = _minimal_risk_point_dict()
    doc = {
        "scene_analysis": {"scene_type": "场景", "speaker_roles": "角色"},
        "total_score": 97,
        "total_score_deduction_reason": "理由",
        "risk_points": [r0],
    }
    full = json.dumps(doc, ensure_ascii=False)
    raw = full[: max(20, len(full) // 3)]

    rep = salvage_truncated_analysis_report(raw)
    assert rep is None or len(rep.risk_points) >= 1
