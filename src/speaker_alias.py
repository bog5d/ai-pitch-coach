from __future__ import annotations

import re

from schema import TranscriptionWord


def ordered_speaker_ids(words: list[TranscriptionWord]) -> list[str]:
    ordered: list[str] = []
    for w in words:
        sid = (w.speaker_id or "").strip()
        if not sid:
            sid = "auto_spk_0"
        if sid not in ordered:
            ordered.append(sid)
    return ordered


def speaker_label_map(words: list[TranscriptionWord]) -> dict[str, str]:
    return {sid: f"发言人 {i + 1}" for i, sid in enumerate(ordered_speaker_ids(words))}


def alias_plain_label(plain_text: str, source_label: str, alias_name: str) -> str:
    plain = plain_text or ""
    src = (source_label or "").strip()
    alias = (alias_name or "").strip()
    if not plain or not src or not alias:
        return plain
    pattern = rf"^\[{re.escape(src)}\]:"
    return re.sub(pattern, f"[{alias}（{src}）]:", plain, flags=re.MULTILINE)

