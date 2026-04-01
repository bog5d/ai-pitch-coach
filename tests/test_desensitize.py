"""report_builder.desensitize_text（pypinyin）。仓库发版 V7.5（与 build_release.CURRENT_VERSION 对齐）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pytest

pytest.importorskip("pypinyin")

from report_builder import desensitize_text  # noqa: E402


def test_desensitize_person_xxx() -> None:
    assert desensitize_text("张三", is_person=True) == "XXX"


def test_desensitize_org_dice_capital_suffix() -> None:
    s = desensitize_text("迪策资本", is_person=False)
    assert "资本" in s
    assert s.startswith("D")
