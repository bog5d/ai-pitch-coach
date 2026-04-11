"""
跨公司匿名基准分析引擎 — V10.0 数据飞轮终态。

扫描 workspace 中所有 *_analytics.json 文件，聚合匿名统计数据，
让每家被访公司都能与行业基准对比：
"在所有被访者中，'严重'风险点出现率 68%，均分 73.4 分"

设计原则：
- 完全匿名：聚合时只统计计数/比率，不暴露具体 company_id 或 speaker_id
- 失败静默：单文件损坏跳过，不中断整体扫描
- 无外部依赖：仅标准库 + schema
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

_SCORE_BUCKETS = [
    ("0-59", 0, 59),
    ("60-74", 60, 74),
    ("75-89", 75, 89),
    ("90-100", 90, 100),
]


def scan_analytics_files(workspace_root: Path | str) -> list[dict]:
    """
    递归扫描 workspace_root 下所有 *_analytics.json 文件。

    跳过：不存在的目录、非 analytics 文件、损坏 JSON。
    返回：解析后的 dict 列表（空目录返回 []）。
    """
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
            logger.debug("scan_analytics_files: 跳过损坏文件 %s（%s）", p, exc)
    return results


def build_benchmark(analytics_list: list[dict]) -> dict:
    """
    从 analytics dict 列表聚合匿名基准统计数据。

    返回字段：
      total_sessions: 总分析场次数
      total_companies: 唯一公司数（按 company_id 去重）
      avg_score: 平均综合得分
      score_distribution: 四个档位分布列表
      risk_type_frequency: 各严重程度的风险点总出现次数
      refinement_rate: 有精炼的场次占比（0.0~1.0）
      truncation_rate: 阶段一截断的场次占比（0.0~1.0）
    """
    empty = {
        "total_sessions": 0,
        "total_companies": 0,
        "avg_score": 0.0,
        "score_distribution": [{"range": r, "count": 0} for r, _, _ in _SCORE_BUCKETS],
        "risk_type_frequency": {"严重": 0, "一般": 0, "轻微": 0},
        "refinement_rate": 0.0,
        "truncation_rate": 0.0,
    }
    if not analytics_list:
        return empty

    n = len(analytics_list)
    companies: set[str] = set()
    scores: list[float] = []
    bucket_counts: Counter[str] = Counter()
    risk_freq: Counter[str] = Counter()
    has_refinement = 0
    has_truncation = 0

    for item in analytics_list:
        cid = (item.get("company_id") or "").strip()
        if cid:
            companies.add(cid)

        score = item.get("total_score")
        if isinstance(score, (int, float)):
            s = float(score)
            scores.append(s)
            for bucket_name, lo, hi in _SCORE_BUCKETS:
                if lo <= s <= hi:
                    bucket_counts[bucket_name] += 1
                    break

        rb = item.get("risk_breakdown") or {}
        for level in ("严重", "一般", "轻微"):
            level_data = rb.get(level) or {}
            cnt = level_data.get("count", 0)
            if isinstance(cnt, int):
                risk_freq[level] += cnt

        if int(item.get("refinement_count", 0)) > 0:
            has_refinement += 1
        if item.get("stage1_truncated") is True:
            has_truncation += 1

    return {
        "total_sessions": n,
        "total_companies": len(companies),
        "avg_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        "score_distribution": [
            {"range": r, "count": bucket_counts.get(r, 0)}
            for r, _, _ in _SCORE_BUCKETS
        ],
        "risk_type_frequency": {
            "严重": risk_freq["严重"],
            "一般": risk_freq["一般"],
            "轻微": risk_freq["轻微"],
        },
        "refinement_rate": round(has_refinement / n, 4),
        "truncation_rate": round(has_truncation / n, 4),
    }
