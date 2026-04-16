"""Week 3：agent_tenant 闸门。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_resolve_unknown_returns_none():
    from agent_tenant import resolve_memory_company_id

    assert resolve_memory_company_id("unknown") is None
    assert resolve_memory_company_id("UNKNOWN") is None


def test_resolve_empty_returns_none():
    from agent_tenant import resolve_memory_company_id

    assert resolve_memory_company_id("") is None
    assert resolve_memory_company_id("   ") is None


def test_resolve_未指定_returns_none():
    from agent_tenant import resolve_memory_company_id

    assert resolve_memory_company_id("未指定") is None


def test_resolve_real_id_normalized():
    from agent_tenant import resolve_memory_company_id

    assert resolve_memory_company_id("co_1") == "co_1"
