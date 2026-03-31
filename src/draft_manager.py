"""
本地草稿箱：原子落盘与恢复（随 CURRENT_VERSION，始于 V7.0）。
草稿目录位于可写应用根下的隐藏文件夹 `.drafts/`（与 .env、默认归档一致）。
发版与根目录 build_release.py → CURRENT_VERSION 对齐。
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from runtime_paths import get_writable_app_root

logger = logging.getLogger(__name__)

_DRAFT_PREFIX = "draft_"
_TEMP_PREFIX = "temp_"
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")


def _sanitize_session_id(session_id: str) -> str:
    s = (session_id or "").strip()
    if not s or not _SESSION_ID_RE.fullmatch(s):
        raise ValueError("session_id 无效或包含非法字符")
    return s


def _drafts_dir() -> Path:
    d = get_writable_app_root() / ".drafts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_draft(session_id: str, data: dict[str, Any]) -> None:
    """原子写入：先 temp_*.json，再 os.replace 为 draft_*.json。"""
    sid = _sanitize_session_id(session_id)
    base = _drafts_dir()
    tmp = base / f"{_TEMP_PREFIX}{sid}.json"
    final = base / f"{_DRAFT_PREFIX}{sid}.json"
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, final)


def load_draft(session_id: str) -> dict[str, Any] | None:
    """读取草稿；JSON 损坏则静默删除文件并返回 None。"""
    try:
        sid = _sanitize_session_id(session_id)
    except ValueError:
        return None
    path = _drafts_dir() / f"{_DRAFT_PREFIX}{sid}.json"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        obj = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as e:
        logger.warning("草稿损坏已删除: %s (%s)", path, e)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    if not isinstance(obj, dict):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return obj


def list_available_drafts() -> list[str]:
    """列出所有可用草稿对应的 session_id（按文件名排序）。"""
    base = _drafts_dir()
    if not base.is_dir():
        return []
    out: list[str] = []
    for p in sorted(base.glob(f"{_DRAFT_PREFIX}*.json")):
        name = p.name
        sid = name[len(_DRAFT_PREFIX) : -len(".json")]
        if sid and _SESSION_ID_RE.fullmatch(sid):
            out.append(sid)
    return out
