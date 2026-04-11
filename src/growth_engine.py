"""
个人成长引擎 — V10.1

三大核心功能：
1. get_person_sessions  — 从 workspace 筛选指定人的历史 analytics 记录（时间升序）
2. build_growth_curve   — 个人历次得分成长曲线 + 趋势判断
3. build_weakness_radar — 弱点维度雷达图数据（与行业基准对比）
4. get_practice_recommendations — "今天要重点练什么"（近期加权推荐）

设计原则：
- 纯数据计算，不依赖 Streamlit / session_state
- 失败静默，不抛异常中断 Dashboard 渲染
- 近期会话权重更高（最近3次 ×2 加权）
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

# 近期会话（最新 N 次）权重倍数；4x 意味着最近3次出现1次 = 历史3次出现4次
_RECENT_N = 3
_RECENT_WEIGHT = 4

# 雷达图维度定义
_RADAR_DIMS = ["综合得分", "严重风险率", "一般风险率", "AI纠错率", "精炼覆盖率"]

# 各风险类型的练习建议文案
_PRACTICE_TIPS: dict[str, str] = {
    "估值回避": "准备 3 套估值区间话术，练习主动抛出锚定数字并给出逻辑支撑",
    "数据含糊": "核心财务/运营数据提前背熟，练习用「精确数字+来源+趋势」三段式作答",
    "逻辑断裂": "梳理商业逻辑链（痛点→方案→壁垒→收益），练习用 MECE 结构陈述",
    "口径偏离": "对照公司 QA 库逐条校对，练习在压力追问下保持口径稳定",
    "数据矛盾": "建立数据字典，同一场景同一数字只用一个权威版本",
    "表达模糊": "练习「结论先行，3 秒内说清核心」，避免绕弯子",
    "主动防御不足": "模拟最难追问场景，提前设计防守型开场句",
    "竞品回避": "准备竞品对比表，练习正面承认差距并快速切换优势视角",
    "案例缺失": "准备 5 个标杆客户故事，练习用 STAR 结构（情境-任务-行动-结果）讲述",
}
_DEFAULT_TIP = "整理该类问题的历次翻车片段，逐条模拟改进话术并与 AI 教练对练"


# ── 辅助 ─────────────────────────────────────────────────────────────────────

def _load_analytics_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


# ── 1. 筛选指定人的会话 ───────────────────────────────────────────────────────

def get_person_sessions(
    workspace_root: Path | str,
    company_id: str,
    interviewee: str,
) -> list[dict]:
    """
    从 workspace_root 递归扫描所有 *_analytics.json，
    筛选 company_id 和 interviewee 均匹配的记录，按时间升序返回。
    """
    root = Path(workspace_root)
    if not root.is_dir():
        return []

    results: list[dict] = []
    for p in root.rglob("*_analytics.json"):
        item = _load_analytics_file(p)
        if item is None:
            continue
        if item.get("company_id", "").strip() != company_id.strip():
            continue
        if item.get("interviewee", "").strip() != interviewee.strip():
            continue
        results.append(item)

    return sorted(results, key=lambda x: x.get("generated_at", ""))


# ── 2. 成长曲线 ──────────────────────────────────────────────────────────────

def build_growth_curve(sessions: list[dict]) -> dict:
    """
    根据历次 analytics 数据构建个人成长曲线。

    返回字段：
      dates             : ISO 时间戳列表（与 scores 等长）
      scores            : 历次综合得分列表
      score_delta       : 最新得分 − 最早得分（正值=进步）
      avg_score         : 历次平均分
      trend             : 上升 / 下降 / 平稳 / 首次 / 暂无数据
      avg_severe_per_session : 平均每场严重风险点数
      severe_counts     : 各场严重风险点数列表
    """
    if not sessions:
        return {
            "dates": [], "scores": [], "score_delta": 0,
            "avg_score": 0.0, "trend": "暂无数据",
            "avg_severe_per_session": 0.0, "severe_counts": [],
        }

    dates = [s.get("generated_at", "") for s in sessions]
    scores = [int(s.get("total_score", 0)) for s in sessions]
    severe_counts = [
        int((s.get("risk_breakdown") or {}).get("严重", {}).get("count", 0))
        for s in sessions
    ]

    n = len(scores)
    avg_score = round(sum(scores) / n, 1)
    score_delta = scores[-1] - scores[0] if n > 1 else 0

    if n == 1:
        trend = "首次"
    elif n >= 2:
        # 用线性回归斜率判断趋势：比单纯看首尾更稳健
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(scores) / n
        num = sum((xs[i] - x_mean) * (scores[i] - y_mean) for i in range(n))
        den = sum((xs[i] - x_mean) ** 2 for i in range(n))
        slope = num / den if den != 0 else 0
        if slope > 0.5:
            trend = "上升"
        elif slope < -0.5:
            trend = "下降"
        else:
            trend = "平稳"
    else:
        trend = "暂无数据"

    return {
        "dates": dates,
        "scores": scores,
        "score_delta": score_delta,
        "avg_score": avg_score,
        "trend": trend,
        "avg_severe_per_session": round(sum(severe_counts) / n, 2),
        "severe_counts": severe_counts,
    }


# ── 3. 弱点雷达图 ────────────────────────────────────────────────────────────

def _normalize(value: float, max_val: float = 100.0) -> float:
    """归一化到 0-100 之间（雷达图统一量纲）。"""
    return round(min(max(value, 0.0), max_val), 1)


def build_weakness_radar(sessions: list[dict], benchmark: dict) -> dict:
    """
    构建个人弱点维度雷达图数据，与行业基准对比。

    dimensions     : 维度名称列表
    person_values  : 个人各维度值（0-100）
    benchmark_values: 行业均值各维度值（0-100）
    top_weakness_types: 该人最高频的风险类型（用于文字标注）
    """
    zero_result = {
        "dimensions": _RADAR_DIMS,
        "person_values": [0.0] * len(_RADAR_DIMS),
        "benchmark_values": [0.0] * len(_RADAR_DIMS),
        "top_weakness_types": [],
    }

    if not sessions:
        return zero_result

    n = len(sessions)

    # ── 个人维度 ────────────────────────────────────────────────────────────
    avg_score = sum(s.get("total_score", 0) for s in sessions) / n

    total_risk = sum(s.get("total_risk_count", 0) for s in sessions) or 1
    severe_total = sum(
        (s.get("risk_breakdown") or {}).get("严重", {}).get("count", 0) for s in sessions
    )
    general_total = sum(
        (s.get("risk_breakdown") or {}).get("一般", {}).get("count", 0) for s in sessions
    )
    refinement_total = sum(s.get("refinement_count", 0) for s in sessions)
    ai_miss_total = sum(s.get("ai_miss_count", 0) for s in sessions)

    # 严重风险率：严重数/总风险点（越低越好，用 100-x% 转为正向）
    severe_rate = severe_total / total_risk * 100
    general_rate = general_total / total_risk * 100
    # AI纠错率：ai_miss（人工补录）/ 总风险点，代表AI被你抓到漏网的比例
    ai_miss_rate = ai_miss_total / total_risk * 100
    # 精炼覆盖率：有精炼的风险点/总风险点
    refinement_rate = refinement_total / total_risk * 100

    # 雷达图取"越高越好"的方向：
    # 综合得分直接用，严重/一般风险率转为"清洁度"（100-rate，越低越好→越高越好）
    person_values = [
        _normalize(avg_score),                       # 综合得分（0-100）
        _normalize(100 - severe_rate),               # 严重清洁度（高=好）
        _normalize(100 - general_rate),              # 一般清洁度（高=好）
        _normalize(min(ai_miss_rate * 5, 100)),      # AI纠错力（人工补录越多越能发现问题，放大5倍）
        _normalize(min(refinement_rate * 3, 100)),   # 精炼覆盖率（放大3倍让差异可见）
    ]

    # ── 行业基准维度 ─────────────────────────────────────────────────────────
    bm_avg_score = float(benchmark.get("avg_score", 70))
    bm_rf = benchmark.get("risk_type_frequency") or {}
    bm_sessions = int(benchmark.get("total_sessions", 1)) or 1
    # 行业基准只有汇总数，用近似计算
    bm_severe = bm_rf.get("严重", 0)
    bm_general = bm_rf.get("一般", 0)
    bm_total_risk = bm_severe + bm_general + bm_rf.get("轻微", 0) or 1
    bm_severe_rate = bm_severe / bm_total_risk * 100
    bm_general_rate = bm_general / bm_total_risk * 100
    bm_refinement_rate = float(benchmark.get("refinement_rate", 0.3)) * 100

    benchmark_values = [
        _normalize(bm_avg_score),
        _normalize(100 - bm_severe_rate),
        _normalize(100 - bm_general_rate),
        _normalize(30),                                    # 行业 AI 纠错力基准（固定参考值）
        _normalize(min(bm_refinement_rate * 3, 100)),
    ]

    # ── 最高频弱点类型 ───────────────────────────────────────────────────────
    type_counter: Counter[str] = Counter()
    for s in sessions:
        for rt, cnt in (s.get("risk_type_counts") or {}).items():
            if rt:
                type_counter[rt] += int(cnt)
    top_weakness = [rt for rt, _ in type_counter.most_common(3)]

    return {
        "dimensions": _RADAR_DIMS,
        "person_values": person_values,
        "benchmark_values": benchmark_values,
        "top_weakness_types": top_weakness,
    }


# ── 4. 今天要重点练什么 ──────────────────────────────────────────────────────

def get_practice_recommendations(
    sessions: list[dict],
    top_n: int = 3,
) -> list[dict]:
    """
    基于历史 risk_type_counts，近期会话加权，推荐最需要练的 top_n 个场景。

    每条推荐包含：
      risk_type : 风险类型名称
      count     : 加权出现次数
      suggestion: 配套练习建议文案
    """
    if not sessions:
        return []

    # 近期 _RECENT_N 条加权
    if len(sessions) <= _RECENT_N:
        weights = [_RECENT_WEIGHT] * len(sessions)
    else:
        weights = [1] * (len(sessions) - _RECENT_N) + [_RECENT_WEIGHT] * _RECENT_N

    type_counter: Counter[str] = Counter()
    for session, w in zip(sessions, weights):
        for rt, cnt in (session.get("risk_type_counts") or {}).items():
            rt = (rt or "").strip()
            if rt:
                type_counter[rt] += int(cnt) * w

    if not type_counter:
        return []

    recs = []
    for rt, weighted_count in type_counter.most_common(top_n):
        recs.append({
            "risk_type": rt,
            "count": weighted_count,
            "suggestion": _PRACTICE_TIPS.get(rt, _DEFAULT_TIP),
        })
    return recs
