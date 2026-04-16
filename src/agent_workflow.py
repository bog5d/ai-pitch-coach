"""
编译 LangGraph：
ingest → retrieve_memory → sanitize_inputs → prepare_eval → phase1_scan → phase2_report
→ finalize → memory_event_producer → feedback_telemetry → END
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from agent_nodes import (
    node_feedback_telemetry,
    node_finalize,
    node_ingest,
    node_memory_event_producer,
    node_prepare_eval_context,
    node_retrieve_memory,
    node_sanitize_inputs,
    node_run_phase1_scan,
    node_run_phase2_report,
)
from agent_state import AgentState

# 递增以失效 agent_runner 内缓存的已编译图
WORKFLOW_BUILD_ID = 5


def build_pitch_evaluation_workflow() -> StateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("ingest", node_ingest)
    graph.add_node("retrieve_memory", node_retrieve_memory)
    graph.add_node("sanitize_inputs", node_sanitize_inputs)
    graph.add_node("prepare_eval", node_prepare_eval_context)
    graph.add_node("phase1_scan", node_run_phase1_scan)
    graph.add_node("phase2_report", node_run_phase2_report)
    graph.add_node("finalize", node_finalize)
    graph.add_node("memory_event_producer", node_memory_event_producer)
    graph.add_node("feedback_telemetry", node_feedback_telemetry)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "retrieve_memory")
    graph.add_edge("retrieve_memory", "sanitize_inputs")
    graph.add_edge("sanitize_inputs", "prepare_eval")
    graph.add_edge("prepare_eval", "phase1_scan")
    graph.add_edge("phase1_scan", "phase2_report")
    graph.add_edge("phase2_report", "finalize")
    graph.add_edge("finalize", "memory_event_producer")
    graph.add_edge("memory_event_producer", "feedback_telemetry")
    graph.add_edge("feedback_telemetry", END)
    return graph


def compile_pitch_evaluation_app():
    """供 Streamlit / job_pipeline 使用的已编译 Runnable。"""
    return build_pitch_evaluation_workflow().compile()
