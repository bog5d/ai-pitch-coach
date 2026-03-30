"""敏感词解析：宽松分隔符 + 去重（保序），供侧边栏与流水线共用。仓库发版 V6.2（与 build_release.CURRENT_VERSION 对齐）。"""
from __future__ import annotations

import re


def parse_sensitive_words(raw_input: str) -> list[str]:
    """
    按逗号/中英文分号/任意空白切分，strip 后去空、去重（保留首次出现顺序）。
    """
    if raw_input is None:
        return []
    s = str(raw_input)
    if not s.strip():
        return []
    parts = re.split(r"[,\s，；;]+", s)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        w = p.strip()
        if not w or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out
