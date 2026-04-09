# 依赖：pip install openai python-dotenv pydantic
"""
LLM 逻辑打分模块 2.0：三巨头模型路由（DeepSeek / Kimi / Qwen-Max）+ AnalysisReport 契约。
仓库发版 V7.5（与 build_release.CURRENT_VERSION 对齐）。
V7.5：max_tokens 扩容、截断 JSON 抢救、结构化狙击清单；沿用转写/QA 分池与超长截断。
支持显式业务上下文与 QA 知识库注入，结构化防幻觉 Prompt。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from collections.abc import Callable
from typing import Any, List

from dotenv import load_dotenv
from openai import APIError, OpenAI
from pydantic import ValidationError

from retry_policy import run_with_backoff
from schema import (
    AnalysisReport,
    ExecutiveMemory,
    MagicRefinementResult,
    RiskPoint,
    RiskScanResult,
    RiskTargetCandidate,
    SceneAnalysis,
    TranscriptionWord,
)
from runtime_paths import get_writable_app_root

# ---------------------------------------------------------------------------
load_dotenv(get_writable_app_root() / ".env")

logger = logging.getLogger(__name__)

# V7.0：录音转写与 QA 补充材料字数池物理隔离
MAX_TRANSCRIPT_CHARS = 80_000
MAX_QA_CHARS = 30_000
MAX_COMPANY_BG_CHARS = 8_000

# OpenAI 兼容接口 completion 上限（按模型典型能力设安全值，避免默认过小导致 JSON 拦腰截断）
MAX_COMPLETION_TOKENS_BY_MODEL: dict[str, int] = {
    "deepseek-chat": 8192,
    "moonshot-v1-32k": 8192,
    "qwen-max": 8192,
}

MIDDLE_OMIT_MARK = "\n...[内容过长，系统已智能省略中间部分]...\n"


def truncate_qa_text(qa: str, max_chars: int = MAX_QA_CHARS) -> tuple[str, bool]:
    """
    超长 QA 掐头去尾，中间用省略标记连接。
    返回 (处理后文本, 是否发生过截断)；结果长度保证不超过 max_chars。
    """
    q = (qa or "").strip()
    if len(q) <= max_chars:
        return q, False
    m = len(MIDDLE_OMIT_MARK)
    if max_chars <= m:
        return q[:max_chars], True
    inner = max_chars - m
    head_n = inner // 2
    tail_n = inner - head_n
    return q[:head_n] + MIDDLE_OMIT_MARK + q[-tail_n:], True


def truncate_company_background(bg: str, max_chars: int = MAX_COMPANY_BG_CHARS) -> tuple[str, bool]:
    """
    公司背景超出限制时截取头部（优先保留公司核心信息）。
    返回 (处理后文本, 是否发生过截断)。
    """
    b = (bg or "").strip()
    if len(b) <= max_chars:
        return b, False
    return b[:max_chars], True


def detect_logical_conflict(company_background: str, sniper_targets_json: str) -> list[str]:
    """
    检测公司背景与狙击目标之间的潜在逻辑冲突（冲突报警机制）。
    简单关键词重叠检测：若狙击 reason 中长度>2 的词片段出现在背景中，触发警告。
    返回警告字符串列表；无冲突或输入为空时返回 []。
    """
    bg = (company_background or "").strip()
    sj = (sniper_targets_json or "").strip()
    if not bg or not sj:
        return []
    try:
        snipers = json.loads(sj)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(snipers, list):
        return []
    warnings: list[str] = []
    for item in snipers:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason", "")).strip()
        if not reason:
            continue
        # 按常见分隔符拆词，取长度 > 2 的片段做关键词
        fragments = re.split(r"[，,、。.；;\s]+", reason)
        matched_frag: str = ""
        for frag in fragments:
            frag = frag.strip()
            if len(frag) > 2 and frag in bg:
                matched_frag = frag
                break
        # 如果分隔符拆词未命中，进一步用滑动窗口（步长3~8字）查找共现关键词
        if not matched_frag:
            for win in range(4, min(len(reason) + 1, 9)):
                for start in range(0, len(reason) - win + 1):
                    sub = reason[start:start + win]
                    if sub in bg:
                        matched_frag = sub
                        break
                if matched_frag:
                    break
        if matched_frag:
            warnings.append(
                f"潜在冲突：狙击目标「{reason[:30]}」中的关键词「{matched_frag}」出现在公司背景描述中，请确认口径一致性"
            )
    return warnings


# 三巨头官方兼容 OpenAI 的路由配置
ROUTER: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "KIMI_API_KEY",
        "model": "moonshot-v1-32k",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "model": "qwen-max",
    },
}

# 主评委与错题提炼（V8.6.1 起统一 DeepSeek 通道，不引入第二家闭源底座）
JUDGE_MODEL_KEYS: frozenset[str] = frozenset({"deepseek", "kimi", "qwen"})

DISPLAY_NAME = {
    "deepseek": "DeepSeek-V3 (deepseek-chat)",
    "kimi": "Kimi (Moonshot moonshot-v1-32k)",
    "qwen": "Qwen-Max (DashScope 兼容模式)",
}


def choose_model_with_timeout(timeout: float = 3) -> str:
    """
    终端 3 秒内可选 k / q 切换模型；超时或未输入则默认 deepseek。
    Windows 下 stdin 无法用 select 可靠做超时，故使用「子线程 readline + 主线程 queue.get 超时」。
    """
    t0 = time.monotonic()
    print(
        "默认使用 DeepSeek-V3 评委。你有 3 秒钟时间输入 k (切换Kimi) 或 q (切换Qwen)，按回车确认。"
        "不输入则默认 DeepSeek...",
        flush=True,
    )

    q: queue.Queue[str] = queue.Queue(maxsize=1)

    def _reader() -> None:
        try:
            line = sys.stdin.readline()
        except Exception:
            q.put("deepseek")
            return
        s = (line or "").strip().lower()
        if s.startswith("k"):
            q.put("kimi")
        elif s.startswith("q"):
            q.put("qwen")
        else:
            q.put("deepseek")

    threading.Thread(target=_reader, daemon=True).start()
    try:
        choice = q.get(timeout=timeout)
        logger.debug("choose_model 耗时 %.2fs", time.monotonic() - t0)
        return choice
    except queue.Empty:
        logger.debug("choose_model 超时，默认 deepseek，耗时 %.2fs", time.monotonic() - t0)
        return "deepseek"


def _recover_risk_point_dicts_from_truncated_json(raw: str) -> list[dict[str, Any]] | None:
    """
    在整段响应非法 JSON 时，定位 risk_points 数组，用 JSONDecoder.raw_decode
    顺序解析其中每一个完整对象，丢弃末尾未完成碎片（非正则拼接）。
    """
    marker = '"risk_points"'
    mi = raw.find(marker)
    if mi < 0:
        return None
    lb = raw.find("[", mi)
    if lb < 0:
        return None
    decoder = json.JSONDecoder()
    pos = lb + 1
    n = len(raw)
    items: list[dict[str, Any]] = []
    while pos < n:
        while pos < n and raw[pos] in " \t\n\r,":
            pos += 1
        if pos >= n or raw[pos] == "]":
            break
        try:
            obj, end = decoder.raw_decode(raw, pos)
            if isinstance(obj, dict):
                items.append(obj)
            pos = end
        except json.JSONDecodeError:
            break
    return items or None


def _closing_brace_indices_outside_strings(s: str) -> list[int]:
    """从左到右记录所有位于 JSON 字符串外的 `}` 下标（供逆向裁剪候选）。"""
    in_str = False
    escape = False
    out: list[int] = []
    for i, c in enumerate(s):
        if escape:
            escape = False
            continue
        if in_str:
            if c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "}":
            out.append(i)
    return out


def salvage_risk_point_dicts_from_truncated_llm_json(raw: str) -> list[dict[str, Any]] | None:
    """
    抢救 `risk_points` 中已完整闭合的对象列表。
    顺序：① 数组内 raw_decode 增量解析；② 自右向左在字符串外截取到最后一个 `}` 再试；
    ③ 自尾向前逐字符缩短再试。全程不抛 JSONDecodeError。
    """
    if not (raw or "").strip():
        return None
    direct = _recover_risk_point_dicts_from_truncated_json(raw)
    if direct:
        return direct
    mi = raw.find('"risk_points"')
    if mi < 0:
        return None
    lb = raw.find("[", mi)
    if lb < 0:
        return None
    for j in reversed(_closing_brace_indices_outside_strings(raw)):
        if j <= lb:
            continue
        prefix = raw[: j + 1].rstrip()
        got = _recover_risk_point_dicts_from_truncated_json(prefix)
        if got:
            return got
    rstrip = raw.rstrip()
    max_trim = min(8000, max(0, len(rstrip)))
    for t in range(1, max_trim + 1):
        got = _recover_risk_point_dicts_from_truncated_json(rstrip[:-t].rstrip())
        if got:
            return got
    return None


def _is_valid_risk_point(rp: RiskPoint) -> bool:
    """空壳 RiskPoint 守门：tier1 和 improvement_suggestion 均非空才算有效。"""
    return bool((rp.tier1_general_critique or "").strip()) and bool(
        (rp.improvement_suggestion or "").strip()
    )


def salvage_truncated_analysis_report(raw: str) -> AnalysisReport | None:
    """将截断 LLM 输出抢救为可展示的 AnalysisReport；无法抢救时返回 None。"""
    dicts = salvage_risk_point_dicts_from_truncated_llm_json(raw)
    if not dicts:
        return None
    risks: list[RiskPoint] = []
    for d in dicts:
        try:
            risks.append(RiskPoint.model_validate(d))
        except ValidationError:
            continue
    # Fix 4: 过滤空壳 RiskPoint（tier1 或 improvement 为空）
    risks = [rp for rp in risks if _is_valid_risk_point(rp)]
    if not risks:
        return None
    total_ded = sum(int(r.score_deduction or 0) for r in risks)
    total_ded = min(100, max(0, total_ded))
    score = max(0, min(100, 100 - total_ded))
    return AnalysisReport(
        scene_analysis=SceneAnalysis(
            scene_type="（模型 JSON 被 API 截断，已抢救部分风险点）",
            speaker_roles="请结合录音与审查台人工复核",
        ),
        total_score=score,
        total_score_deduction_reason=(
            "（输出被长度截断，系统已丢弃未完成片段；建议缩短上下文或重试）"
        ),
        risk_points=risks,
    )


def _salvage_analysis_report_from_truncated_json(raw: str) -> AnalysisReport | None:
    return salvage_truncated_analysis_report(raw)


def _salvage_risk_scan_result(raw: str, original_error: Exception) -> RiskScanResult | None:
    """
    阶段一 JSON 截断时的最大努力抢救：
    1. 尝试从已截断的 JSON 中解析 scene_analysis；
    2. 成功时返回含空 targets 的 RiskScanResult（总比崩溃好）；
    3. 解析失败返回 None，由调用方决定是否抛异常。
    """
    if not (raw or "").strip():
        return None
    # 先尝试完整解析（带 try 双保险）
    try:
        return RiskScanResult.model_validate_json(raw)
    except Exception:
        pass
    # 从截断 JSON 中提取 scene_analysis
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 逆向裁剪至最后合法 `}`
        for i in range(len(raw) - 1, -1, -1):
            if raw[i] == "}":
                try:
                    data = json.loads(raw[: i + 1])
                    break
                except json.JSONDecodeError:
                    continue
        else:
            return None
    if not isinstance(data, dict):
        return None
    sa_raw = data.get("scene_analysis")
    if not isinstance(sa_raw, dict):
        return None
    try:
        sa = SceneAnalysis.model_validate(sa_raw)
    except (ValidationError, Exception):
        return None
    targets_raw = data.get("targets") or []
    valid_targets: list[RiskTargetCandidate] = []
    if isinstance(targets_raw, list):
        for item in targets_raw:
            try:
                valid_targets.append(RiskTargetCandidate.model_validate(item))
            except (ValidationError, Exception):
                continue
    return RiskScanResult(scene_analysis=sa, targets=valid_targets)


def _validation_suggests_truncated_json(e: ValidationError) -> bool:
    for err in e.errors():
        if err.get("type") == "json_invalid":
            ctx = err.get("ctx") or {}
            je = ctx.get("error")
            if je is not None:
                m = str(je).lower()
                if "eof" in m or "unterminated" in m or "delimiter" in m:
                    return True
    msg = str(e).lower()
    return "eof" in msg or "invalid json" in msg


def _format_sniper_block(sniper_json: str) -> str:
    try:
        data = json.loads(sniper_json)
    except json.JSONDecodeError:
        return "（狙击清单 JSON 无法解析，已忽略）"
    if not isinstance(data, list):
        return "（无主理人结构化狙击清单）"
    rows: list[dict[str, str]] = []
    for x in data:
        if not isinstance(x, dict):
            continue
        q = str(x.get("quote", "") or "").strip()
        r = str(x.get("reason", "") or "").strip()
        if q or r:
            rows.append({"quote": q, "reason": r})
    if not rows:
        return "（无主理人结构化狙击清单）"
    return json.dumps(rows, ensure_ascii=False, indent=2)


def format_transcript_for_llm(words: List[TranscriptionWord]) -> str:
    """[0]词 [1]词 ..."""
    if not words:
        return ""
    parts: list[str] = []
    for w in words:
        text = (w.text or "").strip()
        parts.append(f"[{w.word_index}]{text}")
    return " ".join(parts)


def _normalize_explicit_context(explicit_context: dict[str, Any] | None) -> dict[str, str]:
    """缺省键时填占位，避免 Prompt 中出现 None。"""
    base = explicit_context or {}
    notes = str(base.get("session_notes") or "").strip()
    rec = str(base.get("recording_label") or "").strip()
    sj = str(base.get("sniper_targets_json") or "").strip() or "[]"
    return {
        "biz_type": str(base.get("biz_type") or "未指定"),
        "exact_roles": str(base.get("exact_roles") or "未指定"),
        "project_name": str(base.get("project_name") or "未指定"),
        "interviewee": str(base.get("interviewee") or "未指定"),
        "session_notes": notes if notes else "无",
        "sniper_targets_json": sj,
        "recording_label": rec if rec else "未指定",
    }


def _format_historical_profile_block(memories: list[ExecutiveMemory] | None) -> str:
    """
    V8.6：将 Top 记忆格式化为 Prompt 块（单条字段截断，防 Token 爆炸）。
    红蓝：再次按 weight 降序截断至 5 条，防止调用方误传过长列表。
    """
    if not memories:
        return ""
    sorted_m = sorted(memories, key=lambda m: m.weight, reverse=True)[:5]
    lines: list[str] = []
    for i, m in enumerate(sorted_m, 1):
        raw = (m.raw_text or "").replace("\n", " ").strip()
        cor = (m.correction or "").replace("\n", " ").strip()
        if len(raw) > 400:
            raw = raw[:400] + "…"
        if len(cor) > 400:
            cor = cor[:400] + "…"
        lines.append(
            f"{i}. [标签:{m.tag}] [权重:{m.weight:.2f}] "
            f"易错要点：{raw} → 建议口径：{cor}"
        )
    return "\n".join(lines)


def _build_system_prompt(
    schema_str: str,
    explicit_context: dict[str, Any] | None,
    qa_text: str,
    company_background: str = "",
    historical_memories: list[ExecutiveMemory] | None = None,
) -> str:
    ctx = _normalize_explicit_context(explicit_context)
    kb = (qa_text or "").strip()
    kb_block = kb if kb else "未提供参考QA知识库。"
    sniper_block = _format_sniper_block(ctx["sniper_targets_json"])
    _bg_use = (company_background or "").strip()
    _company_bg_block = (
        f"<COMPANY_BACKGROUND>\n{_bg_use}\n</COMPANY_BACKGROUND>"
        if _bg_use
        else ""
    )
    _hist_use = _format_historical_profile_block(historical_memories)
    _hist_block = (
        f"<HISTORICAL_PROFILE>\n"
        f"以下为该高管/标签下历史沉淀的「易错点与标准口径」（按权重优先列出，最多 Top 5）。\n"
        f"请在本场复盘中优先规避同类表述，并在不违背逐字稿事实的前提下对齐建议口径。\n"
        f"{_hist_use}\n"
        f"</HISTORICAL_PROFILE>"
        if _hist_use
        else ""
    )

    return f"""你是一位拥有15年一线投行经验的「顶级金牌路演教练」。你的唯一服务对象是【被访谈对象/路演发言人】。你正在复盘带有词级索引 [index] 的录音逐字稿，目的是帮助发言人提升话术应对能力。
