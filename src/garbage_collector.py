"""
V4.0 / V6.2 幽灵清道夫：静默删除过期中间 JSON，不碰原始录音与 HTML 成品。（发版与 build_release.CURRENT_VERSION 对齐）
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 仅删除文件名以这些后缀结尾的 JSON（系统生成的中间产物）
GC_JSON_SUFFIXES: tuple[str, ...] = (
    "_transcription.json",
    "_analysis_report.json",
)

# 绝不按「后缀」删除的原始媒体与成品报告（双保险）
_PROTECTED_SUFFIXES: tuple[str, ...] = (
    ".html",
    ".m4a",
    ".mp3",
    ".wav",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".webm",
    ".flac",
    ".ogg",
)

GC_MAX_AGE_SEC = 7 * 24 * 3600


def _is_gc_candidate(path: Path) -> bool:
    if not path.is_file():
        return False
    name_lower = path.name.lower()
    for prot in _PROTECTED_SUFFIXES:
        if name_lower.endswith(prot):
            return False
    if not name_lower.endswith(".json"):
        return False
    return any(name_lower.endswith(suf) for suf in GC_JSON_SUFFIXES)


def sweep_stale_intermediate_json(workspace_root: Path | str) -> int:
    """
    递归扫描 workspace_root，删除 mtime 早于 7 天的 *_transcription.json / *_analysis_report.json。
    返回删除文件数；所有错误吞掉并打日志，不影响主流程。
    """
    root = Path(workspace_root).expanduser()
    if not root.is_dir():
        return 0
    now = time.time()
    deleted = 0
    try:
        for p in root.rglob("*"):
            try:
                if not _is_gc_candidate(p):
                    continue
                mtime = p.stat().st_mtime
                if now - mtime <= GC_MAX_AGE_SEC:
                    continue
                p.unlink()
                deleted += 1
                logger.info("GC 已删除过期中间文件: %s", p)
            except OSError as e:
                logger.warning("GC 跳过 %s: %s", p, e)
    except OSError as e:
        logger.warning("GC 扫描失败 %s: %s", root, e)
    return deleted
