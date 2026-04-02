"""
V8.6 静默收割器：防噪门 + capture_and_distill_diff（LLM 全 Mock）。

运行：pytest tests/test_v86_harvester.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from memory_engine import (  # noqa: E402
    capture_and_distill_diff,
    load_executive_memories,
    memory_diff_noise_gate_passes,
)
from schema import ExecutiveMemory  # noqa: E402


class TestNoiseGate:
    def test_identical_text_blocked(self):
        assert memory_diff_noise_gate_passes("hello", "hello") is False

    def test_tiny_typo_same_length_blocked(self):
        """单字符差异，相对距离 ≤10% 且字数差为 0 → 拦截。"""
        assert memory_diff_noise_gate_passes("abcdefghij", "abcdefghik") is False

    def test_large_len_diff_passes(self):
        a = "短"
        b = "这是一段明显更长的改写内容用于测试字数差异超过十个字的情况"
        assert abs(len(a) - len(b)) > 10
        assert memory_diff_noise_gate_passes(a, b) is True

    def test_high_edit_ratio_passes(self):
        """大幅改写：相对 Levenshtein 比例 > 10%。"""
        a = "我们明年一定上市"
        b = "我们建议以关键里程碑与假设条件描述资本市场路径，避免绝对化承诺"
        assert memory_diff_noise_gate_passes(a, b) is True


class TestCaptureAndDistill:
    def test_gate_blocks_no_distill_no_append(self, tmp_path):
        with patch("llm_judge.distill_executive_memory_from_diff") as mock_d:
            out = capture_and_distill_diff(
                "same",
                "same",
                "co1",
                "张总",
                store_dir=tmp_path,
            )
            assert out is None
            mock_d.assert_not_called()
        assert load_executive_memories("co1", "张总", store_dir=tmp_path) == []

    def test_empty_company_skips(self, tmp_path):
        with patch("llm_judge.distill_executive_memory_from_diff") as mock_d:
            out = capture_and_distill_diff("a" * 50, "b" * 50, "", "张总", store_dir=tmp_path)
            assert out is None
            mock_d.assert_not_called()

    def test_distill_appended(self, tmp_path):
        distilled = ExecutiveMemory(
            tag="张总",
            raw_text="问题类型概括",
            correction="标准口径",
            weight=2.0,
        )

        def _fake_distill(o, r, tag, **kw):
            assert "明年" in o or len(o) > 5
            assert "里程碑" in r or len(r) > 5
            return distilled

        with patch("llm_judge.distill_executive_memory_from_diff", side_effect=_fake_distill):
            o = "我们承诺明年一定上市敲钟"
            r = "我们建议以可验证里程碑与假设条件描述上市路径，避免时间上的绝对化承诺"
            mem = capture_and_distill_diff(o, r, "co1", "张总", store_dir=tmp_path)

        assert mem is not None
        assert mem.uuid == distilled.uuid
        loaded = load_executive_memories("co1", "张总", store_dir=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].raw_text == "问题类型概括"

    def test_distill_failure_returns_none(self, tmp_path):
        with patch(
            "llm_judge.distill_executive_memory_from_diff",
            side_effect=RuntimeError("API down"),
        ):
            o = "我们承诺明年一定上市敲钟"
            r = "我们建议以可验证里程碑与假设条件描述上市路径，避免时间上的绝对化承诺"
            out = capture_and_distill_diff(o, r, "co1", "张总", store_dir=tmp_path)
        assert out is None
        assert load_executive_memories("co1", "张总", store_dir=tmp_path) == []