<DOMAIN_ANCHOR>
【行业领域铁律 — 优先级高于一切，在解读任何词汇前必须先读此节】
本系统专用于以下领域的创业企业被尽调访谈与路演复盘：
硬科技 / 深科技 / 军工国防 / 低空经济 / 先进制造 / 半导体。

遇到存在歧义的词汇，必须优先采纳【技术 / 商业 / 产品维度】的解释，
严禁脑补法律诉讼、公关纠纷或社会事件背景，具体规则如下：
  • "指控"   → 指挥控制（Command and Control，C2），军工/低空领域标准术语
  • "火控"   → 火力控制系统（Fire Control System），非法律"控诉"
  • "靶场"   → 测试场地 / 靶向测试环境
  • "制导"   → 导弹制导 / 精准制导技术
  • "预警"   → 感知预警系统，非灾害/舆情预警
  • "攻击"   → 进攻性飞行器 / 攻击模式，非人身攻击
  • "载荷"   → 有效载荷（Payload），航空器携带的任务设备

【绝对红线】：
1. 除非逐字稿中明确出现「法院」「诉讼」「起诉书」「律师函」等法律词汇，
   否则禁止凭空引入任何法律纠纷叙事。
2. 严禁捏造不存在于逐字稿中的机构名称、产品名称或人名。
3. 如不确定某专业术语的含义，优先假设其为当前硬科技领域的技术术语，
   而非通用社会语境中的含义。
