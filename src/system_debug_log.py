"""
V4.0 / V6.2 / V7.0 系统诊断日志：统一写入可写根目录下 debug.log，供一键下载与远程排障。（发版与 build_release.CURRENT_VERSION 对齐）
"""
from __future__ import annotations

import logging
from pathlib import Path

from runtime_paths import get_writable_app_root

_CONFIGURED = False

LOGGERS_TO_FILE = (
    "llm_judge",
    "transcriber",
    "report_builder",
    "job_pipeline",
    "garbage_collector",
    "retry_policy",
    "ai_pitch_coach",
)


def get_debug_log_path() -> Path:
    return get_writable_app_root() / "debug.log"


def setup_file_logging() -> Path:
    """幂等：为指定 logger 挂载 FileHandler（UTF-8）。"""
    global _CONFIGURED
    path = get_debug_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.touch()

    if _CONFIGURED:
        return path
    _CONFIGURED = True

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(path, encoding="utf-8", mode="a")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    root_pkg = logging.getLogger("ai_pitch_coach")
    root_pkg.setLevel(logging.DEBUG)
    root_pkg.addHandler(fh)

    for name in LOGGERS_TO_FILE:
        lg = logging.getLogger(name)
        if not any(isinstance(h, logging.FileHandler) for h in lg.handlers):
            lg.addHandler(fh)
        lg.setLevel(logging.DEBUG)

    return path


def read_debug_log_bytes(max_bytes: int = 2_000_000) -> bytes:
    p = get_debug_log_path()
    if not p.is_file():
        return b"(debug.log not found)\n"
    data = p.read_bytes()
    if len(data) > max_bytes:
        tail = data[-max_bytes:]
        return tail + b"\n... (truncated tail only)\n"
    return data
