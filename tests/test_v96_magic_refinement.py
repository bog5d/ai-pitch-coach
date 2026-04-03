"""
V9.6 refine_single_risk_point（魔法对话框）Mock 测试。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm_judge import refine_single_risk_point  # noqa: E402
from schema import MagicRefinementResult  # noqa: E402


def test_refine_single_risk_point_returns_magic_result():
    payload = {"improvement_suggestion": "重写后：强调定型是工艺阶段而非句号。"}

    ch = MagicMock()
    ch.message.content = json.dumps(payload, ensure_ascii=False)
    resp = MagicMock()
    resp.choices = [ch]

    with patch("llm_judge._make_client") as mk:
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        mk.return_value = (client, "deepseek-chat")
        with patch("llm_judge.run_with_backoff", side_effect=lambda fn, **kw: fn()):
            r = refine_single_risk_point(
                risk_point_id="rp-batch0-3",
                user_instruction="重写这段，强调定型不是句号",
                context_text="发言人称产品已定型。",
                original_suggestion="建议补充验证数据。",
                model_choice="deepseek",
            )

    assert isinstance(r, MagicRefinementResult)
    assert r.risk_point_id == "rp-batch0-3"
    assert "定型" in r.improvement_suggestion
    client.chat.completions.create.assert_called_once()


def test_refine_single_risk_point_empty_instruction_raises():
    with pytest.raises(ValueError, match="user_instruction"):
        refine_single_risk_point(
            risk_point_id="x",
            user_instruction="  ",
            context_text="",
            original_suggestion="旧",
        )


def test_refine_single_risk_point_oversized_context_truncated():
    """context_text 超过 4000 字时应截断后调用，不影响返回结果。"""
    payload = {"improvement_suggestion": "截断后仍正常返回"}
    ch = MagicMock()
    ch.message.content = json.dumps(payload, ensure_ascii=False)
    resp = MagicMock()
    resp.choices = [ch]

    long_ctx = "x" * 8000  # 超过 4000 字

    with patch("llm_judge._make_client") as mk:
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        mk.return_value = (client, "deepseek-chat")
        with patch("llm_judge.run_with_backoff", side_effect=lambda fn, **kw: fn()):
            r = refine_single_risk_point(
                risk_point_id="rp-001",
                user_instruction="重写这段",
                context_text=long_ctx,
                original_suggestion="旧建议",
                model_choice="deepseek",
            )

    assert r.improvement_suggestion == "截断后仍正常返回"
    # 验证送到 LLM 的 prompt 中 context_text 不超过 4000 字
    call_args = client.chat.completions.create.call_args
    user_msg = next(m["content"] for m in call_args.kwargs["messages"] if m["role"] == "user")
    assert len(long_ctx) not in range(len(user_msg))  # 超长原文未完整传入
    assert "x" * 4001 not in user_msg  # 截断有效