</DOMAIN_ANCHOR>
<CONTEXT>
当前业务场景：{ctx["biz_type"]}
双方角色设定公理：{ctx["exact_roles"]}
当前投资机构/项目名称：{ctx["project_name"]}
被访谈对象（标识）：{ctx["interviewee"]}
当前录音文件标识：{ctx["recording_label"]}
🎯 主理人【结构化狙击清单】（JSON 数组，每项含 quote=原文引用、reason=找茬疑点；优先级高于一切自由文本备注；须对每条执行 1V1 狙击核实）：
{sniper_block}
其它自由备注（补充）：{ctx["session_notes"]}
</CONTEXT>
<KNOWLEDGE_BASE>
{kb_block}
</KNOWLEDGE_BASE>
{_company_bg_block}
{_hist_block}
<TASK>
1【角色防错乱锚定（极度重要）】：你必须结合逐字稿中每个 `[index]` 词段的前后上下文，深度核对各段说话人（Speaker）身份与立场，再推断哪个是投资机构、哪个是被访谈方/发言人。
- **核心纪律**：投资人通常是「抛出压力、质疑、要求数据验证」的一方；发言人通常是「解释业务、回答追问、组织逻辑应对」的一方。
- **严禁将投资人的质询、措辞或口误，判定为发言人的问题来写 Tier 改进建议；板子绝对不能打错人！** 找茬与 improvement_suggestion 必须 100% 锚定在【发言人】的实际应答上。
2【实战复盘与话术重构】：找出被尽调方在回答中的避重就轻、逻辑漏洞或数据打架。指出问题后，你必须指导他们如何完美应对！
3【结构化狙击清单（最高优先级）】：若 <CONTEXT> 中 JSON 狙击清单非空，这是主理人下达的**逐条作战任务**。你必须对**清单中的每一条**（每个 quote + reason 配对）进行 **1 对 1 的深度定向分析**：在下方 user 转写稿中用 **[index]** 做字面锚定，为每一对至少对应一个 RiskPoint（可合并极短重复项，但不可漏项）；reason 即「找茬方向」，必须在该 RiskPoint 的 tier 剖析与 deduction_reason 中明确回应。
   - **字面量防脱轨（强制）**：每个 quote 须在转写中找到**完全一致或逐字包含**的锚点后再选取 `start_word_index` / `end_word_index`；**严禁**无锚点瞎猜。
   - **定向核实专用扩窗纪律（强制）**：针对上述狙击清单产生的 RiskPoint，**严禁**盲目超长扩窗；仅允许覆盖**该原话所在当前问答回合**，`start_word_index` 至 `end_word_index` 时间上**压制在约 60 秒内**。
   - 若狙击清单为空且仅有自由备注，则退化为原「定向核实」逻辑：备注非占位「无」时仍须单独提取 RiskPoint 并遵守上述锚定与 60 秒纪律。
</TASK>
<SCORING_RULE>
【量化扣分引擎（极度重要）】：
你必须采用【自下而上的扣分法】。满分 100 分。对于你找出的每一个风险点，必须给出具体的扣分值（score_deduction）：轻微啰嗦/瑕疵扣 2-5 分；逻辑卡壳/答非所问扣 6-10 分；严重违背 QA 口径/红线翻车扣 11-20 分。最终的 total_score 必须等于 100 减去所有 risk_points 中 score_deduction 的总和！绝不允许凭感觉给出一个固定分数（如 68 分）！
（注：若未提供 QA，仍须按上述档位为每个风险点赋值 score_deduction，并保证 total_score 与扣分总和一致。）
</SCORING_RULE>
<CONSTRAINTS>
必须提供两层剖析：
- Tier 1: 商业逻辑致命伤。
- Tier 2: 如果 <KNOWLEDGE_BASE> 为空或未提供有效内部 QA，必须直接回答「未提供内部 QA，基于行业常识推断」，绝对禁止凭空捏造虚假规定！若有知识库，则对比是否违背标准。

