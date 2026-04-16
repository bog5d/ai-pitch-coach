from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_speaker_label_map_orders_by_first_seen():
    from schema import TranscriptionWord
    from speaker_alias import speaker_label_map

    words = [
        TranscriptionWord(word_index=0, text="你", start_time=0.0, end_time=0.1, speaker_id="s2"),
        TranscriptionWord(word_index=1, text="好", start_time=0.1, end_time=0.2, speaker_id="s1"),
    ]
    m = speaker_label_map(words)
    assert m["s2"] == "发言人 1"
    assert m["s1"] == "发言人 2"


def test_alias_plain_label_replaces_header_only():
    from speaker_alias import alias_plain_label

    plain = "[发言人 1]: 你好。\n\n[发言人 2]: 我是李总。"
    out = alias_plain_label(plain, "发言人 2", "张三")
    assert "[张三（发言人 2）]:" in out
    assert "[发言人 1]:" in out

