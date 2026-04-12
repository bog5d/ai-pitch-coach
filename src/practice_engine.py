"""
会前演练模式引擎 — V10.3 P2.1

功能：AI 扮演投资机构投资人，与创始人进行文字模拟问答。
每轮：AI 提问 → 创始人文字作答 → AI 评分 + 反馈 + 下一问。

设计原则：
- start_practice_session()  纯数据初始化，LLM 生成开场问题
- evaluate_answer_and_next() 评分当前答案 + 生成下一问
- get_session_summary()      纯数据汇总，无 LLM
- _call_llm_question()       LLM 生成问题（可单独 mock）
- _call_llm_evaluate()       LLM 评分（可单独 mock）
- LLM 不可用时静默降级，绝不崩溃
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

from institution_profiler import build_institution_profile
from runtime_paths import get_writable_app_root

load_dotenv(get_writable_app_root() / ".env")
logger = logging.getLogger(__name__)

# 评分低于此分数视为「弱项」，归入 summary.weak_areas
_WEAK_SCORE_THRESHOLD = 60


# ── LLM 调用层（可单独 Mock） ──────────────────────────────────────────────────

def _get_llm_client() -> OpenAI:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未设置")
    return OpenAI(base_url="https://api.deepseek.com", api_key=api_key)


def _call_llm_question(
    institution_profile: dict,
    conversation_history: list[dict],
    *,
    system_hint: str = "",
) -> str:
    """
    调用 LLM 生成下一个投资人问题。

    conversation_history: [{"role": "investor"|"founder", "content": "..."}]
    返回问题字符串。
    """
    client = _get_llm_client()

    name = institution_profile.get("canonical_name", "投资机构")
    top_risks = institution_profile.get("top_risk_types", [])
    killer_qs = institution_profile.get("killer_questions", [])

    risk_text = "、".join(r["risk_type"] for r in top_risks[:3]) or "商业模式、增长逻辑"
    killer_text = "\n".join(f"- {q}" for q in killer_qs[:3]) or "（暂无历史数据）"

    history_text = ""
    for turn in conversation_history[-6:]:  # 最近 3 轮
        role_label = "投资人" if turn["role"] == "investor" else "创始人"
        history_text += f"\n{role_label}：{turn['content']}"

    hint_text = f"\n附加提示：{system_hint}" if system_hint else ""

    prompt = f"""你正在扮演「{name}」的投资经理，正在对一家初创公司进行投资尽调访谈。

你的投资风格特征：
- 最关注的风险类型：{risk_text}
- 你的历史致命问题：
{killer_text}

当前对话记录：
{history_text or "（对话刚开始）"}
{hint_text}

请生成**下一个犀利但合理的问题**，聚焦于该机构最关心的风险维度。
要求：
- 用中文
- 1-2句话，简洁直接
- 基于对话上下文，避免重复
- 不要以"问题："开头，直接问

只输出问题本身，不要任何解释。"""

    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150,
        temperature=0.8,
    )
    return (resp.choices[0].message.content or "").strip()


def _call_llm_evaluate(
    question: str,
    answer: str,
    institution_profile: dict,
) -> dict:
    """
    调用 LLM 对回答进行评分。

    返回 {"score": int(0-100), "feedback": str}
    失败时抛出异常（由调用层降级处理）。
    """
    client = _get_llm_client()

    name = institution_profile.get("canonical_name", "投资机构")
    top_risks = institution_profile.get("top_risk_types", [])
    risk_text = "、".join(r["risk_type"] for r in top_risks[:3]) or "商业逻辑"

    prompt = f"""你是「{name}」的资深投资合伙人，正在评价被访公司创始人的回答质量。

**投资人问题**：{question}

**创始人回答**：{answer}

**你最关注的风险维度**：{risk_text}

请从以下维度评价这个回答（满分 100）：
1. 逻辑清晰度（25分）：回答是否有清晰的论点和支撑
2. 数据支撑（25分）：是否有具体数字/案例而非模糊表述
3. 风险意识（25分）：是否主动识别和应对了投资人关切的风险
4. 口径一致性（25分）：是否与商业逻辑自洽，无前后矛盾