【一、视角绝对锁定（强制红线）】：
- 你的屁股必须绝对坐在“发言人”这一边！
- 绝对禁止输出“建议投资机构接下来如何提问”的内容。
- 所有的改进建议（improvement_suggestion），必须直接针对发言人提供「标准话术示范」（例如：“针对这个问题，建议你下次这样回答：第一...第二...”）。

【商业与法律合规红线（致命底线）】：
- 你给出的标准话术示范，绝对不允许包含任何财务层面的过度承诺、绝对化用语或违反中国现行法律法规（尤其是私募/投融资监管）的内容。
- 严禁教唆发言人说出“绝对保本保息”、“业绩绝对翻倍”等违规话术。
- 如遇极端棘手问题，教发言人用“高情商的外交辞令”化解，或以“数据需会后核实”为由安全着陆，绝不能编造承诺！

【切片精准度与「黄金 60 秒」剪辑（强制）】：
- 不要盲目圈定超长对话。以「直击痛点」为原则，将 `start_word_index` 与 `end_word_index` 所覆盖的交锋时长（按词级起止时间理解）引导在 **45–60 秒**左右。
- **截取艺术**：建议包含【问题最尖锐的末尾约 10 秒】+【回答最核心的约 40–50 秒】。若 Q&A 确实漫长且精彩，允许适度放宽，但 **绝对禁止超过 180 秒的无信息量无效圈地**；系统侧会对物理音频硬截断至 180 秒，过度圈地只会丢失后半段听感。

【二、场记与索引纪律】：`original_text` 在落盘时由系统按 ASR 词索引**物理覆写**，你仍须输出与 `start_word_index`–`end_word_index` 范围一致的摘录；可带 [index] 以利对齐。禁止书面化润色、禁止抄 QA 冒充实录。

【三、扣分说明与索引边界（强制）】：
- 根级字段 total_score_deduction_reason：结合总分与 <KNOWLEDGE_BASE>，说明主要扣分维度与依据。
- 每个 risk_points[] 元素的 deduction_reason：结合参考QA具体指出偏离了哪条口径；若无有效QA可写「未提供可对齐的QA条款，扣分依据为行业尽调常识」。
- 字段 is_manual_entry 仅允许为 false。
- 字段 needs_refinement 仅允许为 false。
- 字段 refinement_note 仅允许为空字符串 ""。
- 【极度重要】：输出 start_word_index 和 end_word_index 时切忌只圈出错片段的几个词；必须向外扩展索引边界，包含投资人完整提问与创始人完整回答段落，使切割音频能呈现完整交锋语境。（**例外**：因 <TASK> 第 3 条「🎯 定向核实」单独提取的 RiskPoint 必须遵守该条中的**字面量锚定**与**约 60 秒内、单回合**纪律，禁止套用本条无边界扩展。）

必须严格按照 JSON Schema 输出，start/end index 必须精确。

【四、公司背景与本次指令冲突仲裁（COMPANY_BACKGROUND）】：
- 若 SNIPER_TARGETS（狙击清单）与 COMPANY_BACKGROUND（公司常态化背景）存在矛盾，以 SNIPER_TARGETS 指令为准，并在该风险点的 deduction_reason 中注明差异。
</CONSTRAINTS>
<JSON_SCHEMA>
{schema_str}
</JSON_SCHEMA>
<FINAL_REMINDER>
【最后重申你的核心纪律（至关重要）】：
1. 必须绝对站在发言人视角给话术，严禁当投资人的军师！
2. `original_text` 须与索引范围一致；后端按 ASR 强制覆写落盘，禁止抄 QA。
3. 必须严格执行量化扣分引擎：每个 risk_points[] 填写 score_deduction，且 total_score = 100 - Σscore_deduction！
4. 话术建议坚守合规底线，严禁过度承诺！
</FINAL_REMINDER>
"""


def _clamp_word_span(start_word_index: int, end_word_index: int, n_words: int) -> tuple[int, int] | None:
    if n_words <= 0:
        return None
    sw = int(start_word_index)
    ew = int(end_word_index)
    sw = max(0, min(sw, n_words - 1))
    ew = max(0, min(ew, n_words - 1))
    if sw > ew:
        sw, ew = ew, sw
    return sw, ew


def _build_risk_scan_system_prompt(
    schema_str: str,
    explicit_context: dict[str, Any] | None,
    qa_text: str,
    company_background: str = "",
    historical_memories: list[ExecutiveMemory] | None = None,
) -> str:
    """V9.6 阶段一：仅扫描靶点，不要求输出完整 Tier / improvement。"""
    ctx = _normalize_explicit_context(explicit_context)
    kb = (qa_text or "").strip()
    kb_block = kb if kb else "未提供参考QA知识库。"
    sniper_block = _format_sniper_block(ctx["sniper_targets_json"])
    _bg_use = (company_background or "").strip()
    _company_bg_block = (
        f"<COMPANY_BACKGROUND>\n{_bg_use}\n</COMPANY_BACKGROUND>"
        if _bg_use
        else ""
    )
    _hist_use = _format_historical_profile_block(historical_memories)
    _hist_block = (
        f"<HISTORICAL_PROFILE>\n{_hist_use}\n</HISTORICAL_PROFILE>"
        if _hist_use
        else ""
    )
    return f"""你是拥有 15 年经验的尽调与路演复盘架构师。当前任务为**阶段一：找靶子**。
你只负责从带 [index] 词索引的转写稿中扫描**发言人侧**应答风险位置，输出结构化靶点列表；不要写长篇改进话术（阶段二会逐点深评）。

<CONTEXT>
业务场景：{ctx["biz_type"]}
双方角色：{ctx["exact_roles"]}
项目：{ctx["project_name"]}
被访谈对象：{ctx["interviewee"]}
录音标识：{ctx["recording_label"]}
狙击清单（JSON）：{sniper_block}
备注：{ctx["session_notes"]}
</CONTEXT>
<KNOWLEDGE_BASE>
{kb_block}
</KNOWLEDGE_BASE>
{_company_bg_block}
{_hist_block}
<TASK>
1. 推断 scene_analysis（场景类型与说话人角色关系）。
2. 列出 risk targets：每项含 start_word_index、end_word_index、problem_description（简练）、risk_type（类型标签）。
3. 板子打在【发言人】应答上；勿把投资人的质问当成发言人的 risk。
4. 索引必须来自转写中出现的 [index]；单靶点覆盖时长宜在约 45–60 秒量级（狙击清单项仍遵守原 60 秒纪律）。
5. 仅输出符合 JSON Schema 的一个对象，键为 scene_analysis、targets。
</TASK>
<JSON_SCHEMA>
{schema_str}
</JSON_SCHEMA>
"""


def _build_deep_single_risk_system_prompt(
    schema_str: str,
    explicit_context: dict[str, Any] | None,
    qa_text: str,
    company_background: str = "",
    historical_memories: list[ExecutiveMemory] | None = None,
) -> str:
    """V9.6 阶段二：单靶点深度评估 — 军工/硬科技 IR 顶尖视角。"""
    ctx = _normalize_explicit_context(explicit_context)
    kb_block = (qa_text or "").strip() or "未提供参考QA知识库。"
    _bg_use = (company_background or "").strip()
    _company_bg_block = (
        f"<COMPANY_BACKGROUND>\n{_bg_use}\n</COMPANY_BACKGROUND>"
        if _bg_use
        else ""
    )
    _hist_use = _format_historical_profile_block(historical_memories)
    _hist_block = (
        f"<HISTORICAL_PROFILE>\n{_hist_use}\n</HISTORICAL_PROFILE>"
        if _hist_use
        else ""
    )
    return f"""你是**军工 / 硬科技投资界顶尖 IR 与资本市场沟通专家**，熟悉尽调话术、合规边界与技术产品叙事。
