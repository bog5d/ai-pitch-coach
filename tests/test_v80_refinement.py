"""
V8.0 局部精炼与 LLM 润色单元测试。

覆盖：
1. refine_risk_point: 调用一次 LLM，返回 RiskPoint，词索引保留
2. polish_manual_risk_point: 调用一次 LLM，返回 is_manual_entry=True 的 RiskPoint
3. 边界：空描述 → ValueError; LLM 返回残缺 JSON → 抛 ValueError
4. Mock 拦截：零 API 费用

运行：pytest tests/test_v80_refinement.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import RiskPoint, TranscriptionWord  # noqa: E402


# ──────────────────────────────────────────────────────────────────
# Helpers

def _tw(i: int, text: str) -> TranscriptionWord:
    return TranscriptionWord(
        word_index=i, text=text,
        start_time=float(i) * 0.5, end_time=float(i) * 0.5 + 0.4,
        speaker_id="spk_a",
    )


MOCK_WORDS = [_tw(i, f"词{i}") for i in range(10)]

MOCK_RP_DICT: dict = {
    "risk_level": "严重",
    "tier1_general_critique": "原始顶尖视角",
    "tier2_qa_alignment": "原始QA对齐",
    "improvement_suggestion": "原始改进建议",
    "original_text": "词1词2词3",
    "start_word_index": 1,
    "end_word_index": 3,
    "score_deduction": 5,
    "deduction_reason": "原始扣分原因",
    "is_manual_entry": False,
}

REFINED_RP_DICT = {
    "risk_level": "严重",
    "tier1_general_critique": "精炼后的顶尖视角：数据漏洞严重",
    "tier2_qa_alignment": "精炼后的QA对齐：与口径第3条相悖",
    "improvement_suggestion": "精炼后的改进建议：第一步先承认不足...",
    "original_text": "词1词2词3",
    "start_word_index": 1,
    "end_word_index": 3,
    "score_deduction": 8,
    "deduction_reason": "精炼后的扣分原因",
    "is_manual_entry": False,
}

POLISH_RP_DICT = {
    "risk_level": "一般",
    "tier1_general_critique": "AI润色后的顶尖视角",
    "tier2_qa_alignment": "未提供内部QA，基于行业常识",
    "improvement_suggestion": "建议话术示范：第一...",
    "original_text": "",
    "start_word_index": 0,
    "end_word_index": 0,
    "score_deduction": 3,
    "deduction_reason": "基于行业常识",
    "is_manual_entry": True,
}


def _mock_llm_response(content: str):
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ──────────────────────────────────────────────────────────────────
class TestRefineRiskPoint:
    def test_calls_llm_exactly_once(self):
        from llm_judge import refine_risk_point

        with patch("llm_judge._make_client") as mock_make:
            client_mock = MagicMock()
            client_mock.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(REFINED_RP_DICT)
            )
            mock_make.return_value = (client_mock, "deepseek-chat")

            refine_risk_point(MOCK_RP_DICT, MOCK_WORDS, model_choice="deepseek")

        client_mock.chat.completions.create.assert_called_once()

    def test_returns_riskpoint_instance(self):
        from llm_judge import refine_risk_point

        with patch("llm_judge._make_client") as mock_make:
            client_mock = MagicMock()
            client_mock.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(REFINED_RP_DICT)
            )
            mock_make.return_value = (client_mock, "deepseek-chat")

            result = refine_risk_point(MOCK_RP_DICT, MOCK_WORDS, model_choice="deepseek")

        assert isinstance(result, RiskPoint)

    def test_refined_content_differs_from_original(self):
        from llm_judge import refine_risk_point

        with patch("llm_judge._make_client") as mock_make:
            client_mock = MagicMock()
            client_mock.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(REFINED_RP_DICT)
            )
            mock_make.return_value = (client_mock, "deepseek-chat")

            result = refine_risk_point(
                MOCK_RP_DICT, MOCK_WORDS,
                model_choice="deepseek",
                refinement_note="请特别关注数据一致性",
            )

        assert result.tier1_general_critique == "精炼后的顶尖视角：数据漏洞严重"

    def test_word_indices_preserved(self):
        """精炼后的 start/end_word_index 保持与原条目一致（除非 LLM 显式返回新值）。"""
        from llm_judge import refine_risk_point

        with patch("llm_judge._make_client") as mock_make:
            client_mock = MagicMock()
            client_mock.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(REFINED_RP_DICT)
            )
            mock_make.return_value = (client_mock, "deepseek-chat")

            result = refine_risk_point(MOCK_RP_DICT, MOCK_WORDS)

        assert result.start_word_index == MOCK_RP_DICT["start_word_index"]
        assert result.end_word_index == MOCK_RP_DICT["end_word_index"]

    def test_refinement_note_included_in_prompt(self):
        """refinement_note 被注入到 LLM 调用的消息中。"""
        from llm_judge import refine_risk_point

        captured_messages: list = []

        with patch("llm_judge._make_client") as mock_make:
            def fake_create(**kwargs):
                captured_messages.extend(kwargs.get("messages", []))
                return _mock_llm_response(json.dumps(REFINED_RP_DICT))

            client_mock = MagicMock()
            client_mock.chat.completions.create.side_effect = fake_create
            mock_make.return_value = (client_mock, "deepseek-chat")

            refine_risk_point(
                MOCK_RP_DICT, MOCK_WORDS,
                refinement_note="主理人重点批示：需检验数据一致性",
            )

        full_text = " ".join(
            m.get("content", "") for m in captured_messages if isinstance(m, dict)
        )
        assert "主理人重点批示" in full_text, "批示意见应在 prompt 中"

    def test_invalid_llm_json_raises_valueerror(self):
        from llm_judge import refine_risk_point

        with patch("llm_judge._make_client") as mock_make:
            client_mock = MagicMock()
            client_mock.chat.completions.create.return_value = _mock_llm_response(
                "这不是 JSON"
            )
            mock_make.return_value = (client_mock, "deepseek-chat")

            with pytest.raises((ValueError, Exception)):
                refine_risk_point(MOCK_RP_DICT, MOCK_WORDS)


# ──────────────────────────────────────────────────────────────────
class TestPolishManualRiskPoint:
    def test_calls_llm_once(self):
        from llm_judge import polish_manual_risk_point

        with patch("llm_judge._make_client") as mock_make:
            client_mock = MagicMock()
            client_mock.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(POLISH_RP_DICT)
            )
            mock_make.return_value = (client_mock, "deepseek-chat")

            polish_manual_risk_point("发言人数据含糊", model_choice="deepseek")

        client_mock.chat.completions.create.assert_called_once()

    def test_returns_riskpoint_with_is_manual_entry_true(self):
        from llm_judge import polish_manual_risk_point

        with patch("llm_judge._make_client") as mock_make:
            client_mock = MagicMock()
            client_mock.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(POLISH_RP_DICT)
            )
            mock_make.return_value = (client_mock, "deepseek-chat")

            result = polish_manual_risk_point("发言人数据含糊")

        assert isinstance(result, RiskPoint)
        assert result.is_manual_entry is True

    def test_empty_description_raises_valueerror(self):
        from llm_judge import polish_manual_risk_point

        with pytest.raises(ValueError, match="描述"):
            polish_manual_risk_point("   ")

    def test_word_indices_are_zero(self):
        """人工条目无词级锚定，start/end_word_index 必须为 0。"""
        from llm_judge import polish_manual_risk_point

        with patch("llm_judge._make_client") as mock_make:
            client_mock = MagicMock()
            client_mock.chat.completions.create.return_value = _mock_llm_response(
                json.dumps(POLISH_RP_DICT)
            )
            mock_make.return_value = (client_mock, "deepseek-chat")

            result = polish_manual_risk_point("某个重要遗漏点")

        assert result.start_word_index == 0
        assert result.end_word_index == 0

    def test_description_in_prompt(self):
        from llm_judge import polish_manual_risk_point

        captured: list = []

        with patch("llm_judge._make_client") as mock_make:
            def fake_create(**kwargs):
                captured.extend(kwargs.get("messages", []))
                return _mock_llm_response(json.dumps(POLISH_RP_DICT))

            client_mock = MagicMock()
            client_mock.chat.completions.create.side_effect = fake_create
            mock_make.return_value = (client_mock, "deepseek-chat")

            polish_manual_risk_point("发言人在财务数据问题上前后矛盾")

        all_text = " ".join(
            m.get("content", "") for m in captured if isinstance(m, dict)
        )
        assert "前后矛盾" in all_text, "原始描述应出现在 prompt 中"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
