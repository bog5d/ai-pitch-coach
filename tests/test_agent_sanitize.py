"""Week 4：送 LLM 文本脱敏。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_sanitize_replaces_phone_email_id_and_person():
    from agent_sanitize import sanitize_llm_input_text

    text = "张三的电话是13812345678，邮箱是zhangsan@example.com，身份证是110101199001011234。"
    result = sanitize_llm_input_text(text)

    assert "[PHONE_NUMBER]" in result.text
    assert "[EMAIL_ADDRESS]" in result.text
    assert "[ID_NUMBER]" in result.text
    assert "[PERSON]" in result.text
    assert result.redaction_count >= 4


def test_sanitize_empty_text_is_noop():
    from agent_sanitize import sanitize_llm_input_text

    result = sanitize_llm_input_text("   ")
    assert result.text == ""
    assert result.redaction_count == 0
    assert result.redaction_summary == {}
    assert result.engine == "noop"