当前为**阶段二：单点爆破** — 仅针对 user 给出的**单个风险靶点**，输出完整 RiskPoint JSON（含 tier1/tier2/improvement_suggestion 等所有必填字段）。

<CONTEXT>
业务场景：{ctx["biz_type"]}
双方角色：{ctx["exact_roles"]}
项目：{ctx["project_name"]}
被访谈对象：{ctx["interviewee"]}
</CONTEXT>
<KNOWLEDGE_BASE>
{kb_block}
</KNOWLEDGE_BASE>
{_company_bg_block}
{_hist_block}
<TASK>
1. improvement_suggestion 必须**专业、一针见血**，给出发言人可直接复用的应答结构或示例句式（遵守私募合规，禁止保本保收益等表述）。
2. 必须严格执行量化扣分：填写 score_deduction；deduction_reason 说明扣分依据。
3. start_word_index / end_word_index 必须与 user 给出的靶点范围一致（系统会再次强制校验）。
4. is_manual_entry=false，needs_refinement=false，refinement_note=""。
5. original_text 须与该索引范围的实录一致；禁止抄 QA 冒充。
6. 仅输出一个 RiskPoint JSON 对象。
</TASK>
<JSON_SCHEMA>
{schema_str}
</JSON_SCHEMA>
"""


def _make_client(model_key: str) -> tuple[OpenAI, str]:
    if model_key not in ROUTER:
        raise ValueError(f"未知模型键: {model_key}，应为 deepseek / kimi / qwen")
    cfg = ROUTER[model_key]
    api_key = os.getenv(cfg["api_key_env"])
    if not api_key:
        raise ValueError(f"未设置环境变量 {cfg['api_key_env']}")
    client = OpenAI(
        base_url=cfg["base_url"],
        api_key=api_key,
    )
    return client, cfg["model"]


def _compose_total_deduction_reason(risk_points: list[RiskPoint], total_ded: int) -> str:
    if not risk_points:
        return "未发现显著风险点，未执行扣分。"
    return (
        f"共 {len(risk_points)} 个风险点，合计扣分 {total_ded} 分；"
        "各条目依据见 deduction_reason。"
    )


def deep_evaluate_single_risk(
    words: List[TranscriptionWord],
    target: RiskTargetCandidate,
    *,
    model_choice: str = "deepseek",
    explicit_context: dict[str, Any] | None = None,
    qa_text: str = "",
    company_background: str = "",
    historical_memories: list[ExecutiveMemory] | None = None,
) -> RiskPoint:
    """
    V9.6 阶段二：针对单个靶点调用 LLM，生成完整 RiskPoint（军工 / 硬科技 IR 深评视角）。
    """
    if model_choice not in JUDGE_MODEL_KEYS:
        raise ValueError('model_choice 必须是 "deepseek"、"kimi" 或 "qwen"')
    n = len(words)
    sw = int(target.start_word_index)
    ew = int(target.end_word_index)
    span = _clamp_word_span(sw, ew, n)
    if span is None:
        raise ValueError("词列表为空，无法深评")
    sw, ew = span
    seg_start = max(0, sw - 40)
    seg_end = min(n - 1, ew + 40)
    segment_words = words[seg_start : seg_end + 1]
    segment_text = " ".join(f"[{w.word_index}]{w.text}" for w in segment_words)

    qa_use = (qa_text or "").strip()
    qa_use, _ = truncate_qa_text(qa_use, MAX_QA_CHARS)

    schema_str = json.dumps(RiskPoint.model_json_schema(), ensure_ascii=False)
    system_prompt = _build_deep_single_risk_system_prompt(
        schema_str,
        explicit_context,
        qa_use,
        company_background,
        historical_memories=historical_memories,
    )
    target_json = json.dumps(
        {
            "start_word_index": sw,
            "end_word_index": ew,
            "problem_description": target.problem_description,
            "risk_type": target.risk_type,
        },
        ensure_ascii=False,
    )
    user_prompt = (
        f"【风险靶点】\n{target_json}\n\n"
        "以下是该靶点周边的转写片段（含 [索引] 词锚）：\n\n"
        f"{segment_text}\n\n"
        "请输出单个 RiskPoint JSON 对象。"
    )

    client, model_name = _make_client(model_choice)
    max_tokens = MAX_COMPLETION_TOKENS_BY_MODEL.get(model_name, 8192)

    def _chat_once():
        return client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.25,
            max_tokens=max_tokens,
        )

    try:
        response = run_with_backoff(
            _chat_once,
            logger=logger,
            operation=f"deep_evaluate_single_risk ({model_choice})",
        )
    except APIError as e:
        raise RuntimeError(f"深评 LLM API 失败: {e}") from e

    choice = response.choices[0] if response.choices else None
    if choice is None or not choice.message or choice.message.content is None:
        raise RuntimeError("深评 LLM 返回空内容")

    raw_json = choice.message.content.strip()
    try:
        rp = RiskPoint.model_validate_json(raw_json)
    except ValidationError:
        try:
            outer = json.loads(raw_json)
            inner = next((v for v in outer.values() if isinstance(v, dict)), outer)
            rp = RiskPoint.model_validate(inner)
        except Exception as e:
            raise ValueError(f"深评结果不符合 RiskPoint 契约: {e}\n原始: {raw_json[:800]}") from e

    return rp.model_copy(
        update={
            "start_word_index": sw,
            "end_word_index": ew,
            "needs_refinement": False,
            "refinement_note": "",
        }
    )


def evaluate_pitch(
    words: List[TranscriptionWord],
    model_choice: str = "deepseek",
    *,
    explicit_context: dict[str, Any] | None = None,
    qa_text: str = "",
    company_background: str = "",
    on_notice: Callable[[str], None] | None = None,
    historical_memories: list[ExecutiveMemory] | None = None,
) -> AnalysisReport:
    """
    V9.6：两阶段评估 — 先 scan_risk_targets（找靶子），再对每靶点 deep_evaluate_single_risk（单点爆破）。
    explicit_context 建议包含：biz_type, exact_roles, project_name, interviewee；
    可选 session_notes（本段备注）、recording_label（录音文件名标识）。
    """
    if model_choice not in JUDGE_MODEL_KEYS:
        raise ValueError('model_choice 必须是 "deepseek"、"kimi" 或 "qwen"')

    transcript = format_transcript_for_llm(words)
    if not transcript.strip():
        raise ValueError("转写词列表为空，无法评估")

    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:MAX_TRANSCRIPT_CHARS]
        logger.warning(
            "转写已超过 MAX_TRANSCRIPT_CHARS=%d，已截取前缀以稳定上下文",
            MAX_TRANSCRIPT_CHARS,
        )

    qa_use = (qa_text or "").strip()
    qa_use, qa_truncated = truncate_qa_text(qa_use, MAX_QA_CHARS)
    if qa_truncated:
        warn_msg = (
            "⚠️ QA 补充材料字数超载（超过3万字），为防止 AI 崩溃，已截取核心头尾条款"
        )
        logger.warning("%s", warn_msg)
        if callable(on_notice):
            try:
                on_notice(warn_msg)
            except Exception:
                logger.exception("on_notice 回调失败")

    bg_use, _ = truncate_company_background(company_background or "")

    scan_schema = json.dumps(RiskScanResult.model_json_schema(), ensure_ascii=False)
    scan_system = _build_risk_scan_system_prompt(
        scan_schema,
        explicit_context,
        qa_use,
        bg_use,
        historical_memories=historical_memories,
    )
    scan_user = (
        "以下是本场沟通转写（每个词前有 [索引]，targets 中索引必须来自这些锚点）：\n\n"
        f"{transcript}"
    )

    client, model_name = _make_client(model_choice)
    max_tokens = MAX_COMPLETION_TOKENS_BY_MODEL.get(model_name, 8192)

    logger.info(
        "V9.6 两阶段评估: scan router_key=%s model=%s 词数=%d",
        model_choice,
        model_name,
        len(words),
    )

    t_api0 = time.monotonic()

    def _scan_chat():
        return client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": scan_system},
                {"role": "user", "content": scan_user},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=max_tokens,
        )

    try:
        scan_resp = run_with_backoff(
            _scan_chat,
            logger=logger,
            operation=f"risk_scan ({model_choice})",
        )
    except APIError as e:
        logger.exception("阶段一扫描 API 失败")
        raise RuntimeError(f"LLM API 请求失败: {e}") from e
    except Exception as e:
        logger.exception("阶段一扫描异常")
        raise RuntimeError(f"LLM 调用异常: {e}") from e

    logger.info(
        "阶段一 scan 成功 model=%s 耗时=%.2fs",
        model_name,
        time.monotonic() - t_api0,
    )

    sch = scan_resp.choices[0] if scan_resp.choices else None
    if sch is None or not sch.message or sch.message.content is None:
        raise RuntimeError("阶段一 LLM 返回空内容")

    raw_scan = sch.message.content.strip()
    try:
        scan = RiskScanResult.model_validate_json(raw_scan)
    except (ValidationError, Exception) as e:
        # 尝试从截断 JSON 中抢救 scene_analysis + targets
        scan = _salvage_risk_scan_result(raw_scan, e)
        if scan is None:
            logger.error("RiskScanResult 无法抢救: %s\n原始: %s", e, raw_scan[:2000])
            raise ValueError(f"模型输出不符合 RiskScanResult 契约: {e}") from e
        warn_msg = "⚠️ 阶段一扫描结果 JSON 被截断，已抢救 scene_analysis，靶点列表可能不完整。"
        logger.warning(warn_msg)
        if callable(on_notice):
            try:
                on_notice(warn_msg)
            except Exception:
                pass

    n_words = len(words)

    # 预处理靶点：clamp 索引并过滤越界项，保留原始序号供结果排序
    valid_targets: list[tuple[int, RiskTargetCandidate]] = []
    for i, t in enumerate(scan.targets):
        span = _clamp_word_span(t.start_word_index, t.end_word_index, n_words)
        if span is None:
            continue
        sw, ew = span
        valid_targets.append((i, RiskTargetCandidate(
            start_word_index=sw,
            end_word_index=ew,
            problem_description=t.problem_description,
            risk_type=t.risk_type,
        )))

    # 并发深评：每个靶点独立 LLM 调用，I/O 密集型，线程安全
    # 最多 6 路并发（防止同时涌入过多 API 请求导致限流）
    max_workers = min(len(valid_targets), 6) if valid_targets else 1
    results: dict[int, RiskPoint] = {}

    def _eval_one(idx: int, tc: RiskTargetCandidate) -> tuple[int, RiskPoint | None]:
        try:
            rp = deep_evaluate_single_risk(
                words,
                tc,
                model_choice=model_choice,
                explicit_context=explicit_context,
                qa_text=qa_use,
                company_background=bg_use,
                historical_memories=historical_memories,
            )
            return idx, rp
        except Exception:
            logger.exception("靶点 %d 深评失败，已跳过该条", idx)
            return idx, None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_eval_one, idx, tc): idx for idx, tc in valid_targets}
        for future in as_completed(futures):
            idx, rp = future.result()
            if rp is not None:
                results[idx] = rp

    # 按原始靶点顺序排列（保证报告稳定性）
    risk_points: list[RiskPoint] = [results[idx] for idx in sorted(results.keys())]

    total_ded = sum(int(r.score_deduction or 0) for r in risk_points)
    total_ded = min(100, max(0, total_ded))
    score = max(0, min(100, 100 - total_ded))
    reason = _compose_total_deduction_reason(risk_points, total_ded)

    return AnalysisReport(
        scene_analysis=scan.scene_analysis,
        total_score=score,
        total_score_deduction_reason=reason,
        risk_points=risk_points,
    )


def distill_executive_memory_from_diff(
    original: str,
    refined: str,
    tag: str,
) -> ExecutiveMemory:
    """
    V8.6 / V8.6.1：对比改写前后文本，提炼可复用的「易错要点 + 标准口径」写入错题本。
    **固定走 DeepSeek**（`deepseek-chat`），与主评委同一底座；调用方须已通过防噪门。
    """
    o = (original or "").strip()
    r = (refined or "").strip()
    cap = 12_000
    if len(o) > cap:
        o = o[:cap] + "…"
    if len(r) > cap:
        r = r[:cap] + "…"
    tg = (tag or "").strip() or "default"

    distill_schema = json.dumps(
        {
            "type": "object",
            "required": ["raw_text", "correction"],
            "properties": {
                "raw_text": {
                    "type": "string",
                    "description": "用一句话概括原表述中的踩坑点或不当口径（非逐字抄录）",
                },
                "correction": {
                    "type": "string",
                    "description": "业务逻辑层面的纠正建议或高管应遵循的表述偏好/黄金口径",
                },
                "weight": {
                    "type": "number",
                    "description": "重要性 0~5，默认 1",
                },
            },
        },
        ensure_ascii=False,
    )

    system_prompt = f"""你是投后复盘与高管表达教练。主理人刚完成一段「AI 改写或人工深度修订」。
