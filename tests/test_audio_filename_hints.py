"""
audio_filename_hints 单元测试。
仓库发版 V7.2（与 build_release.CURRENT_VERSION 对齐）。
运行：python tests/test_audio_filename_hints.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from audio_filename_hints import (  # noqa: E402
    guess_batch_fields_from_stem,
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


if __name__ == "__main__":
    test_example_with_org_name_date()
    test_no_date_suffix()
    test_no_hyphen()
    test_multiple_hyphens_first_split_only()
    test_stem_from_audio_filename()
    test_empty_stem()
    print("OK: test_audio_filename_hints 全部通过")
