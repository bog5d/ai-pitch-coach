"""
LangGraph 租户闸门：所有错题本（Episodic）IO 必须以通过校验的 company_id 为唯一键。

仓颉 asset_index（Week 3）为全局只读，不经过本模块。
"""
from __future__ import annotations

from memory_engine import normalized_company_id

# 不参与记忆落盘/读取的占位 tenant 字符串（大小写敏感项在 resolve 中单独处理）
_MEMORY_IO_BLOCKED_RAW = frozenset({"未指定"})


def resolve_memory_company_id(tenant_id: str | None) -> str | None:
    """
    将图内 tenant_id 解析为 memory_engine 使用的 company_id。
    无效时返回 None：调用方应跳过一切错题本读写，仅允许继续 LLM 评估。
    """
    raw = (tenant_id or "").strip()
    if not raw:
        return None
    if raw.lower() == "unknown":
        return None
    if raw in _MEMORY_IO_BLOCKED_RAW:
        return None
    return normalized_company_id(raw)


def is_memory_io_enabled(tenant_id: str | None) -> bool:
    return resolve_memory_company_id(tenant_id) is not None
