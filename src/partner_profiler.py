"""
投资人个人画像模块 — V10.3.1 P3.2（审计修复版）

按 (institution_id, investor_name) 维度聚合 analytics 数据，
构建 Partner 级别的投资偏好画像。

设计原则：
- 纯数据统计，无 ML 依赖
- 静默跳过解析失败的文件
- 空 investor_name 不纳入统计（调用方传入空字符串时直接返回空画像，
  防止把所有"无投资人记录"的 session 错误聚合在一起）
- 内置轻量扫描缓存：同一进程内多次查询同一 workspace 只扫描一次磁盘
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── 轻量扫描缓存 ─────────────────────────────────────────────────────────────
# 结构：{workspace_root_str → {institution_id → [record, ...]}}
# 失效策略：workspace 目录下最新文件的 mtime 变化时清空
_SCAN_CACHE: dict[str, dict[str, list[dict]]] = {}
_SCAN_CACHE_MTIME: dict[str, float] = {}  # {workspace_root_str → last_known_mtime}


def _get_workspace_mtime(workspace_root: Path) -> float:
    """快速检测 workspace 内最新 analytics 文件的修改时间，用于缓存失效。"""
    latest = 0.0
    for p in workspace_root.rglob("*_analytics.json"):
        try:
            mtime = p.stat().st_mtime
            if mtime > latest:
                latest = mtime
        except OSError:
            continue
    return latest


def _load_all_analytics(workspace_root: Path) -> dict[str, list[dict]]:
    """
    全量扫描 workspace_root 下所有 *_analytics.json，
    按 institution_id 分组返回。带内存缓存，文件无变化时不重复扫描。
    """
    ws_key = str(workspace_root.resolve())
    current_mtime = _get_workspace_mtime(workspace_root)

    # 缓存命中：workspace 没有新文件
    if (ws_key in _SCAN_CACHE and
            _SCAN_CACHE_MTIME.get(ws_key, -1.0) >= current_mtime):
        return _SCAN_CACHE[ws_key]

    # 缓存失效：重新扫描
    grouped: dict[str, list[dict]] = {}
    for p in workspace_root.rglob("*_analytics.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            iid = (data.get("institution_id") or "").strip()
            if iid:
                grouped.setdefault(iid, []).append(data)
        except (json.JSONDecodeError, OSError):
            continue

    _SCAN_CACHE[ws_key] = grouped
    _SCAN_CACHE_MTIME[ws_key] = current_mtime
    return grouped


def _iter_analytics(workspace_root: Path, institution_id: str) -> list[dict]:
    """返回属于指定 institution 的所有 analytics 记录（带缓存）。"""
    return _load_all_analytics(workspace_root).get(institution_id, [])


def invalidate_cache(workspace_root: Path | str | None = None) -> None:
    """
    手动失效缓存。写入新 analytics 文件后可调用，强制下次重新扫描。
    workspace_root=None 时清空全部缓存。
    """
    if workspace_root is None:
        _SCAN_CACHE.clear()
        _SCAN_CACHE_MTIME.clear()
    else:
        ws_key = str(Path(workspace_root).resolve())
        _SCAN_CACHE.pop(ws_key, None)
        _SCAN_CACHE_MTIME.pop(ws_key, None)


# ── 公开 API ──────────────────────────────────────────────────────────────────

def build_partner_profile(
    institution_id: str,
    investor_name: str,
    workspace_root: Path | str,
) -> dict[str, Any]:
    """
    构建指定 partner 的投资人画像。

    参数：
      institution_id  : 机构唯一标识
      investor_name   : 投资人姓名（空字符串时返回空画像，不聚合无名 session）
      workspace_root  : analytics JSON 文件所在根目录

    返回：
      institution_id  : str
      investor_name   : str
      total_sessions  : int
      avg_score       : float
      top_risk_types  : list[tuple[str, int]]  — 按出现次数降序
      score_trend     : list[float]            — 按 generated_at 升序的得分列表
    """
    workspace_root = Path(workspace_root)

    # 空姓名防卫：拒绝聚合（避免把所有无名 session 错误归入同一画像）
    _target = (investor_name or "").strip()
    if not _target:
        return {
            "institution_id": institution_id,
            "investor_name": investor_name,
            "total_sessions": 0,
            "avg_score": 0.0,
            "top_risk_types": [],
            "score_trend": [],
        }

    all_records = _iter_analytics(workspace_root, institution_id)
    sessions = [
        r for r in all_records
        if (r.get("investor_name") or "").strip() == _target
    ]

    if not sessions:
        return {
            "institution_id": institution_id,
            "investor_name": investor_name,
            "total_sessions": 0,
            "avg_score": 0.0,
            "top_risk_types": [],
            "score_trend": [],
        }

    # 按时间排序（generated_at ISO 字符串可直接比较）
    # 二级排序用 session_id 保证同时间戳时顺序稳定
    sessions_sorted = sorted(
        sessions,
        key=lambda s: (s.get("generated_at", ""), s.get("session_id", "")),
    )

    # 平均分
    scores = [s.get("total_score", 0) for s in sessions]
    avg_score = sum(scores) / len(scores)

    # 风险类型统计
    risk_counter: Counter[str] = Counter()
    for s in sessions:
        rtc = s.get("risk_type_counts") or {}
        for rtype, cnt in rtc.items():
            risk_counter[rtype] += cnt if isinstance(cnt, int) else 0

    top_risk_types = risk_counter.most_common()

    return {
        "institution_id": institution_id,
        "investor_name": investor_name,
        "total_sessions": len(sessions),
        "avg_score": round(avg_score, 2),
        "top_risk_types": top_risk_types,
        "score_trend": [s.get("total_score", 0) for s in sessions_sorted],
    }


def list_partners_for_institution(
    institution_id: str,
    workspace_root: Path | str,
) -> list[str]:
    """
    返回指定机构下所有出现过的投资人姓名（去重，排除空字符串）。

    参数：
      institution_id : 机构唯一标识
      workspace_root : analytics JSON 文件所在根目录

    返回：
      list[str] — 去重后的 investor_name 列表（已排序）
    """
    workspace_root = Path(workspace_root)
    all_records = _iter_analytics(workspace_root, institution_id)

    names: set[str] = set()
    for r in all_records:
        name = (r.get("investor_name") or "").strip()
        if name:
            names.add(name)

    return sorted(names)
