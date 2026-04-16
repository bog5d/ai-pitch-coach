"""Week 5：记忆事件化输出。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_node_memory_event_producer_outputs_compatible_event_shape():
    from agent_nodes import node_memory_event_producer

    rp = SimpleNamespace(
        tier1_general_critique="逻辑链条断裂，未给出验证数据。",
        improvement_suggestion="建议按结论-证据-风险对策结构回答。",
        score_deduction=8,
        risk_type="逻辑断裂",
    )
    report = SimpleNamespace(risk_points=[rp])
    state = {
        "report": report,
        "memory_company_id": "co1",
        "explicit_context": {"interviewee": "张三"},
    }
    out = node_memory_event_producer(state)  # type: ignore[arg-type]
    events = out["memory_events"]
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "risk_memory_candidate"
    assert e["company_id"] == "co1"
    assert e["tag"] == "张三"
    assert "raw_text" in e["memory"] and "correction" in e["memory"]
    assert isinstance(e["memory"]["weight"], float)


def test_feedback_telemetry_contains_memory_events_and_count():
    from agent_nodes import node_feedback_telemetry

    report = SimpleNamespace(risk_points=[])
    state = {
        "trace_id": "t1",
        "tenant_id": "co1",
        "memory_io_enabled": True,
        "memory_company_id": "co1",
        "asset_hits": [],
        "memory_events": [{"event_type": "risk_memory_candidate"}],
        "report": report,
    }
    out = node_feedback_telemetry(state)  # type: ignore[arg-type]
    tele = out["feedback_telemetry"]
    assert tele["memory_event_count"] == 1
    assert len(tele["memory_events"]) == 1
    assert tele["feedback_persisted"] is False
