"""
工业级稳定性修复——统一测试套件
运行：pytest tests/test_bugfix_stability.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ════════════════════════════════════════════════════════
# T1  BUG-2: DashScope 轮询应用 GET 而非 POST
# ════════════════════════════════════════════════════════

class TestDashScopePollUsesGet:
    """_dashscope_poll_task_rest 必须调用 GET 方法，不能调用 POST。"""

    def test_poll_calls_get_not_post(self):
        import transcriber

        succeeded_body = {
            "output": {
                "task_status": "SUCCEEDED",
                "results": [{"subtask_status": "SUCCEEDED", "transcription_url": "http://x.test/r.json"}],
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = succeeded_body

        with (
            patch.object(transcriber, "_requests_get_with_retry", return_value=mock_resp) as mock_get,
            patch.object(transcriber, "_requests_post_with_retry") as mock_post,
        ):
            result = transcriber._dashscope_poll_task_rest("fake_key", "task_abc123")

        mock_get.assert_called_once()
        mock_post.assert_not_called()
        assert result[0]["subtask_status"] == "SUCCEEDED"

    def test_poll_url_contains_task_id(self):
        import transcriber

        succeeded_body = {
            "output": {
                "task_status": "SUCCEEDED",
                "results": [{"subtask_status": "SUCCEEDED", "transcription_url": "http://x/r.json"}],
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = succeeded_body

        with patch.object(transcriber, "_requests_get_with_retry", return_value=mock_resp) as mock_get:
            transcriber._dashscope_poll_task_rest("fake_key", "MY_TASK_ID")

        called_url = mock_get.call_args[0][0]
        assert "MY_TASK_ID" in called_url


# ════════════════════════════════════════════════════════
# T3  BUG-8: safe_fs_segment 必须截断超长输入
# ════════════════════════════════════════════════════════

class TestSafeFsSegmentLengthLimit:
    """safe_fs_segment 产物不超过 200 字符（与 memory_engine 保持一致）。"""

    def test_normal_name_unchanged(self):
        from job_pipeline import safe_fs_segment
        assert safe_fs_segment("正常文件名") == "正常文件名"

    def test_very_long_name_truncated_to_200(self):
        from job_pipeline import safe_fs_segment
        long_name = "A" * 500
        result = safe_fs_segment(long_name)
        assert len(result) <= 200

    def test_invalid_chars_replaced(self):
        from job_pipeline import safe_fs_segment
        result = safe_fs_segment('foo<>:"/\\|?*bar')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result

    def test_empty_returns_default(self):
        from job_pipeline import safe_fs_segment
        assert safe_fs_segment("") == "未命名批次"
        assert safe_fs_segment("   ") == "未命名批次"

    def test_long_chinese_name_truncated(self):
        from job_pipeline import safe_fs_segment
        long_name = "测试" * 200  # 400 字
        result = safe_fs_segment(long_name)
        assert len(result) <= 200


# ════════════════════════════════════════════════════════
# T4  BUG-4: 毫秒启发式在长录音下不翻转
# ════════════════════════════════════════════════════════

class TestCoerceSecondsPair:
    """_coerce_seconds_pair 在各种时间格式下正确解析。"""

    def _coerce(self, d):
        from transcriber import _coerce_seconds_pair
        return _coerce_seconds_pair(d)

    def test_openai_start_end_seconds(self):
        """OpenAI 标准格式 start/end（秒）直接返回，不经启发式。"""
        result = self._coerce({"start": 10.5, "end": 12.3})
        assert result is not None
        assert abs(result[0] - 10.5) < 0.001
        assert abs(result[1] - 12.3) < 0.001

    def test_openai_start_end_long_audio_not_divided(self):
        """超过 5 分钟的录音，start/end 格式不被误判为毫秒。"""
        result = self._coerce({"start": 400.0, "end": 405.0})
        assert result is not None
        assert abs(result[0] - 400.0) < 0.001
        assert abs(result[1] - 405.0) < 0.001

    def test_begin_time_always_treated_as_ms(self):
        """begin_time/end_time 字段固定为毫秒（阿里云 SDK 命名约定），必须除以 1000。"""
        result = self._coerce({"begin_time": 5000, "end_time": 6500})
        assert result is not None
        assert abs(result[0] - 5.0) < 0.001
        assert abs(result[1] - 6.5) < 0.001

    def test_begin_time_small_value_still_ms(self):
        """begin_time=250（< 300）仍必须当毫秒处理（不能当 250 秒）。"""
        result = self._coerce({"begin_time": 250, "end_time": 300})
        assert result is not None
        assert abs(result[0] - 0.25) < 0.001
        assert abs(result[1] - 0.30) < 0.001

    def test_start_time_large_value_divided(self):
        """start_time 极大值（明显毫秒）仍被除以 1000。"""
        result = self._coerce({"start_time": 400000, "end_time": 405000})
        assert result is not None
        assert abs(result[0] - 400.0) < 0.001
        assert abs(result[1] - 405.0) < 0.001

    def test_none_input_returns_none(self):
        result = self._coerce({"foo": 1})
        assert result is None

    def test_invalid_value_returns_none(self):
        result = self._coerce({"begin_time": "abc", "end_time": "xyz"})
        assert result is None


# ════════════════════════════════════════════════════════
# T5  BUG-7: append_executive_memory 防重复入库
# ════════════════════════════════════════════════════════

class TestAppendExecutiveMemoryIdempotent:
    """同一 raw_text 的记忆不应被重复入库（防双击 race）。"""

    def _make_mem(self, raw_text: str, correction: str = "标准口径"):
        from schema import ExecutiveMemory
        import uuid as uuid_mod
        return ExecutiveMemory(
            uuid=str(uuid_mod.uuid4()),
            tag="张三",
            raw_text=raw_text,
            correction=correction,
            weight=1.0,
            risk_type="一般",
            updated_at="2026-04-10T00:00:00Z",
            hit_count=0,
        )

    def test_duplicate_raw_text_not_appended(self, tmp_path):
        """raw_text 完全相同的记忆再次 append 时，总条数不增加。"""
        from memory_engine import append_executive_memory, load_executive_memories

        mem1 = self._make_mem("表达含糊，缺乏数据支撑")
        mem2 = self._make_mem("表达含糊，缺乏数据支撑")  # 相同 raw_text，不同 uuid

        append_executive_memory("test_co", "张三", mem1, store_dir=tmp_path)
        append_executive_memory("test_co", "张三", mem2, store_dir=tmp_path)

        items = load_executive_memories("test_co", "张三", store_dir=tmp_path)
        assert len(items) == 1, f"期望 1 条，实际 {len(items)} 条（重复入库）"

    def test_different_raw_text_both_appended(self, tmp_path):
        """raw_text 不同的两条记忆均应入库。"""
        from memory_engine import append_executive_memory, load_executive_memories

        mem1 = self._make_mem("问题 A")
        mem2 = self._make_mem("问题 B")

        append_executive_memory("test_co", "张三", mem1, store_dir=tmp_path)
        append_executive_memory("test_co", "张三", mem2, store_dir=tmp_path)

        items = load_executive_memories("test_co", "张三", store_dir=tmp_path)
        assert len(items) == 2

    def test_empty_store_appends_normally(self, tmp_path):
        """空桶首次追加，正常入库。"""
        from memory_engine import append_executive_memory, load_executive_memories

        mem = self._make_mem("首次入库")
        append_executive_memory("new_co", "李四", mem, store_dir=tmp_path)

        items = load_executive_memories("new_co", "李四", store_dir=tmp_path)
        assert len(items) == 1


# ════════════════════════════════════════════════════════
# T6  BUG-6: get_company_dashboard_stats 支持预传 pairs 避免双倍 IO
# ════════════════════════════════════════════════════════

class TestDashboardStatsAcceptsPairs:
    """get_company_dashboard_stats 接受 pre_loaded_pairs 时，不再读磁盘。"""

    def _make_mem(self):
        from schema import ExecutiveMemory
        import uuid as uuid_mod
        return ExecutiveMemory(
            uuid=str(uuid_mod.uuid4()),
            tag="王总",
            raw_text="示例记忆",
            correction="标准口径",
            weight=1.5,
            risk_type="严重",
            updated_at="2026-04-10T10:00:00Z",
            hit_count=3,
        )

    def test_stats_with_pre_loaded_pairs_skips_disk_read(self):
        """传入 pre_loaded_pairs 时，list_all_executive_memories_for_company 不被调用。"""
        from memory_engine import get_company_dashboard_stats
        from unittest.mock import patch

        mem = self._make_mem()
        pairs = [("王总", mem)]

        with patch("memory_engine.list_all_executive_memories_for_company") as mock_list:
            stats = get_company_dashboard_stats("test_co", pre_loaded_pairs=pairs)

        mock_list.assert_not_called()
        assert stats["total_memories"] == 1
        assert stats["active_executives"] == 1

    def test_stats_without_pre_loaded_reads_disk(self):
        """不传 pre_loaded_pairs 时，仍走磁盘读取路径（兼容旧调用方）。"""
        from memory_engine import get_company_dashboard_stats
        from unittest.mock import patch

        with patch("memory_engine.list_all_executive_memories_for_company", return_value=[]) as mock_list:
            get_company_dashboard_stats("test_co")

        mock_list.assert_called_once()
