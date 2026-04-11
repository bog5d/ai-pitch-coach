"""
从音频主文件名推断批量模式下的被访谈人与备注（机构-姓名[YYYYMMDD] 等常见命名）。
仓库发版 V7.5（与 build_release.CURRENT_VERSION 对齐）。
"""
from __future__ import annotations

import re
from pathlib import Path

_DATE_TAIL = re.compile(r"^(.+?)(\d{8})$")


def guess_batch_fields_from_stem(stem: str) -> tuple[str, str]:
    """
    根据去掉扩展名的主文件名，返回 (被访谈人, 本段备注)。

    规则：
    - 无「-」：整段作为被访谈人，备注为空。
    - 有「-」：仅按第一个「-」分为机构段与剩余段；剩余段尾部 8 位数字视为日期并从姓名中剥离。
    """
    stem = (stem or "").strip()
    if not stem:
        return "", ""

    if "-" not in stem:
        return stem, ""

    org, rest = stem.split("-", 1)
    org, rest = org.strip(), rest.strip()
    if not rest:
        return org, f"机构：{org}" if org else ""

    m = _DATE_TAIL.match(rest)
    if m:
        name = m.group(1).strip()
        ymd = m.group(2)
        notes = f"机构：{org}；录音文件名日期：{ymd}" if org else f"录音文件名日期：{ymd}"
        return name or rest, notes

    notes = f"机构：{org}" if org else ""
    return rest, notes


def stem_from_audio_filename(filename: str) -> str:
    """从完整文件名得到主文件名（无扩展名）。"""
    return Path(filename or "").stem


def should_autofill_iv(current_iv: str, last_autofilled: str | None) -> bool:
    """
    判断是否应将自动猜测值写入「被访谈人」字段（BUG-C 保护逻辑）。

    规则：
    - 字段为空 → 总是填（首次）
    - last_autofilled 为 None 且字段非空 → 用户全手动填写，不覆盖
    - 当前值等于上次自动填充的值 → 用户未改动，允许用新猜测覆盖
    - 当前值不等于上次自动填充的值 → 用户手动修改过，保护，不覆盖
    """
    if not current_iv:
        return True
    if last_autofilled is None:
        return False
    return current_iv == last_autofilled
