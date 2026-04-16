"""Week 4：sanitize_inputs 节点。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_node_sanitize_inputs_replaces_sensitive_markers():
    from agent_nodes import node_sanitize_inputs

    state = {
        "qa_text": "张三电话13812345678，邮箱zhangsan@example.com，身份证110101199001011234",
    }
    out = node_sanitize_inputs(state)  # type: ignore[arg-type]

    assert "[PHONE_NUMBER]" in out["sanitized_qa_text"]
    assert "[EMAIL_ADDRESS]" in out["sanitized_qa_text"]
    assert "[ID_NUMBER]" in out["sanitized_qa_text"]
    assert out["sanitization_meta"]["redaction_count"] >= 3


def test_node_sanitize_inputs_does_not_mutate_original_words():
    from agent_nodes import node_sanitize_inputs
    from schema import TranscriptionWord

    words = [
        TranscriptionWord(
            word_index=0,
            text="张三",
            start_time=0.0,
            end_time=0.1,
            speaker_id="S1",
        )
    ]
    state = {
        "qa_text": "张三电话13812345678",
        "words": words,
    }
    out = node_sanitize_inputs(state)  # type: ignore[arg-type]

    assert words[0].text == "张三"
    assert state["qa_text"] == "张三电话13812345678"
    assert "[PHONE_NUMBER]" in out["sanitized_qa_text"]
