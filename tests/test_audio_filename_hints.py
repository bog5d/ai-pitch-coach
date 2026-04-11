"""
audio_filename_hints 单元测试。
仓库发版 V7.5（与 build_release.CURRENT_VERSION 对齐）。
运行：python tests/test_audio_filename_hints.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from audio_filename_hints import (  # noqa: E402
    guess_batch_fields_from_stem,
    should_autofill_iv,
    stem_from_audio_filename,
)


def test_example_with_org_name_date() -> None:
    iv, note = guess_batch_fields_from_stem("迪策资本-赵治鹏20260108")
    assert iv == "赵治鹏"
    assert "迪策资本" in note
    assert "20260108" in note


def test_no_date_suffix() -> None:
    iv, note = guess_batch_fields_from_stem("迪策资本-赵治鹏")
    assert iv == "赵治鹏"
    assert note == "机构：迪策资本"


def test_no_hyphen() -> None:
    iv, note = guess_batch_fields_from_stem("单场录音001")
    assert iv == "单场录音001"
    assert note == ""


def test_multiple_hyphens_first_split_only() -> None:
    iv, note = guess_batch_fields_from_stem("机构A-部门B-张三20240101")
    assert iv == "部门B-张三"
    assert "机构A" in note
    assert "20240101" in note


def test_stem_from_audio_filename() -> None:
    assert stem_from_audio_filename("x/y/迪策资本-赵治鹏20260108.m4a") == "迪策资本-赵治鹏20260108"


def test_empty_stem() -> None:
    assert guess_batch_fields_from_stem("") == ("", "")
    assert guess_batch_fields_from_stem("   ") == ("", "")


# ── BUG-C：自动填充保护逻辑 ──

def test_autofill_empty_field_always_fills() -> None:
    """字段为空时，不管历史如何，都应自动填充。"""
    assert should_autofill_iv("", None) is True
    assert should_autofill_iv("", "赵治鹏") is True


def test_autofill_first_time_fills() -> None:
    """从未自动填充过（last_autofilled=None），有值的字段也应填充（首次触发）。"""
    assert should_autofill_iv("", None) is True


def test_autofill_user_unchanged_fills() -> None:
    """用户没改过（当前值等于上次自动填充），应允许覆盖新猜测。"""
    assert should_autofill_iv("赵治鹏", "赵治鹏") is True


def test_autofill_user_changed_protects() -> None:
    """用户手动改过（当前值不等于上次自动填充），不覆盖。"""
    assert should_autofill_iv("李总", "赵治鹏") is False


def test_autofill_manual_non_empty_no_history_protects() -> None:
    """有值且没有上次自动填充记录：说明用户全手动填写，不覆盖。"""
    assert should_autofill_iv("手动填的名字", None) is False


if __name__ == "__main__":
    test_example_with_org_name_date()
    test_no_date_suffix()
    test_no_hyphen()
    test_multiple_hyphens_first_split_only()
    test_stem_from_audio_filename()
    test_empty_stem()
    print("OK: test_audio_filename_hints 全部通过")
