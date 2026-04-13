"""
investor_matcher.py — 投资人匹配引擎 v1.0 (Sprint 3)

基于 AI Coach 已积累的机构画像数据（analytics 文件），
为给定的目标公司推荐最匹配的投资机构。

设计原则：
  - 纯关键词命中计数，零 LLM 成本，零网络延迟
  - analytics 文件已有就能用，数据越积累越准（数据飞轮）
  - 失败路径静默降级（文件不存在、格式错误 → 空列表）
  - LLM 增强为可选扩展点（扩大关键词库等）
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class CompanySnapshot:
    """
    目标公司的关键标签快照（用于匹配计算）。
    由用户手动输入或从现有 company_profile 提取。
    """
    company_name: str
    industry_tags: list[str] = field(default_factory=list)   # 行业标签，如 ["军工电子", "AI"]
    stage: str = ""                                            # 融资阶段，如 "B轮"
    revenue_rmb_wan: int = 0                                   # 营收（万元），0表示未知
    model_tags: list[str] = field(default_factory=list)        # 商业模式，如 ["ToB", "硬科技"]
    highlights: list[str] = field(default_factory=list)        # 亮点描述


@dataclass
class InstitutionMatchResult:
    """单个机构的匹配结果。"""
    institution_id: str
    institution_name: str
    score: int                              # 0~100
    matched_keywords: list[str] = field(default_factory=list)
    stage_match: bool = False
    session_count: int = 0                  # 积累的访谈次数（越多画像越准）
    match_reason: str = ""                  # 人类可读的匹配理由


# ─────────────────────────────────────────────
# Analytics 数据加载
# ─────────────────────────────────────────────

def _load_analytics_by_institution(workspace_root: str) -> dict[str, list[dict]]:
    """
    扫描 workspace_root 下所有 *_analytics.json，
    按 institution_id 分组返回。
    文件不存在或格式错误时静默跳过。
    """
    grouped: dict[str, list[dict]] = {}
    root = Path(workspace_root)
    if not root.exists():
        return grouped

    for p in root.rglob("*_analytics.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            iid = (data.get("institution_id") or "").strip()
            if iid:
                grouped.setdefault(iid, []).append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return grouped


# ─────────────────────────────────────────────
# 机构画像构建
# ─────────────────────────────────────────────

def build_institution_profile_from_analytics(records: list[dict]) -> Optional[dict]:
    """
    从同一机构的多条 analytics 记录合并构建机构画像。
    records 为空时返回 None。
    """
    if not records:
        return None

    # 取第一条的基础信息
    base = records[0]
    institution_id = (base.get("institution_id") or "").strip()
    institution_name = (base.get("institution_name") or institution_id).strip()

    # 合并所有关键词（去重）
    all_keywords: set[str] = set()
    all_stages: set[str] = set()
    total_sessions = 0

    for rec in records:
        for kw in rec.get("high_freq_topics") or []:
            if kw:
                all_keywords.add(str(kw).strip())
        for kw in rec.get("focus_keywords") or []:
            if kw:
                all_keywords.add(str(kw).strip())
        for stage in rec.get("preferred_stages") or []:
            if stage:
                all_stages.add(str(stage).strip())
        # V10.6：从 company_id 提取词片段（补充匹配信号）
        cid_raw = (rec.get("company_id") or "").strip()
        if cid_raw:
            # 按下划线/空格分割，并提取长度≥2的汉字片段
            for seg in re.split(r"[_\s，,、]+", cid_raw):
                if len(seg) >= 2:
                    all_keywords.add(seg)
                # 同时提取内部2-4字中文短语（滑动窗口）
                cjk = re.sub(r"[^\u4e00-\u9fff]", "", seg)
                if len(cjk) >= 4:
                    for i in range(len(cjk) - 1):
                        chunk = cjk[i:i+2]
                        if chunk:
                            all_keywords.add(chunk)
        total_sessions += int(rec.get("session_count") or 1)

    return {
        "institution_id": institution_id,
        "institution_name": institution_name,
        "all_keywords": sorted(all_keywords),
        "preferred_stages": sorted(all_stages),
        "session_count": total_sessions,
    }


# ─────────────────────────────────────────────
# 匹配分计算
# ─────────────────────────────────────────────

_STAGE_ORDER = ["天使轮", "Pre-A", "A轮", "A+轮", "B轮", "B+轮", "C轮", "D轮", "上市前", "战略轮"]


def _stage_proximity(stage_a: str, stage_b: str) -> float:
    """
    计算两个融资阶段的接近程度（1.0=完全相同，0.5=相差1档，0.0=相差3档以上）。
    """
    try:
        ia = _STAGE_ORDER.index(stage_a)
        ib = _STAGE_ORDER.index(stage_b)
        diff = abs(ia - ib)
        return max(0.0, 1.0 - diff * 0.25)
    except ValueError:
        return 0.0


def calculate_match_score(
    company: CompanySnapshot,
    inst_profile: dict,
) -> int:
    """
    计算公司与机构的匹配分（0~100）。

    评分维度：
      - 行业关键词重合度（40分）
      - 融资阶段匹配度（25分）
      - 商业模式重合度（20分）
      - 访谈积累深度奖励（15分，访谈越多画像越可信）
    """
    inst_keywords = set(kw.lower() for kw in inst_profile.get("all_keywords") or [])
    inst_stages = set(s for s in inst_profile.get("preferred_stages") or [])
    session_count = int(inst_profile.get("session_count") or 0)

    # 1. 行业关键词重合度（40分）
    company_industry = set(t.lower() for t in company.industry_tags)
    industry_hits = company_industry & inst_keywords
    industry_score = min(40, len(industry_hits) * 10) if inst_keywords else 0

    # 2. 融资阶段匹配度（25分）
    stage_score = 0.0
    if company.stage and inst_stages:
        best_stage = max(
            (_stage_proximity(company.stage, s) for s in inst_stages),
            default=0.0,
        )
        stage_score = best_stage * 25
    elif not inst_stages:
        stage_score = 12  # 阶段未知时给半分（不惩罚）

    # 3. 商业模式重合度（20分）
    company_model = set(t.lower() for t in company.model_tags)
    model_hits = company_model & inst_keywords
    model_score = min(20, len(model_hits) * 7) if inst_keywords else 0

    # 4. 访谈积累深度（15分）
    depth_score = min(15, session_count * 5)

    total = int(round(industry_score + stage_score + model_score + depth_score))
    return max(0, min(100, total))


# ─────────────────────────────────────────────
# 匹配理由生成
# ─────────────────────────────────────────────

def _build_match_reason(
    company: CompanySnapshot,
    inst_profile: dict,
    matched_keywords: list[str],
    stage_match: bool,
) -> str:
    parts = []
    if matched_keywords:
        parts.append(f"行业/模式标签重合：{', '.join(matched_keywords[:4])}")
    if stage_match:
        parts.append(f"融资阶段吻合（{company.stage}）")
    elif inst_profile.get("preferred_stages"):
        parts.append(f"机构偏好阶段：{'、'.join(inst_profile['preferred_stages'][:3])}")
    session_count = inst_profile.get("session_count", 0)
    if session_count > 0:
        parts.append(f"已有 {session_count} 次访谈记录（画像可信度高）")
    return "；".join(parts) if parts else "基础关键词匹配"


# ─────────────────────────────────────────────
# 完整匹配流程
# ─────────────────────────────────────────────

def match_institutions(
    company: CompanySnapshot,
    workspace_root: str,
    top_n: int = 10,
) -> list[InstitutionMatchResult]:
    """
    主入口：从 workspace_root 的 analytics 数据中，
    为 company 推荐最匹配的机构列表（按得分降序）。

    workspace_root：AI Coach 的工作目录（含 analytics 文件）
    top_n：最多返回多少家机构
    """
    grouped = _load_analytics_by_institution(workspace_root)
    if not grouped:
        return []

    results: list[InstitutionMatchResult] = []
    company_all_tags = set(t.lower() for t in company.industry_tags + company.model_tags)

    for iid, records in grouped.items():
        profile = build_institution_profile_from_analytics(records)
        if not profile:
            continue

        score = calculate_match_score(company, profile)

        # 计算匹配关键词
        inst_keywords = set(kw.lower() for kw in profile.get("all_keywords") or [])
        matched_kw = sorted(company_all_tags & inst_keywords)

        # 阶段匹配
        inst_stages = set(profile.get("preferred_stages") or [])
        stage_match = bool(company.stage and company.stage in inst_stages)

        reason = _build_match_reason(company, profile, matched_kw, stage_match)

        results.append(InstitutionMatchResult(
            institution_id=iid,
            institution_name=profile["institution_name"],
            score=score,
            matched_keywords=matched_kw,
            stage_match=stage_match,
            session_count=profile.get("session_count", 0),
            match_reason=reason,
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_n]


# ─────────────────────────────────────────────
# 报告格式化
# ─────────────────────────────────────────────

def format_match_report(company: CompanySnapshot, results: list[InstitutionMatchResult]) -> str:
    """格式化匹配报告为易读文本。"""
    lines = [
        "=" * 54,
        f"   投资人匹配报告 — {company.company_name}",
        "=" * 54,
        f"公司标签：{', '.join(company.industry_tags + company.model_tags)}",
        f"融资阶段：{company.stage or '未知'}",
        "",
    ]

    if not results:
        lines.append("暂无匹配数据（请先完成至少一次机构访谈以积累画像）")
    else:
        lines.append(f"共找到 {len(results)} 家潜在匹配机构：")
        lines.append("")
        for i, r in enumerate(results, 1):
            bar = "█" * (r.score // 10) + "░" * (10 - r.score // 10)
            lines.append(f"{i}. {r.institution_name}  [{bar}] {r.score}分")
            lines.append(f"   {r.match_reason}")
            if r.session_count > 0:
                lines.append(f"   📊 已有 {r.session_count} 次访谈记录")
            lines.append("")

    lines.append("=" * 54)
    return "\n".join(lines)
