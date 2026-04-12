"""
投资人个人画像模块 — V10.3 P3.2

按 (institution_id, investor_name) 维度聚合 analytics 数据，
构建 Partner 级别的投资偏好画像。

设计原则：
- 纯数据统计，无 ML 依赖
- 静默跳过解析失败的文件
- 空 investor_name 不纳入统计
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _iter_analytics(workspace_root: Path, institution_id: str) -> list[dict]:
    """扫描 workspace_root 下所有 *_analytics.json，返回属于指定 institution 的记录。"""
    records: list[dict] = []
    for p in workspace_root.rglob("*_analytics.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if (data.get("institution_id") or "").strip() == institution_id:
                records.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return records


def build_partner_profile(
    institution_id: str,
    investor_name: str,
    workspace_root: Path | str,
) -> dict[str, Any]:
    """
    构建指定 partner 的投资人画像。

    参数：
      institution_id  : 机构唯一标识
      investor_name   : 投资人姓名
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
    all_records = _iter_analytics(workspace_root, institution_id)

    # 过滤出该 partner 的 session
    sessions = [
        r for r in all_records
        if (r.get("investor_name") or "").strip() == investor_name
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

    # 按时间排序（generated_at 字段，字符串 ISO 格式可直接比较）
    sessions_sorted = sorted(sessions, key=lambda s: s.get("generated_at", ""))

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
      list[str] — 去重后的 investor_name 列表
    """
    workspace_root = Path(workspace_root)
    all_records = _iter_analytics(workspace_root, institution_id)

    names: set[str] = set()
    for r in all_records:
        name = (r.get("investor_name") or "").strip()
        if name:
            names.add(name)

    return sorted(names)
