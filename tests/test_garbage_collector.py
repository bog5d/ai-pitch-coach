"""garbage_collector：仅删过期中间 JSON，不碰 HTML/音频。仓库发版 V6.2（与 build_release.CURRENT_VERSION 对齐）。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from garbage_collector import sweep_stale_intermediate_json  # noqa: E402


def test_sweep_deletes_stale_transcription_only(tmp_path: Path) -> None:
    stale = tmp_path / "demo_transcription.json"
    stale.write_text("[]", encoding="utf-8")
    old = time.time() - 10 * 24 * 3600
    os.utime(stale, (old, old))

    html = tmp_path / "demo_复盘报告.html"
    html.write_text("<html/>", encoding="utf-8")
    os.utime(html, (old, old))

    audio = tmp_path / "demo.m4a"
    audio.write_bytes(b"fake")
    os.utime(audio, (old, old))

    n = sweep_stale_intermediate_json(tmp_path)
    assert n == 1
    assert not stale.is_file()
    assert html.is_file()
    assert audio.is_file()


def test_sweep_keeps_recent_json(tmp_path: Path) -> None:
    p = tmp_path / "x_transcription.json"
    p.write_text("[]", encoding="utf-8")
    assert sweep_stale_intermediate_json(tmp_path) == 0
    assert p.is_file()
