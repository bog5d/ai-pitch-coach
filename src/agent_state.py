"""
LangGraph 评估流水线 — 共享状态定义。

Week 2：pitch_eval_ctx / risk_scan / stage1_truncated
Week 3：租户闸门 + Episodic 检索结果 + 全局资产命中 + 反馈 telemetry（不落盘）
Week 4：送 LLM 文本脱敏（不污染原始 words）
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from schema import AnalysisReport, ExecutiveMemory, TranscriptionWord


class AgentState(TypedDict, total=False):
    """LangGraph 全局状态（最小集 + 扩展槽位）。"""

    tenant_id: str
    trace_id: str
    messages: Annotated[Sequence[BaseMessage], add_messages]

    words: list[TranscriptionWord]
    model_choice: str
    explicit_context: dict[str, Any] | None
    qa_text: str
    company_background: str
    on_notice: Callable[[str], None] | None
    historical_memories: list[ExecutiveMemory] | None
    sanitized_qa_text: str
    """仅供 LLM 使用的脱敏 QA 文本；保留原 qa_text 作为真实输入快照。"""

    sanitization_meta: dict[str, Any]
    """脱敏统计（类型、数量、引擎），便于观测与审计。"""

    pitch_eval_ctx: Any
    """llm_judge.PitchEvalContext（避免图状态与 llm_judge 循环 import，此处用 Any）。"""

    risk_scan: Any
    """llm_judge 阶段一输出的 RiskScanResult。"""

    stage1_truncated: bool

    memory_io_enabled: bool
    """True 时允许读写 memory_engine（已解析出合法 company_id）。"""

    memory_company_id: str | None
    """与 memory_engine 目录一致的 company_id；仅当 memory_io_enabled 时非 None。"""

    asset_hits: list[dict]
    """仓颉 asset_index 关键词命中（全局只读，无租户隔离）。"""

    asset_index_count: int
    asset_summary_markdown: str
    """供 prompt 注入的资产摘要（Top N，长度受控）。"""

    memory_retrieve_meta: dict[str, Any]
    """检索节点元信息（如 skip 原因），便于观测。"""

    memory_events: list[dict[str, Any]]
    """图内统一生成的记忆事件（档 1 仅产事件，不直接落盘）。"""

    feedback_telemetry: dict[str, Any]
    """档 1：图尾仅结构化 telemetry，不写错题本。"""

    report: AnalysisReport | None
    error: str | None