请以**严格的 JSON 格式**返回，不要任何其他文字：
{{"score": <整数0-100>, "feedback": "<2-3句具体反馈，指出优缺点和改进建议>"}}"""

    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    raw = (resp.choices[0].message.content or "").strip()
    data = json.loads(raw)
    score = max(0, min(100, int(data.get("score", 50))))
    feedback = str(data.get("feedback", ""))
    return {"score": score, "feedback": feedback}


# ── 公开 API ──────────────────────────────────────────────────────────────────

def start_practice_session(
    institution_id: str,
    company_id: str,
    workspace_root: Path | str,
    *,
    opening_hint: str = "",
) -> dict:
    """
    初始化一次演练会话，生成开场问题。

    返回 session dict：
      institution_id      : str
      company_id          : str
      institution_profile : dict  机构画像快照
      opening_question    : str   AI 投资人开场问题
      rounds              : []    历史问答轮次（初始为空）
      conversation_history: []   对话历史（供 LLM 上下文）
    """
    workspace_root = Path(workspace_root)
    profile = build_institution_profile(institution_id, workspace_root)

    session: dict = {
        "institution_id": institution_id,
        "company_id": company_id,
        "institution_profile": profile,
        "rounds": [],
        "conversation_history": [],
    }

    # 生成开场问题
    try:
        question = _call_llm_question(
            profile,
            conversation_history=[],
            system_hint=opening_hint,
        )
    except Exception as exc:
        logger.warning("practice_engine: LLM 生成开场问题失败，降级（%s）", exc)
        # 降级：从机构历史致命问题中取第一个
        killer_qs = profile.get("killer_questions", [])
        if killer_qs:
            question = killer_qs[0]
        else:
            name = profile.get("canonical_name", "本机构")
            question = f"请简单介绍一下贵公司的商业模式，以及为什么{name}会对你们感兴趣？"

    session["opening_question"] = question
    session["conversation_history"].append({"role": "investor", "content": question})
    return session


def evaluate_answer_and_next(
    session: dict,
    question: str,
    answer: str,
) -> dict:
    """
    评估当前答案并生成下一个问题。

    返回：
      score           : int (0-100)
      feedback        : str
      next_question   : str
      updated_session : dict  （rounds 已追加当前轮）
    """
    import copy
    session = copy.deepcopy(session)
    # 兼容：旧 session 可能无 conversation_history
    if "conversation_history" not in session:
        session["conversation_history"] = []
    profile = session.get("institution_profile", {})

    # ── 评分 ──────────────────────────────────────────────────────────────────
    try:
        eval_result = _call_llm_evaluate(question, answer, profile)
        score = eval_result["score"]
        feedback = eval_result["feedback"]
    except Exception as exc:
        logger.warning("practice_engine: LLM 评分失败，使用默认值（%s）", exc)
        score = 50
        feedback = "AI 评分暂不可用，请检查网络或 API 配置。"

    # 追加本轮记录
    round_record = {
        "question": question,
        "answer": answer,
        "score": score,
        "feedback": feedback,
    }
    session["rounds"].append(round_record)
    session["conversation_history"].append({"role": "founder", "content": answer})

    # ── 生成下一问 ────────────────────────────────────────────────────────────
    try:
        next_q = _call_llm_question(
            profile,
            conversation_history=session["conversation_history"],
        )
    except Exception as exc:
        logger.warning("practice_engine: LLM 生成下一问失败（%s）", exc)
        killer_qs = profile.get("killer_questions", [])
        round_idx = len(session["rounds"])
        if round_idx < len(killer_qs):
            next_q = killer_qs[round_idx]
        else:
            next_q = "你们目前融资规划是什么？打算在什么时候实现盈亏平衡？"

    session["conversation_history"].append({"role": "investor", "content": next_q})

    return {
        "score": score,
        "feedback": feedback,
        "next_question": next_q,
        "updated_session": session,
    }


def get_session_summary(session: dict) -> dict:
    """
    纯数据汇总，无 LLM。

    返回：
      total_rounds  : int
      avg_score     : float
      weak_areas    : list[str]  低分轮次的问题文本
      strong_areas  : list[str]  高分轮次的问题文本
      score_trend   : list[int]  每轮得分列表
    """
    rounds = session.get("rounds", [])
    total = len(rounds)

    if total == 0:
        return {
            "total_rounds": 0,
            "avg_score": 0.0,
            "weak_areas": [],
            "strong_areas": [],
            "score_trend": [],
        }

    scores = [r.get("score", 50) for r in rounds]
    avg = round(sum(scores) / total, 1)

    weak_areas = [r["question"] for r in rounds if r.get("score", 100) < _WEAK_SCORE_THRESHOLD]
    strong_areas = [r["question"] for r in rounds if r.get("score", 0) >= 80]

    return {
        "total_rounds": total,
        "avg_score": avg,
        "weak_areas": weak_areas,
        "strong_areas": strong_areas,
        "score_trend": scores,
    }
