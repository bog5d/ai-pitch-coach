"""
V8.6.1：记忆进化字段（updated_at / hit_count / risk_type）与命中计数、雷区聚合。

零真实 API。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from memory_engine import (  # noqa: E402
    capture_and_distill_diff,
    list_all_executive_memories_for_company,
    load_executive_memories,
    record_executive_memory_prompt_hits,
    top_risk_type_counts_for_company,
)
from schema import ExecutiveMemory  # noqa: E402


class TestRecordHits:
    def test_record_hits_bumps_count_and_updated_at(self, tmp_path):
        m = ExecutiveMemory(
            tag="张总",
            raw_text="a",
            correction="b",
            weight=1.0,
            hit_count=0,
            updated_at="",
            risk_type="严重",
        )
        from memory_engine import save_executive_memories  # noqa: E402

        save_executive_memories("co", "张总", [m], store_dir=tmp_path)
        loaded = load_executive_memories("co", "张总", store_dir=tmp_path)
        assert loaded[0].hit_count == 0

        record_executive_memory_prompt_hits("co", "张总", loaded, store_dir=tmp_path)
        after = load_executive_memories("co", "张总", store_dir=tmp_path)
        assert after[0].hit_count == 1
        assert after[0].updated_at != ""

    def test_record_hits_noop_when_empty(self, tmp_path):
        record_executive_memory_prompt_hits("co", "张总", [], store_dir=tmp_path)


class TestTopRiskTypes:
    def test_top_three_by_count(self, tmp_path):
        from memory_engine import save_executive_memories  # noqa: E402

        items = [
            ExecutiveMemory(tag="t", raw_text="1", correction="c", risk_type="严重", weight=1.0),
            ExecutiveMemory(tag="t", raw_text="2", correction="c", risk_type="严重", weight=1.0),
            ExecutiveMemory(tag="t", raw_text="3", correction="c", risk_type="一般", weight=1.0),
            ExecutiveMemory(tag="t", raw_text="4", correction="c", risk_type="轻微", weight=1.0),
        ]
        save_executive_memories("co", "桶a", [items[0], items[1]], store_dir=tmp_path)
        save_executive_memories("co", "桶b", [items[2], items[3]], store_dir=tmp_path)

        top = top_risk_type_counts_for_company("co", limit=3, store_dir=tmp_path)
        assert top[0] == ("严重", 2)
        assert ("一般", 1) in top
        assert len(top) == 3


class TestDistillUsesDeepSeekOnly:
    def test_router_has_no_haiku(self):
        import llm_judge  # noqa: E402

        assert "haiku" not in llm_judge.ROUTER

    def test_distill_calls_make_client_with_deepseek(self):
        from llm_judge import distill_executive_memory_from_diff  # noqa: E402

        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"raw_text":"问题","correction":"口径","weight":1.5}'
                )
            )
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        def _run(fn, **kw):
            return fn()

        with (
            patch("llm_judge._make_client", return_value=(mock_client, "deepseek-chat")) as mc,
            patch("llm_judge.run_with_backoff", side_effect=_run),
        ):
            mem = distill_executive_memory_from_diff("旧文很长" * 5, "新文完全不同" * 5, "李总")

        mc.assert_called_once_with("deepseek")
        assert mem.raw_text == "问题"
        assert mock_client.chat.completions.create.called


class TestCapturePassesRiskType:
    def test_capture_sets_risk_type_and_timestamps(self, tmp_path):
        distilled = ExecutiveMemory(
            tag="张总",
            raw_text="x",
            correction="y",
            weight=1.0,
            risk_type="",
            hit_count=0,
            updated_at="",
        )

        def _fake(o, r, tag, **kw):
            return distilled

        with patch("llm_judge.distill_executive_memory_from_diff", side_effect=_fake):
            out = capture_and_distill_diff(
                "我们承诺明年一定上市敲钟",
                "我们建议以可验证里程碑与假设条件描述上市路径，避免时间上的绝对化承诺",
                "co1",
                "张总",
                risk_type="严重",
                store_dir=tmp_path,
            )
        assert out is not None
        loaded = load_executive_memories("co1", "张总", store_dir=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].risk_type == "严重"
        assert loaded[0].hit_count == 0
        assert loaded[0].updated_at != ""
