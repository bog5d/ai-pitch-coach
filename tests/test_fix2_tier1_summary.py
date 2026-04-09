"""
Fix 2 — tier1 首句摘要相关测试（Prompt 约束 + UI 提取函数）。
zero API cost。
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


def test_prompt_requires_tier1_first_sentence_format():
    prompt = _minimal_prompt()
    assert "tier1_general_critique" in prompt
    assert "≤25 字" in prompt, "Prompt 必须要求 tier1 首句 ≤25 字"
    assert "动词开头" in prompt, "Prompt 必须要求 tier1 首句动词开头"


# ── UI 辅助函数测试（本地复制逻辑，与 app.py 保持一致）──
def _extract_tier1_summary(tier1: str) -> str:
    """本地复制，与 app.py _extract_tier1_summary 逻辑完全一致，用于测试。"""
    text = (tier1 or "").strip()
    if not text:
        return ""
    for sep in ["。", "；", "！", "？"]:
        idx = text.find(sep)
        if 0 < idx <= 40:
            return text[: idx + 1]
    if len(text) <= 40:
        return text
    return text[:40] + "…"


def test_extract_first_sentence_with_period():
    tier1 = "营收预测与财务口径存在巨大分歧。投资人在问到营收预测时，发言人给出的数字与CFO口径相差30%。"
    result = _extract_tier1_summary(tier1)
    assert result == "营收预测与财务口径存在巨大分歧。"


def test_extract_first_sentence_with_semicolon():
    # 去掉尾部句号，确保"；"是第一个被命中的断句标点（idx=17 < 40）
    tier1 = "项目落地时间表模糊，订单确定性不足；具体原因是发言人无法给出确切的交付节点"
    result = _extract_tier1_summary(tier1)
    assert result == "项目落地时间表模糊，订单确定性不足；"


def test_extract_fallback_to_40_chars():
    tier1 = "这是一段没有句号也没有分号的很长很长很长很长很长很长很长很长很长很长的描述文字"
    result = _extract_tier1_summary(tier1)
    assert len(result) <= 43  # 40字 + 省略号"…"


def test_extract_empty_string_returns_empty():
    assert _extract_tier1_summary("") == ""


def test_extract_short_tier1_returned_as_is():
    tier1 = "数据不一致。"
    result = _extract_tier1_summary(tier1)
    assert result == "数据不一致。"


def test_extract_sentence_longer_than_40_chars_uses_fallback():
    """首句句号位于第41字以后时，触发40字截断回退（句号不在0<idx<=40范围内）。"""
    # "。" 在位置 42（0-indexed），超过 40，触发截断回退
    tier1 = "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十一二。第二句。"
    result = _extract_tier1_summary(tier1)
    assert len(result) <= 43
    assert result.endswith("…")