请从「业务逻辑与沟通策略」角度提炼一条可复用记忆，用于未来同场景预防。

要求：
1. raw_text：概括**原表述的问题类型或错误倾向**（不要逐字复制长文，不超过 200 字）。
2. correction：给出**应遵循的口径、结构或偏好**（可含简短示例句式，不超过 300 字）。
3. weight：0~5 的浮点数，越重要越高，默认 1.0。
4. 忽略纯错别字、标点或无关痛痒的润色；聚焦业务与话术逻辑。
5. 仅输出一个 JSON 对象，键为 raw_text、correction、weight。

JSON 形状约束：
{distill_schema}"""

    user_prompt = (
        f"高管/标签上下文：{tg}\n\n"
        f"<BEFORE>\n{o}\n</BEFORE>\n\n"
        f"<AFTER>\n{r}\n</AFTER>\n\n"
        "请输出 JSON。"
    )

    client, model_name = _make_client("deepseek")
    max_tokens = MAX_COMPLETION_TOKENS_BY_MODEL.get(model_name, 8192)

    def _chat_once():
        return client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=max_tokens,
        )

    try:
        response = run_with_backoff(
            _chat_once,
            logger=logger,
            operation="distill_executive_memory_from_diff (deepseek)",
        )
    except APIError as e:
        raise RuntimeError(f"记忆提炼 LLM API 失败: {e}") from e

    choice = response.choices[0] if response.choices else None
    if choice is None or not choice.message or choice.message.content is None:
        raise RuntimeError("记忆提炼 LLM 返回空内容")

    raw_json = choice.message.content.strip()
    try:
        data = json.loads(raw_json)
        if not isinstance(data, dict):
            raise ValueError("根节点须为对象")
        inner = next((v for v in data.values() if isinstance(v, dict)), data)
        raw_text = str(inner.get("raw_text", "")).strip()
        correction = str(inner.get("correction", "")).strip()
        if not raw_text or not correction:
            raise ValueError("raw_text/correction 不能为空")
        w = float(inner.get("weight", 1.0))
        w = max(0.0, min(5.0, w))
        return ExecutiveMemory(tag=tg, raw_text=raw_text, correction=correction, weight=w)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        raise ValueError(f"记忆提炼 JSON 无效: {e}\n原始: {raw_json[:800]}") from e


def load_transcription_words(path: Path) -> List[TranscriptionWord]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"JSON 根节点必须是数组: {path}")
    out: List[TranscriptionWord] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i} 项不是对象")
        out.append(TranscriptionWord.model_validate(item))
    return out


def refine_risk_point(
    rp_dict: dict[str, Any],
    words: List[TranscriptionWord],
    *,
    model_choice: str = "deepseek",
    explicit_context: dict[str, Any] | None = None,
    qa_text: str = "",
    refinement_note: str = "",
) -> RiskPoint:
    """
    局部精炼：对单个风险点调用 LLM 深度重写。
    提取该条目对应的词段作为上下文，注入主理人批示意见，返回精炼后的 RiskPoint。
    词级索引（start/end_word_index）在 prompt 中要求 LLM 保持一致。
    """
    if model_choice not in JUDGE_MODEL_KEYS:
        raise ValueError('model_choice 必须是 "deepseek"、"kimi" 或 "qwen"')
    ctx = _normalize_explicit_context(explicit_context)
    sw = int(rp_dict.get("start_word_index", 0))
    ew = int(rp_dict.get("end_word_index", 0))

    # 提取对应词段（向前后各扩 5 词作上下文）
    n = len(words)
    seg_start = max(0, sw - 5)
    seg_end = min(n - 1, ew + 5)
    segment_words = words[seg_start: seg_end + 1]
    segment_text = " ".join(f"[{w.word_index}]{w.text}" for w in segment_words)

    rp_json_str = json.dumps(rp_dict, ensure_ascii=False, indent=2)
    note_block = (refinement_note or "").strip() or "（无额外批示，请基于逐字稿和 QA 自行深化分析）"
    kb_block = (qa_text or "").strip() or "未提供参考QA知识库。"
    schema_str = json.dumps(RiskPoint.model_json_schema(), ensure_ascii=False)

    system_prompt = f"""你是一位拥有15年一线投行经验的「顶级金牌路演教练」。
