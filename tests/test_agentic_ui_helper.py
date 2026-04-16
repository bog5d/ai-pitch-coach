"""Agentic UI 规则函数测试。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_build_action_specs_contains_chat_focus_and_briefing():
    from agentic_ui_helper import build_action_specs

    state = {
        "asset_hits": [],
        "memory_events": [{"event_type": "risk_memory_candidate"}],
        "sanitization_meta": {"redaction_count": 2},
    }
    draft = {
        "risk_points": [
            {"_rid": "r1", "risk_type": "财务口径不一致", "score_deduction": 12},
            {"_rid": "r2", "risk_type": "增长逻辑断层", "score_deduction": 11},
        ]
    }
    actions = build_action_specs(state, draft)
    kinds = {a.get("kind") for a in actions}
    assert "chat" in kinds
    assert "focus_risk" in kinds
    assert "briefing" in kinds
    focus = [a for a in actions if a.get("kind") == "focus_risk"][0]
    assert focus.get("target_rid") == "r1"


def test_build_action_buttons_default_fallback():
    from agentic_ui_helper import build_action_buttons

    actions = build_action_buttons({}, {"risk_points": []})
    assert len(actions) >= 1
    assert "一键生成会前简报" in actions


def test_resolve_focus_target_found():
    from agentic_ui_helper import resolve_focus_target

    draft = {"risk_points": [{"_rid": "abc", "risk_type": "叙事断层"}]}
    rp = resolve_focus_target(draft, "abc")
    assert rp is not None
    assert rp.get("risk_type") == "叙事断层"
