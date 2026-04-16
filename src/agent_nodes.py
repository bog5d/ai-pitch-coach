"""
LangGraph 节点：Week 2 两阶段评估 + Week 3 记忆检索与 telemetry。
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage

from agent_sanitize import sanitize_llm_input_text, sanitize_text_meta
from agent_state import AgentState
from agent_tenant import resolve_memory_company_id
from asset_bridge import find_related_assets, load_asset_index
from llm_judge import (
    evaluate_pitch,
    prepare_pitch_evaluation_context,
    run_phase1_risk_scan,
    run_phase2_deep_eval_and_assemble_report,
)
from memory_engine import (
    load_top_executive_memories_for_prompt,
    record_executive_memory_prompt_hits,
)

logger = logging.getLogger(__name__)


def _build_asset_summary_markdown(asset_hits: list[dict], top_n: int = 3, max_chars: int = 800) -> str:
    if not asset_hits:
        return ""
    lines: list[str] = []
    for item in asset_hits[:top_n]:
        name = str(item.get("filename") or "未命名文件").strip()
        summary = str(item.get("summary") or "").replace("\n", " ").strip()
        if len(summary) > 60:
            summary = summary[:60] + "…"
        line = f"- {name}"
        if summary:
            line += f"：{summary}"
        lines.append(line)
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[:max_chars] + "…"
    return text


def node_ingest(state: AgentState) -> dict[str, Any]:
    """校验最小入参。"""
    words = state.get("words") or []
    if not words:
        raise ValueError("LangGraph ingest: words 为空，无法评估")
    tid = (state.get("tenant_id") or "").strip()
    if not tid:
        raise ValueError("LangGraph ingest: tenant_id 为空")
    return {}


def node_retrieve_memory(state: AgentState) -> dict[str, Any]:
    """
    Episodic：按 tenant 解析出的 company_id 加载高管错题本；全局加载仓颉资产索引并做 QA 关键词命中。
    无效 tenant 时跳过一切错题本 IO，仍可加载资产索引（方案 A）。
    """
    trace = state.get("trace_id") or ""
    tid = state.get("tenant_id") or ""
    cid = resolve_memory_company_id(tid)
    enabled = cid is not None

    meta: dict[str, Any] = {"trace_id": trace, "tenant_id": tid, "skip_reason": None}
    out: dict[str, Any] = {
        "memory_io_enabled": enabled,
        "memory_company_id": cid,
        "historical_memories": None,
        "asset_hits": [],
        "asset_index_count": 0,
        "memory_retrieve_meta": meta,
    }

    if not enabled:
        meta["skip_reason"] = "invalid_or_unmapped_tenant_for_memory_io"
        logger.info(
            "retrieve_memory: skip episodic IO trace_id=%s tenant_id=%s",
            trace,
            tid,
        )
    else:
        tag = (state.get("explicit_context") or {}).get("interviewee", "").strip()
        _tag_ok = bool(tag) and tag != "未指定"
        if _tag_ok:
            mems = load_top_executive_memories_for_prompt(cid, tag, limit=5)
            if mems:
                record_executive_memory_prompt_hits(cid, tag, mems)
            out["historical_memories"] = mems or None
            meta["interviewee_tag"] = tag
        else:
            meta["skip_reason"] = "interviewee_placeholder_or_empty"
            out["historical_memories"] = None

    assets = load_asset_index()
    out["asset_index_count"] = len(assets)
    kw = (state.get("qa_text") or "").strip()
    if kw and assets:
        out["asset_hits"] = find_related_assets(kw, assets, top_n=5)
    out["asset_summary_markdown"] = _build_asset_summary_markdown(out["asset_hits"], top_n=3)
    meta["asset_hits_n"] = len(out["asset_hits"])

    return out


def node_sanitize_inputs(state: AgentState) -> dict[str, Any]:
    """
    Week 4：仅对进入 LLM 的文本做最小脱敏。
    当前先处理 qa_text；原始 words / 原始 qa_text 均不改。
    """
    qa_text = state.get("qa_text", "")
    result = sanitize_llm_input_text(qa_text)
    return {
        "sanitized_qa_text": result.text,
        "sanitization_meta": sanitize_text_meta(result),
    }


def node_prepare_eval_context(state: AgentState) -> dict[str, Any]:
    ctx = prepare_pitch_evaluation_context(
        state["words"],
        state.get("model_choice", "deepseek"),
        explicit_context=state.get("explicit_context"),
        qa_text=state.get("sanitized_qa_text", state.get("qa_text", "")),
        company_background=state.get("company_background", ""),
        on_notice=state.get("on_notice"),
        historical_memories=state.get("historical_memories"),
        asset_reference_markdown=state.get("asset_summary_markdown", ""),
    )
    return {"pitch_eval_ctx": ctx}


def node_run_phase1_scan(state: AgentState) -> dict[str, Any]:
    ctx = state["pitch_eval_ctx"]
    scan, truncated = run_phase1_risk_scan(ctx)
    return {"risk_scan": scan, "stage1_truncated": truncated}


def node_run_phase2_report(state: AgentState) -> dict[str, Any]:
    ctx = state["pitch_eval_ctx"]
    scan = state["risk_scan"]
    truncated = bool(state.get("stage1_truncated", False))
    report = run_phase2_deep_eval_and_assemble_report(ctx, scan, truncated)
    return {"report": report}


def node_run_evaluate_pitch_monolith(state: AgentState) -> dict[str, Any]:
    """单节点回退（当前未接入图）。"""
    report = evaluate_pitch(
        state["words"],
        state.get("model_choice", "deepseek"),
        explicit_context=state.get("explicit_context"),
        qa_text=state.get("qa_text", ""),
        company_background=state.get("company_background", ""),
        on_notice=state.get("on_notice"),
        historical_memories=state.get("historical_memories"),
    )
    return {"report": report}


def node_finalize(state: AgentState) -> dict[str, Any]:
    report = state.get("report")
    if report is None:
        return {}
    score = getattr(report, "total_score", None)
    summary = f"[FOS] 评估完成 total_score={score!s}"
    return {"messages": [AIMessage(content=summary)]}


def node_memory_event_producer(state: AgentState) -> dict[str, Any]:
    """
    Week 5：统一记忆写入协议（事件化）。
    仅产出 memory_events，不直接写 .executive_memory。
    """
    report = state.get("report")
    if report is None:
        return {"memory_events": []}

    company_id = state.get("memory_company_id")
    tag = (state.get("explicit_context") or {}).get("interviewee", "").strip() or "default"
    if tag == "未指定":
        tag = "default"

    events: list[dict[str, Any]] = []
    for idx, rp in enumerate(report.risk_points):
        raw_text = str(getattr(rp, "tier1_general_critique", "") or "").strip()
        if len(raw_text) > 200:
            raw_text = raw_text[:200] + "…"
        correction = str(getattr(rp, "improvement_suggestion", "") or "").strip()
        if len(correction) > 300:
            correction = correction[:300] + "…"
        if not raw_text or not correction:
            continue
        ded = int(getattr(rp, "score_deduction", 0) or 0)
        weight = max(0.5, min(5.0, round(ded / 4.0, 2)))
        events.append(
            {
                "event_type": "risk_memory_candidate",
                "company_id": company_id,
                "tag": tag,
                "memory": {
                    "raw_text": raw_text,
                    "correction": correction,
                    "weight": weight,
                },
                "risk_type": str(getattr(rp, "risk_type", "") or ""),
                "score_deduction": ded,
                "source": "langgraph_week5_event_producer",
                "sequence": idx,
            }
        )
    return {"memory_events": events}


def node_feedback_telemetry(state: AgentState) -> dict[str, Any]:
    """
    档 1：仅记录 telemetry，不向错题本落盘（避免与审查台锁定收割双写）。
    """
    trace = state.get("trace_id") or ""
    tid = state.get("tenant_id") or ""
    report = state.get("report")
    rps = list(report.risk_points) if report else []
    deductions = [int(getattr(rp, "score_deduction", 0) or 0) for rp in rps]
    telemetry = {
        "trace_id": trace,
        "tenant_id": tid,
        "memory_io_enabled": bool(state.get("memory_io_enabled", False)),
        "memory_company_id": state.get("memory_company_id"),
        "risk_point_count": len(rps),
        "max_score_deduction": max(deductions) if deductions else 0,
        "sum_score_deduction": sum(deductions) if deductions else 0,
        "asset_hits_n": len(state.get("asset_hits") or []),
        "memory_event_count": len(state.get("memory_events") or []),
        "memory_events": list(state.get("memory_events") or []),
        "feedback_persisted": False,
    }
    logger.info(
        "memory_feedback_telemetry trace_id=%s tenant_id=%s "
        "memory_io=%s risk_points=%d max_ded=%s",
        trace,
        tid,
        telemetry["memory_io_enabled"],
        telemetry["risk_point_count"],
        telemetry["max_score_deduction"],
    )
    return {"feedback_telemetry": telemetry}