主理人对 AI 初稿中的一个风险点不满意，需要你对其进行深度精炼。

<CONTEXT>
业务场景：{ctx["biz_type"]}
双方角色：{ctx["exact_roles"]}
项目名称：{ctx["project_name"]}
被访谈对象：{ctx["interviewee"]}
</CONTEXT>

<KNOWLEDGE_BASE>
{kb_block}
</KNOWLEDGE_BASE>

<TASK>
对以下风险点进行深度精炼。要求：
1. 保持 start_word_index={sw} / end_word_index={ew} 不变（除非主理人明确要求调整）
2. 所有分析必须立足于【发言人】视角
3. improvement_suggestion 必须给出具体话术示范
4. is_manual_entry 必须为 false
5. needs_refinement 必须为 false
6. refinement_note 必须为空字符串 ""
7. 严格按照 JSON Schema 输出单个 RiskPoint 对象
</TASK>

<ORIGINAL_RISK_POINT>
{rp_json_str}
</ORIGINAL_RISK_POINT>

<PRINCIPAL_INSTRUCTION>
{note_block}
</PRINCIPAL_INSTRUCTION>

<JSON_SCHEMA>
{schema_str}
</JSON_SCHEMA>"""

    user_prompt = (
        f"以下是该风险点对应的逐字稿片段（词索引 {seg_start}–{seg_end}）：\n\n{segment_text}\n\n"
        "请输出精炼后的完整 RiskPoint JSON 对象。"
    )

    client, model_name = _make_client(model_choice)
    max_tokens = MAX_COMPLETION_TOKENS_BY_MODEL.get(model_name, 8192)

    def _chat_once():
        return client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=max_tokens,
        )

    try:
        response = run_with_backoff(
            _chat_once,
            logger=logger,
            operation=f"refine_risk_point ({model_choice})",
        )
    except APIError as e:
        raise RuntimeError(f"精炼 LLM API 失败: {e}") from e

    choice = response.choices[0] if response.choices else None
    if choice is None or not choice.message or choice.message.content is None:
        raise RuntimeError("精炼 LLM 返回空内容")

    raw_json = choice.message.content.strip()
    try:
        rp = RiskPoint.model_validate_json(raw_json)
    except (ValidationError, Exception) as e:
        # 尝试将外层包装剥除（有时 LLM 会套一层 {"risk_point": {...}}）
        try:
            outer = json.loads(raw_json)
            inner = next(
                (v for v in outer.values() if isinstance(v, dict)), outer
            )
            rp = RiskPoint.model_validate(inner)
        except Exception:
            raise ValueError(f"精炼结果不符合 RiskPoint 契约: {e}\n原始: {raw_json[:500]}") from e

    # 确保词索引一致性：若 LLM 偏离，强制修正
    rp = rp.model_copy(update={
        "start_word_index": sw,
        "end_word_index": ew,
        "needs_refinement": False,
        "refinement_note": "",
    })
    return rp


def refine_single_risk_point(
    risk_point_id: str,
    user_instruction: str,
    context_text: str,
    original_suggestion: str,
    *,
    model_choice: str = "deepseek",
    explicit_context: dict[str, Any] | None = None,
    qa_text: str = "",
) -> MagicRefinementResult:
    """
    V9.6「魔法对话框」后端：按业务员微调指令重写单条 improvement_suggestion。
    返回带 risk_point_id 的结构化结果，供前端写回 Session / 草稿。
    """
    if model_choice not in JUDGE_MODEL_KEYS:
        raise ValueError('model_choice 必须是 "deepseek"、"kimi" 或 "qwen"')
    inst = (user_instruction or "").strip()
    if not inst:
        raise ValueError("user_instruction 不能为空")
    rid = (risk_point_id or "").strip() or "unknown"

    # context_text 截断保护：超长时截取头部（约 4000 字），防 token 超限
    _ctx_raw = (context_text or "").strip()
    _ctx_use = _ctx_raw[:4000] if len(_ctx_raw) > 4000 else _ctx_raw
    if len(_ctx_raw) > 4000:
        logger.warning(
            "refine_single_risk_point: context_text 超过 4000 字（实际 %d 字），已截取头部",
            len(_ctx_raw),
        )

    ctx = _normalize_explicit_context(explicit_context)
    kb_block = (qa_text or "").strip() or "未提供参考QA知识库。"
    mini_schema = json.dumps(
        {
            "type": "object",
            "required": ["improvement_suggestion"],
            "properties": {
                "improvement_suggestion": {
                    "type": "string",
                    "description": "按用户指令重写后的完整改进建议正文",
                }
            },
        },
        ensure_ascii=False,
    )

    system_prompt = f"""你是军工/硬科技投资界顶尖 IR 专家。主理人要对一条「改进建议」做局部重写。
