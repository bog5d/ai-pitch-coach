"""audio_preprocess.smart_compress_media 轻量单测（小文件免压）。仓库发版 V7.5（与 build_release.CURRENT_VERSION 对齐）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from audio_preprocess import smart_compress_media  # noqa: E402


def test_under_10mb_skips_compression() -> None:
    data = b"\x00" * (1024 * 1024)  # 1MB
    r = smart_compress_media(data, filename_hint="x.mp3")
    assert r.data == data
    assert r.did_compress is False
    assert r.used_fallback is False
