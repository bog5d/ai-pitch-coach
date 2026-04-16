"""
LangGraph 评估入口：对外稳定 API，与 evaluate_pitch 同返回类型。
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage

from agent_state import AgentState
from agent_workflow import WORKFLOW_BUILD_ID, compile_pitch_evaluation_app
from schema import AnalysisReport, ExecutiveMemory, TranscriptionWord

logger = logging.getLogger(__name__)

_COMPILED_APP = None
_CACHED_WORKFLOW_BUILD_ID: int | None = None


def get_compiled_pitch_evaluation_app():
    global _COMPILED_APP, _CACHED_WORKFLOW_BUILD_ID
    if _COMPILED_APP is None or _CACHED_WORKFLOW_BUILD_ID != WORKFLOW_BUILD_ID:
        _COMPILED_APP = compile_pitch_evaluation_app()
        _CACHED_WORKFLOW_BUILD_ID = WORKFLOW_BUILD_ID
    return _COMPILED_APP


def _extract_state_excerpt(final_state: AgentState) -> dict[str, Any]:
    """仅返回前端可视化需要的轻量状态，避免把大对象塞进 session_state。"""
    keys = (
        "asset_summary_markdown",
        "asset_hits",
        "memory_events",
        "feedback_telemetry",
        "sanitization_meta",
        "memory_retrieve_meta",
        "memory_io_enabled",
        "memory_company_id",
    )
    out: dict[str, Any] = {}
    for k in keys:
        if k in final_state:
            out[k] = final_state.get(k)
    return out


def run_pitch_evaluation_via_langgraph(
    *,
    tenant_id: str,
    words: list[TranscriptionWord],
    model_choice: str = "deepseek",
    explicit_context: dict[str, Any] | None = None,
    qa_text: str = "",
    company_background: str = "",
    on_notice: Callable[[str], None] | None = None,
    historical_memories: list[ExecutiveMemory] | None = None,
    trace_id: str | None = None,
) -> AnalysisReport:
    report, _ = run_pitch_evaluation_via_langgraph_with_state(
        tenant_id=tenant_id,
        words=words,
        model_choice=model_choice,
        explicit_context=explicit_context,
        qa_text=qa_text,
        company_background=company_background,
        on_notice=on_notice,
        historical_memories=historical_memories,
        trace_id=trace_id,
    )
    return report


def run_pitch_evaluation_via_langgraph_with_state(
    *,
    tenant_id: str,
    words: list[TranscriptionWord],
    model_choice: str = "deepseek",
    explicit_context: dict[str, Any] | None = None,
    qa_text: str = "",
    company_background: str = "",
    on_notice: Callable[[str], None] | None = None,
    historical_memories: list[ExecutiveMemory] | None = None,
    trace_id: str | None = None,
) -> tuple[AnalysisReport, dict[str, Any]]:
    if historical_memories is not None:
        logger.warning(
            "run_pitch_evaluation_via_langgraph: historical_memories 已忽略，"
            "由图内 retrieve_memory 节点统一加载（Week 3）"
        )
    tid = (tenant_id or "").strip() or "unknown"
    tr = trace_id or uuid.uuid4().hex
    app = get_compiled_pitch_evaluation_app()

    initial: AgentState = {
        "tenant_id": tid,
        "trace_id": tr,
        "messages": [
            HumanMessage(
                content=f"[FOS] trace_id={tr} tenant_id={tid} pipeline=pitch_evaluation_v1"
            )
        ],
        "words": words,
        "model_choice": model_choice,
        "explicit_context": explicit_context,
        "qa_text": qa_text,
        "company_background": company_background,
        "on_notice": on_notice,
        "report": None,
        "error": None,
    }

    logger.info("langgraph_pitch_eval: start trace_id=%s tenant_id=%s", tr, tid)
    final_state = app.invoke(initial)
    report = final_state.get("report")
    if report is None:
        err = final_state.get("error")
        raise RuntimeError(f"LangGraph 评估未产生 report: {err or 'unknown'}")
    logger.info("langgraph_pitch_eval: done trace_id=%s", tr)
    return report, _extract_state_excerpt(final_state)
