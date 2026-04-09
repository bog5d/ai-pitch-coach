"""
Fix 3 — 风险点数量约束 Prompt 测试。
仅做字符串断言，zero API cost。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llm_judge import _build_system_prompt


def _minimal_prompt() -> str:
    return _build_system_prompt(
        schema_str="{}",
        explicit_context=None,
        qa_text="",
        company_background="",
        historical_memories=None,
    )


def test_prompt_contains_risk_count_limit():
    prompt = _minimal_prompt()
    assert "总计 ≤10 个" in prompt, "Prompt 必须包含总数量上限约束"


def test_prompt_contains_severe_limit():
    prompt = _minimal_prompt()
    assert "严重 ≤3 个" in prompt, "Prompt 必须包含严重风险点数量上限"


def test_prompt_contains_quality_gate():
    prompt = _minimal_prompt()
    assert "轻微口误" in prompt and "严禁滥用" in prompt, \
        "Prompt 必须包含质量门槛，明确排除低价值风险点"


def test_prompt_discourages_padding():
    prompt = _minimal_prompt()
    assert "禁止凑数" in prompt, "Prompt 必须明确禁止凑数"
