"""
会前简报引擎 — V10.2 数据飞轮。

给定「即将见面的投资机构 + 当前被访公司」，自动生成会前简报：
1. 该机构历史上最爱追的问题类型（Top3）
2. 该公司历次遗留的逻辑坑（未修复的严重风险点）
3. LLM 汇总生成「今天要重点准备的3件事」

设计原则：
- generate_briefing_data()  纯数据，无 LLM 调用，供测试和 UI 实时预览
- generate_briefing_text()  调用 DeepSeek 生成最终简报文本（Markdown）
- 失败静默返回降级文本，不影响主流程
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

from asset_bridge import build_asset_section, load_asset_index
from institution_profiler import build_institution_profile
from runtime_paths import get_writable_app_root

load_dotenv(get_writable_app_root() / ".env")
logger = logging.getLogger(__name__)

_MAX_KILLER_Q = 5
_MAX_PITS = 5


def _scan_company_pits(
    company_id: str,
    workspace_root: Path,
) -> list[dict]:
    """
    扫描该公司历次 analytics，聚合「遗留逻辑坑」。

    定义：risk_type_counts 中出现频次最高的严重风险类型，
    视为该公司反复翻车的坑，需要会前专项准备。
    """
    from collections import Counter

    root = Path(workspace_root)
    pits: Counter[str] = Counter()
    sessions_found = 0

    for p in root.rglob("*_analytics.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("company_id", "") != company_id:
                continue
            sessions_found += 1
            for rt, cnt in data.get("risk_type_counts", {}).items():
                pits[rt] += cnt
        except (json.JSONDecodeError, OSError):
            continue

    return [
        {"risk_type": rt, "count": cnt}
        for rt, cnt in pits.most_common(_MAX_PITS)
    ]


def generate_briefing_data(
    institution_id: str,
    company_id: str,
    workspace_root: Path | str,
) -> dict:
    """
    纯数据层：返回简报所需的结构化信息，不调用 LLM。

    返回字段：
      institution_id       : str
      canonical_name       : str
      institution_top_risks: list[{risk_type, count, ratio}]  机构惯用追问类型
      killer_questions     : list[str]  机构历史最致命问题
      company_id           : str
      company_pits         : list[{risk_type, count}]  公司遗留逻辑坑
      has_history          : bool  是否有足够历史数据
    """
    workspace_root = Path(workspace_root)
    profile = build_institution_profile(institution_id, workspace_root)
    company_pits = _scan_company_pits(company_id, workspace_root)

    has_history = profile["total_sessions"] > 0

    return {
        "institution_id": institution_id,
        "canonical_name": profile["canonical_name"],
        "institution_top_risks": profile["top_risk_types"][:3],
        "killer_questions": profile["killer_questions"],
        "company_id": company_id,
        "company_pits": company_pits,
        "has_history": has_history,
        "total_sessions": profile["total_sessions"],
        "avg_score": profile["avg_score"],
    }


def generate_briefing_text(
    institution_id: str,
    company_id: str,
    workspace_root: Path | str,
    company_name: str = "",
    institution_name: str = "",
) -> str:
    """
    调用 DeepSeek 生成会前简报 Markdown 文本。
    数据不足时返回纯数据摘要（不调用 LLM）。
    LLM 调用失败时静默降级到纯数据摘要。
    """
    data = generate_briefing_data(institution_id, company_id, workspace_root)

    display_institution = institution_name or data["canonical_name"] or institution_id
    display_company = company_name or company_id

    # 数据不足时直接返回摘要
    if not data["has_history"]:
        base = (
            f"## 📋 会前简报：{display_company} × {display_institution}\n\n"
            "**暂无该机构的历史数据**，本次见面后将开始积累画像。\n\n"
            "建议提前准备：估值逻辑、商业模式可持续性、核心财务数据。"
        )
        return base + _make_asset_appendix(data)

    # 构建 prompt 数据
    top_risks_text = "\n".join(
        f"- {r['risk_type']}（占比 {r['ratio']*100:.0f}%）"
        for r in data["institution_top_risks"]
    ) or "暂无统计"

    pits_text = "\n".join(
        f"- {p['risk_type']}（历次出现 {p['count']} 次）"
        for p in data["company_pits"]
    ) or "暂无记录"

    killer_text = "\n".join(
        f"- {q}" for q in data["killer_questions"]
    ) or "暂无记录"

    prompt = f"""你是一位顶级的融资路演顾问，正在为被访公司做会前辅导。

## 背景数据

**投资机构**：{display_institution}（历史 {data['total_sessions']} 场，平均得分 {data['avg_score']}）

**该机构最爱追问的问题类型**：
{top_risks_text}

**该机构历史最致命问题**：
{killer_text}

**被访公司**：{display_company}

**该公司历次遗留的逻辑坑**：
{pits_text}

## 你的任务

基于以上数据，生成一份**简洁有力的会前简报**，格式要求：

1. 开头用1句话说明今天见这家机构最大的风险是什么
2. 列出「今天必须填的3个坑」（具体，可操作，50字以内/条）
3. 列出「这家机构最可能的2个杀手问题」及建议应对思路（各50字以内）
4. 结尾1句鼓励的话

用**中文**，**Markdown格式**，语气专业但不刻板。"""

    try:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY 未设置")
        client = OpenAI(base_url="https://api.deepseek.com", api_key=api_key)
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.7,
        )
        text = resp.choices[0].message.content or ""
        header = f"## 📋 会前简报：{display_company} × {display_institution}\n\n"
        briefing = header + text
    except Exception as exc:
        logger.warning("briefing_engine: LLM 调用失败，返回数据摘要（%s）", exc)
        briefing = _fallback_briefing(data, display_company, display_institution)

    return briefing + _make_asset_appendix(data)


def _fallback_briefing(data: dict, company: str, institution: str) -> str:
    """LLM 不可用时的纯数据降级简报。"""
    lines = [f"## 📋 会前简报：{company} × {institution}\n"]
    lines.append(f"**机构历史**：{data['total_sessions']} 场，平均得分 {data['avg_score']}\n")

    if data["institution_top_risks"]:
        lines.append("**该机构最爱追问**：")
        for r in data["institution_top_risks"]:
            lines.append(f"- {r['risk_type']}（占比 {r['ratio']*100:.0f}%）")
        lines.append("")

    if data["company_pits"]:
        lines.append("**本公司历次遗留逻辑坑**：")
        for p in data["company_pits"]:
            lines.append(f"- {p['risk_type']}（出现 {p['count']} 次）")
        lines.append("")

    if data["killer_questions"]:
        lines.append("**机构历史最致命问题**：")
        for q in data["killer_questions"]:
            lines.append(f"- {q}")

    return "\n".join(lines)


def _make_asset_appendix(data: dict) -> str:
    """
    从 .fos_data/asset_index.json 读取资产清单，
    按简报中出现的风险类型做关键词匹配，返回「库中相关资产」段落。
    失败或无匹配时返回空字符串，不影响调用方。
    """
    try:
        assets = load_asset_index()
        if not assets:
            return ""

        # 收集关键词：公司历史遗留坑 + 机构最爱追问类型
        keywords = (
            [p["risk_type"] for p in data.get("company_pits", [])]
            + [r["risk_type"] for r in data.get("institution_top_risks", [])]
        )
        return build_asset_section(keywords, assets)
    except Exception:
        return ""
