"""Week 4/5：资产命中摘要注入 Prompt 上下文。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_build_asset_summary_markdown_top3_only():
    from agent_nodes import _build_asset_summary_markdown

    hits = [
        {"filename": "A.pdf", "summary": "摘要A"},
        {"filename": "B.pdf", "summary": "摘要B"},
        {"filename": "C.pdf", "summary": "摘要C"},
        {"filename": "D.pdf", "summary": "摘要D"},
    ]
    text = _build_asset_summary_markdown(hits, top_n=3)
    assert "- A.pdf：摘要A" in text
    assert "- C.pdf：摘要C" in text
    assert "D.pdf" not in text


def test_node_prepare_eval_context_contains_asset_reference_markdown():
    from agent_nodes import node_prepare_eval_context
    from schema import TranscriptionWord

    words = [
        TranscriptionWord(
            word_index=0,
            text="你好",
            start_time=0.0,
            end_time=0.1,
            speaker_id="S1",
        )
    ]
    state = {
        "words": words,
        "model_choice": "deepseek",
        "explicit_context": {},
        "qa_text": "原始QA",
        "sanitized_qa_text": "原始QA",
        "asset_summary_markdown": "- A.pdf：摘要A",
        "company_background": "",
        "on_notice": None,
        "historical_memories": None,
    }
    out = node_prepare_eval_context(state)  # type: ignore[arg-type]
    ctx = out["pitch_eval_ctx"]
    assert getattr(ctx, "asset_reference_markdown", "") == "- A.pdf：摘要A"
