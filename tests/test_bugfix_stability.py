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
