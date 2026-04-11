"""
投资机构画像引擎 — V10.2 数据飞轮。

从 workspace 下所有 *_analytics.json 中，按 institution_id 聚合：
- 历次访谈得分分布
- 高频风险类型（该机构最爱追的问题维度）
- 最致命问题（扣分最高的风险点描述）
- 涉及公司数 / 场次数

设计原则：
- 纯数据计算，无 Streamlit 依赖
- 单文件损坏跳过，不中断整体扫描
- 失败静默返回空结构
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _scan_analytics(workspace_root: Path) -> list[dict]:
    """递归扫描 workspace 下所有 *_analytics.json，返回 dict 列表。"""
    root = Path(workspace_root)
    if not root.is_dir():
        return []
    results: list[dict] = []
    for p in root.rglob("*_analytics.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                results.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("institution_profiler: 跳过损坏文件 %s（%s）", p, exc)
    return results


def build_institution_profile(
    institution_id: str,
    workspace_root: Path | str,
) -> dict:
    """
    按 institution_id 聚合所有相关 analytics，返回机构画像。

    返回字段：
      institution_id        : str
      canonical_name        : str（来自 analytics 的 institution_canonical）
      total_sessions        : int  总场次
      total_companies       : int  涉及不同公司数
      avg_score             : float 平均综合得分
      score_trend           : list[float] 按时间升序的得分列表（最多 20 条）
      top_risk_types        : list[{risk_type, count, ratio}]  高频风险维度（前5）
      killer_questions      : list[str]  历次扣分最高的问题描述（最多5条，去重）
      avg_risk_count        : float 每场平均风险点数
      severe_risk_ratio     : float 严重风险占所有风险比率
    """
    workspace_root = Path(workspace_root)
    all_analytics = _scan_analytics(workspace_root)

    # 筛选本机构
    sessions = [
        a for a in all_analytics
        if a.get("institution_id", "") == institution_id
    ]

    if not sessions:
        return {
            "institution_id": institution_id,
            "canonical_name": "",
            "total_sessions": 0,
            "total_companies": 0,
            "avg_score": 0.0,
            "score_trend": [],
            "top_risk_types": [],
            "killer_questions": [],
            "avg_risk_count": 0.0,
            "severe_risk_ratio": 0.0,
        }

    # 按时间排序
    def _ts(s: dict) -> str:
        return s.get("locked_at") or s.get("generated_at") or ""

    sessions_sorted = sorted(sessions, key=_ts)

    canonical_name = ""
    for s in sessions_sorted:
        cn = s.get("institution_canonical", "")
        if cn:
            canonical_name = cn
            break

    scores = [s["total_score"] for s in sessions_sorted if "total_score" in s]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    score_trend = scores[-20:]  # 最近 20 场

    companies = {s.get("company_id", "") for s in sessions_sorted if s.get("company_id")}

    # 风险类型统计
    risk_type_counter: Counter[str] = Counter()
    total_risk_count = 0
    severe_count = 0
    for s in sessions_sorted:
        rt_counts = s.get("risk_type_counts", {})
        for rt, cnt in rt_counts.items():
            risk_type_counter[rt] += cnt
            total_risk_count += cnt
        rb = s.get("risk_breakdown", {})
        severe_count += rb.get("严重", {}).get("count", 0)

    top_risk_types = []
    for rt, cnt in risk_type_counter.most_common(5):
        ratio = round(cnt / total_risk_count, 3) if total_risk_count else 0.0
        top_risk_types.append({"risk_type": rt, "count": cnt, "ratio": ratio})

    avg_risk_count = round(total_risk_count / len(sessions_sorted), 1) if sessions_sorted else 0.0
    all_risks_total = sum(
        s.get("total_risk_count", 0) for s in sessions_sorted
    )
    severe_risk_ratio = round(severe_count / all_risks_total, 3) if all_risks_total else 0.0

    # 杀手问题：从 analytics 的 killer_questions 字段（如有）聚合，去重取前5
    killer_set: list[str] = []
    seen: set[str] = set()
    for s in reversed(sessions_sorted):  # 最近优先
        for kq in s.get("killer_questions", []):
            if kq and kq not in seen:
                seen.add(kq)
                killer_set.append(kq)
                if len(killer_set) >= 5:
                    break
        if len(killer_set) >= 5:
            break

    return {
        "institution_id": institution_id,
        "canonical_name": canonical_name,
        "total_sessions": len(sessions_sorted),
        "total_companies": len(companies),
        "avg_score": avg_score,
        "score_trend": score_trend,
        "top_risk_types": top_risk_types,
        "killer_questions": killer_set,
        "avg_risk_count": avg_risk_count,
        "severe_risk_ratio": severe_risk_ratio,
    }


def list_all_institution_profiles(workspace_root: Path | str) -> list[dict]:
    """
    扫描 workspace，返回所有出现过的机构的画像列表（按场次降序）。
    用于 Dashboard Tab5 全局机构排行。
    """
    workspace_root = Path(workspace_root)
    all_analytics = _scan_analytics(workspace_root)

    # 收集所有 institution_id
    institution_ids: set[str] = set()
    for a in all_analytics:
        iid = a.get("institution_id", "").strip()
        if iid:
            institution_ids.add(iid)

    profiles = []
    for iid in institution_ids:
        try:
            p = build_institution_profile(iid, workspace_root)
            profiles.append(p)
        except Exception as exc:
            logger.warning("institution_profiler: 跳过机构 %s（%s）", iid, exc)

    profiles.sort(key=lambda x: x["total_sessions"], reverse=True)
    return profiles
