"""
融资成功率预测模型 — V10.3 P3.1

基于历史 analytics 数据（得分、风险分布、已知结果）
用启发式统计模型预估本轮融资成功概率。

设计原则：
- 纯数据统计，无 ML 库依赖
- 有历史结果（已成功/未推进）时：以基率校正预测
- 无历史结果时：完全基于得分和风险特征
- 置信度随 session 数量增加
- 失败静默返回 None probability

公式（线性启发式）：
  base_score = avg_total_score / 100           (0-1)
  severe_penalty = severe_ratio * 0.3          (扣减)
  outcome_boost = success_ratio - failed_ratio (历史结果加成)
  raw = base_score - severe_penalty + outcome_boost * 0.2
  probability = clip(raw, 0.05, 0.95)          (边界限制)
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 置信度权重：session 数量对应的置信等级
_CONFIDENCE_TIERS = [
    (1, "low"),
    (3, "medium"),
    (6, "high"),
]

# 信号阈值
_SIGNAL_STRONG_POS = 0.72
_SIGNAL_POS = 0.58
_SIGNAL_NEG = 0.42
_SIGNAL_STRONG_NEG = 0.30


def _confidence_level(n_sessions: int) -> str:
    for threshold, level in reversed(_CONFIDENCE_TIERS):
        if n_sessions >= threshold:
            return level
    return "low"


def predict_success_probability(
    sessions: list[dict],
) -> dict[str, Any]:
    """
    给定一组 analytics session dict，预测融资成功概率。

    输入字段（每条 session）：
      total_score        : int
      risk_breakdown     : {严重:{count,total_deduction}, ...}
      fundraising_outcome: str  "已成功"|"未推进"|"进行中"|""

    返回：
      probability : float [0.0, 1.0] | None（无数据时）
      confidence  : str   "low"|"medium"|"high"
      signal      : str   "strong_positive"|"positive"|"neutral"|"negative"|"strong_negative"
      factors     : list[str]  影响预测的主要因素说明
    """
    if not sessions:
        return {
            "probability": None,
            "confidence": "low",
            "signal": "neutral",
            "factors": ["暂无历史数据"],
        }

    n = len(sessions)
    factors: list[str] = []

    # ── 1. 基础分：平均总分归一化 ──────────────────────────────────────────
    scores = [s.get("total_score", 0) for s in sessions]
    avg_score = sum(scores) / n
    base = avg_score / 100.0
    factors.append(f"平均路演得分 {avg_score:.1f}（基础贡献 {base:.2f}）")

    # ── 2. 严重风险惩罚 ────────────────────────────────────────────────────
    total_risk_counts = []
    for s in sessions:
        rb = s.get("risk_breakdown") or {}
        severe = rb.get("严重", {}).get("count", 0) if isinstance(rb.get("严重"), dict) else 0
        normal = rb.get("一般", {}).get("count", 0) if isinstance(rb.get("一般"), dict) else 0
        light  = rb.get("轻微", {}).get("count", 0) if isinstance(rb.get("轻微"), dict) else 0
        total = severe + normal + light
        total_risk_counts.append((severe, total))

    avg_severe = sum(s for s, _ in total_risk_counts) / n
    avg_total_risk = sum(t for _, t in total_risk_counts) / n if n > 0 else 1
    severe_ratio = avg_severe / max(avg_total_risk, 1)
    severe_penalty = severe_ratio * 0.3
    if avg_severe > 0:
        factors.append(f"平均严重风险 {avg_severe:.1f} 个（惩罚 -{severe_penalty:.2f}）")

    # ── 3. 历史结果加成 ─────────────────────────────────────────────────────
    outcome_counter: Counter[str] = Counter()
    for s in sessions:
        fo = (s.get("fundraising_outcome") or "").strip()
        if fo in ("已成功", "未推进"):
            outcome_counter[fo] += 1

    total_with_outcome = sum(outcome_counter.values())
    outcome_boost = 0.0
    if total_with_outcome > 0:
        success_ratio = outcome_counter["已成功"] / total_with_outcome
        failed_ratio  = outcome_counter["未推进"] / total_with_outcome
        outcome_boost = (success_ratio - failed_ratio) * 0.2
        if outcome_boost > 0:
            factors.append(f"历史成功记录加成 +{outcome_boost:.2f}")
        elif outcome_boost < 0:
            factors.append(f"历史未推进记录惩罚 {outcome_boost:.2f}")

    # ── 4. 综合计算 ─────────────────────────────────────────────────────────
    raw = base - severe_penalty + outcome_boost
    probability = max(0.05, min(0.95, raw))

    # ── 5. 信号标签 ─────────────────────────────────────────────────────────
    if probability >= _SIGNAL_STRONG_POS:
        signal = "strong_positive"
    elif probability >= _SIGNAL_POS:
        signal = "positive"
    elif probability <= _SIGNAL_STRONG_NEG:
        signal = "strong_negative"
    elif probability <= _SIGNAL_NEG:
        signal = "negative"
    else:
        signal = "neutral"

    return {
        "probability": round(probability, 3),
        "confidence": _confidence_level(n),
        "signal": signal,
        "factors": factors,
    }


def bulk_predict_for_workspace(
    workspace_root: Path | str,
) -> dict[str, dict[str, Any]]:
    """
    批量扫描 workspace_root 下所有 *_analytics.json，
    按 company_id 分组预测成功率。

    返回 {company_id: predict_success_probability_result}
    """
    workspace_root = Path(workspace_root)
    company_sessions: dict[str, list[dict]] = {}

    for p in workspace_root.rglob("*_analytics.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            cid = (data.get("company_id") or "").strip()
            if not cid:
                continue
            company_sessions.setdefault(cid, []).append(data)
        except (json.JSONDecodeError, OSError):
            continue

    results: dict[str, dict[str, Any]] = {}
    for cid, sessions in company_sessions.items():
        try:
            results[cid] = predict_success_probability(sessions)
        except Exception as exc:
            logger.warning("outcome_predictor: %s 预测失败（%s）", cid, exc)
            results[cid] = {
                "probability": None,
                "confidence": "low",
                "signal": "neutral",
                "factors": [f"预测失败：{exc}"],
            }

    return results
