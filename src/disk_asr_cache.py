"""
磁盘级 ASR 缓存 — V8.0 新增。

按文件内容 MD5 存取，缓存跨 Streamlit session 持久化，实现"一次转写，永久免费秒开"。
缓存文件存于 {writable_app_root}/.asr_cache/{md5_hash}.json。

原子写入（tmp + os.replace），防止写入中途崩溃产生损坏缓存。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from runtime_paths import get_writable_app_root, get_asr_cache_root


def get_default_cache_dir() -> Path:
    """
    ASR 磁盘缓存目录（V10.0 升级）。

    优先读取 CACHE_ROOT 环境变量（共享网盘多人协作场景）；
    未设置时为可写根下的 `.asr_cache` 目录，行为与 V9.x 完全一致。
    """
    return get_asr_cache_root()


def load_asr_cache(
    file_hash: str,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any] | None:
    """
    按 MD5 哈希加载缓存条目。
    命中返回 {"words": [...], "plain": "..."}；未命中返回 None。
    """
    d = cache_dir if cache_dir is not None else get_default_cache_dir()
    cache_file = Path(d) / f"{file_hash}.json"
    if not cache_file.is_file():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "words" in data and "plain" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def save_asr_cache(
    file_hash: str,
    words: list[dict[str, Any]],
    plain: str,
    *,
    cache_dir: Path | None = None,
) -> None:
    """
    将转写结果写入磁盘缓存（原子写入，防崩溃损坏）。
    cache_dir 不存在时自动创建。
    """
    d = Path(cache_dir) if cache_dir is not None else get_default_cache_dir()
    d.mkdir(parents=True, exist_ok=True)

    payload = {"words": words, "plain": plain}
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)

    # 原子写入：先写临时文件，再 os.replace
    fd, tmp_path = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
        os.replace(tmp_path, d / f"{file_hash}.json")
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