仅输出一个 JSON 对象，且必须包含键 improvement_suggestion（字符串）。

<CONTEXT>
业务场景：{ctx["biz_type"]}
双方角色：{ctx["exact_roles"]}
项目：{ctx["project_name"]}
被访谈对象：{ctx["interviewee"]}
</CONTEXT>
<KNOWLEDGE_BASE>
{kb_block}
</KNOWLEDGE_BASE>

<TASK>
1. 严格服从 <USER_INSTRUCTION>，在保留事实与合规的前提下重写建议正文。
2. 遵守私募合规：禁止保本保收益、禁止过度承诺。
3. 可引用 <CONTEXT_TEXT> 中的事实，勿捏造录音中不存在的内容。
</TASK>

JSON 形状约束：
{mini_schema}"""

    user_prompt = (
        f"<ORIGINAL_SUGGESTION>\n{(original_suggestion or '').strip()}\n</ORIGINAL_SUGGESTION>\n\n"
        f"<CONTEXT_TEXT>\n{_ctx_use}\n</CONTEXT_TEXT>\n\n"
        f"<USER_INSTRUCTION>\n{inst}\n</USER_INSTRUCTION>\n\n"
        "请仅输出 JSON。"
    )

    client, model_name = _make_client(model_choice)
    max_tokens = MAX_COMPLETION_TOKENS_BY_MODEL.get(model_name, 8192)

    def _chat_once():
        return client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.35,
            max_tokens=max_tokens,
        )

    try:
        response = run_with_backoff(
            _chat_once,
            logger=logger,
            operation=f"refine_single_risk_point ({model_choice})",
        )
    except APIError as e:
        raise RuntimeError(f"魔法对话框 LLM API 失败: {e}") from e

    choice = response.choices[0] if response.choices else None
    if choice is None or not choice.message or choice.message.content is None:
        raise RuntimeError("魔法对话框 LLM 返回空内容")

    raw_json = choice.message.content.strip()
    try:
        data = json.loads(raw_json)
        if not isinstance(data, dict):
            raise ValueError("根须为对象")
        inner = next((v for v in data.values() if isinstance(v, dict)), data)
        sug = str(inner.get("improvement_suggestion", "")).strip()
        if not sug:
            raise ValueError("improvement_suggestion 为空")
        return MagicRefinementResult(risk_point_id=rid, improvement_suggestion=sug)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        raise ValueError(f"魔法对话框 JSON 无效: {e}\n原始: {raw_json[:800]}") from e


def polish_manual_risk_point(
    raw_description: str,
    *,
    model_choice: str = "deepseek",
    explicit_context: dict[str, Any] | None = None,
    qa_text: str = "",
) -> RiskPoint:
    """
    AI 润色：将主理人的原始文字描述结构化为标准 RiskPoint 格式并插入报告。
    返回的 RiskPoint 中 is_manual_entry=True、start/end_word_index=0。
    """
    desc = (raw_description or "").strip()
    if not desc:
        raise ValueError("描述不能为空，请填写至少一句话再调用 LLM 润色。")
    if model_choice not in JUDGE_MODEL_KEYS:
        raise ValueError('model_choice 必须是 "deepseek"、"kimi" 或 "qwen"')

    ctx = _normalize_explicit_context(explicit_context)
    kb_block = (qa_text or "").strip() or "未提供参考QA知识库。"
    schema_str = json.dumps(RiskPoint.model_json_schema(), ensure_ascii=False)

    system_prompt = f"""你是一位拥有15年一线投行经验的「顶级金牌路演教练」。
主理人手动输入了一段观察，请将其结构化为标准的风险点分析格式。

<CONTEXT>
业务场景：{ctx["biz_type"]}
双方角色：{ctx["exact_roles"]}
项目名称：{ctx["project_name"]}
被访谈对象：{ctx["interviewee"]}
</CONTEXT>

<KNOWLEDGE_BASE>
{kb_block}
</KNOWLEDGE_BASE>

<TASK>
将主理人的原始观察扩写为完整的 RiskPoint 分析。要求：
1. is_manual_entry 必须为 true（这是人工标记的遗漏点，无词级音频切片）
2. start_word_index 和 end_word_index 均必须为 0
3. needs_refinement 必须为 false
4. refinement_note 必须为空字符串 ""
5. 在 improvement_suggestion 中给出具体话术示范
6. 根据分析严重程度合理设置 risk_level 与 score_deduction
7. 坚守合规底线，严禁过度承诺
8. 严格按照 JSON Schema 输出单个 RiskPoint 对象
</TASK>

<JSON_SCHEMA>
{schema_str}
</JSON_SCHEMA>"""

    user_prompt = f"以下是主理人的原始观察，请将其结构化：\n\n{desc}"

    client, model_name = _make_client(model_choice)
    max_tokens = MAX_COMPLETION_TOKENS_BY_MODEL.get(model_name, 8192)

    def _chat_once():
        return client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=max_tokens,
        )

    try:
        response = run_with_backoff(
            _chat_once,
            logger=logger,
            operation=f"polish_manual_risk_point ({model_choice})",
        )
    except APIError as e:
        raise RuntimeError(f"润色 LLM API 失败: {e}") from e

    choice = response.choices[0] if response.choices else None
    if choice is None or not choice.message or choice.message.content is None:
        raise RuntimeError("润色 LLM 返回空内容")

    raw_json = choice.message.content.strip()
    try:
        rp = RiskPoint.model_validate_json(raw_json)
    except (ValidationError, Exception) as e:
        try:
            outer = json.loads(raw_json)
            inner = next(
                (v for v in outer.values() if isinstance(v, dict)), outer
            )
            rp = RiskPoint.model_validate(inner)
        except Exception:
            raise ValueError(f"润色结果不符合 RiskPoint 契约: {e}\n原始: {raw_json[:500]}") from e

    # 强制人工条目标记
    rp = rp.model_copy(update={
        "is_manual_entry": True,
        "start_word_index": 0,
        "end_word_index": 0,
        "needs_refinement": False,
        "refinement_note": "",
    })
    return rp


def _save_report(path: Path, report: AnalysisReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("已写入: %s", path)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stdout,
    )

    transcription_path = get_writable_app_root() / "output" / "real_transcription.json"
    if not transcription_path.is_file():
        raise SystemExit(f"未找到转写文件: {transcription_path}")

    try:
        words = load_transcription_words(transcription_path)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as e:
        raise SystemExit(f"加载转写失败: {e}") from e

    logger.info("已加载转写词数: %d", len(words))

    selected = choose_model_with_timeout(3)
    label = DISPLAY_NAME.get(selected, selected)
    print(f"正在召唤 {label} 大脑，阅读分析中...", flush=True)

    cli_ctx = {
        "biz_type": "CLI默认",
        "exact_roles": "未指定",
        "project_name": "未指定",
        "interviewee": "未指定",
        "session_notes": "无",
        "sniper_targets_json": "[]",
        "recording_label": "未指定",
    }

    try:
        report = evaluate_pitch(
            words,
            model_choice=selected,
            explicit_context=cli_ctx,
            qa_text="",
        )
    except Exception as e:
        logger.exception("评估失败")
        print(f"失败: {e}", file=sys.stderr, flush=True)
        raise SystemExit(1) from e

    out_path = get_writable_app_root() / "output" / "real_analysis_report.json"
    _save_report(out_path, report)
    print(f"已保存: {out_path}", flush=True)
