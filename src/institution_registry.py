"""
机构注册表 — V10.2 数据飞轮地基。

持久化存储：{MEMORY_ROOT}/institutions.json
每条记录：{id, canonical_name, aliases, created_at, session_count}

核心功能：
1. fuzzy_match   — 输入任意写法，返回最相似的已知机构（阈值 0.8）
2. register      — 注册新机构或为已有机构添加别名
3. resolve       — 统一入口：输入名称 → 返回 (institution_id, canonical_name)
4. get_all       — 列出所有机构

设计原则：
- 失败静默，不影响主流程
- 原子写入，防崩溃损坏
- 无 Streamlit 依赖，纯数据层
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_FUZZY_THRESHOLD = 0.80   # 相似度 ≥ 此值视为同一机构
_REGISTRY_FILENAME = "institutions.json"


def _get_registry_path() -> Path:
    """从 runtime_paths 获取 memory root，拼接注册表路径。"""
    try:
        from runtime_paths import get_memory_root
        return Path(get_memory_root()) / _REGISTRY_FILENAME
    except Exception:
        return Path(".") / _REGISTRY_FILENAME


def _load_registry(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("institution_registry: 读取失败，返回空列表（%s）", exc)
        return []


def _save_registry(path: Path, records: list[dict]) -> bool:
    """原子写入。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(records, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(serialized)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception as exc:
        logger.warning("institution_registry: 写入失败（%s）", exc)
        return False


def _similarity(a: str, b: str) -> float:
    """不区分大小写的序列相似度。"""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _best_match(name: str, records: list[dict]) -> tuple[Optional[dict], float]:
    """返回最相似的机构记录及其得分。"""
    best_record: Optional[dict] = None
    best_score = 0.0
    for rec in records:
        # 检查 canonical_name 和所有 aliases
        candidates = [rec["canonical_name"]] + rec.get("aliases", [])
        for cand in candidates:
            score = _similarity(name, cand)
            if score > best_score:
                best_score = score
                best_record = rec
    return best_record, best_score


# ── 公开 API ─────────────────────────────────────────────────────────────────

def fuzzy_match(name: str) -> Optional[dict]:
    """
    输入任意机构写法，返回相似度 ≥ 阈值的已知机构记录，否则返回 None。

    返回字段：{id, canonical_name, aliases, session_count, similarity}
    """
    if not name or not name.strip():
        return None
    path = _get_registry_path()
    records = _load_registry(path)
    rec, score = _best_match(name.strip(), records)
    if rec and score >= _FUZZY_THRESHOLD:
        return {**rec, "similarity": round(score, 3)}
    return None


def register(canonical_name: str, alias: Optional[str] = None) -> dict:
    """
    注册新机构，或为已有机构添加别名。

    - 若 canonical_name 与已有机构高度相似 → 只添加 alias，不新建
    - 否则新建机构记录
    返回最终的机构记录 dict。
    """
    canonical_name = (canonical_name or "").strip()
    if not canonical_name:
        raise ValueError("canonical_name 不能为空")

    path = _get_registry_path()
    records = _load_registry(path)

    # 先检查是否已存在（精确匹配 canonical）
    for rec in records:
        if rec["canonical_name"].lower() == canonical_name.lower():
            if alias and alias.strip() and alias.strip() not in rec.get("aliases", []):
                rec.setdefault("aliases", []).append(alias.strip())
                _save_registry(path, records)
            return rec

    # 模糊匹配：高相似度时只加别名
    best, score = _best_match(canonical_name, records)
    if best and score >= _FUZZY_THRESHOLD:
        if canonical_name not in best.get("aliases", []):
            best.setdefault("aliases", []).append(canonical_name)
        if alias and alias.strip() and alias.strip() not in best.get("aliases", []):
            best["aliases"].append(alias.strip())
        _save_registry(path, records)
        return best

    # 新建
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    new_rec = {
        "id": str(uuid.uuid4()),
        "canonical_name": canonical_name,
        "aliases": [alias.strip()] if alias and alias.strip() else [],
        "created_at": now,
        "session_count": 0,
    }
    records.append(new_rec)
    _save_registry(path, records)
    logger.info("institution_registry: 新建机构「%s」（id=%s）", canonical_name, new_rec["id"])
    return new_rec


def resolve(name: str) -> tuple[str, str]:
    """
    统一入口：输入任意名称 → (institution_id, canonical_name)。

    - 高相似度匹配到已有机构 → 返回已有 id/canonical，并将输入写入 aliases
    - 否则新建机构并返回新 id/canonical
    """
    name = (name or "").strip()
    if not name:
        return "", ""

    path = _get_registry_path()
    records = _load_registry(path)
    best, score = _best_match(name, records)

    if best and score >= _FUZZY_THRESHOLD:
        # 将当前写法写入 aliases（如不存在）
        if name not in [best["canonical_name"]] + best.get("aliases", []):
            best.setdefault("aliases", []).append(name)
            _save_registry(path, records)
        return best["id"], best["canonical_name"]

    # 新建
    rec = register(name)
    return rec["id"], rec["canonical_name"]


def increment_session_count(institution_id: str) -> None:
    """锁定时调用，为机构会话计数 +1。"""
    path = _get_registry_path()
    records = _load_registry(path)
    for rec in records:
        if rec["id"] == institution_id:
            rec["session_count"] = rec.get("session_count", 0) + 1
            _save_registry(path, records)
            return


def get_all() -> list[dict]:
    """返回全部机构记录（列表副本）。"""
    return list(_load_registry(_get_registry_path()))


def get_by_id(institution_id: str) -> Optional[dict]:
    """按 id 精确查找。"""
    for rec in _load_registry(_get_registry_path()):
        if rec["id"] == institution_id:
            return rec
    return None
