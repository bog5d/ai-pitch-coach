"""
极端边界自动化测试：不依赖 Streamlit、不人肉点击。
仓库发版 V6.2（与 build_release.CURRENT_VERSION 对齐）。
运行：python tests/test_extreme_cases.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

# 项目根与 src
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm_judge import _build_system_prompt, evaluate_pitch  # noqa: E402
from report_builder import (  # noqa: E402
    PHYSICAL_MAX_DURATION,
    _padded_window_sec,
    slice_audio_file_to_base64,
)
from schema import TranscriptionWord  # noqa: E402


def _write_silent_wav(path: Path, seconds: float = 5.0, rate: int = 8000) -> None:
    n = int(max(1, seconds * rate))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * n)


class TestPromptAntiHallucination(unittest.TestCase):
    """空 QA + 残缺上下文：Prompt 必须包含防幻觉约束，且可构造 System 文本不崩。"""

    def test_empty_qa_kb_placeholder(self) -> None:
        sp = _build_system_prompt('{"dummy": true}', {}, "")
        self.assertIn("未提供参考QA知识库", sp)
        self.assertIn("绝对禁止凭空捏造", sp)
        self.assertIn("黄金 60 秒", sp)
        self.assertIn("[index]", sp)

    def test_broken_context_normalized(self) -> None:
        sp = _build_system_prompt(
            "{}",
            {"biz_type": "", "exact_roles": None, "project_name": ""},
            "",
        )
        self.assertIn("未指定", sp)


class TestEvaluatePitchMocked(unittest.TestCase):
    """极短转写 + 空 qa + 残缺 explicit_context：不崩溃且能解析返回 JSON。"""

    def test_minimal_words_mock_api(self) -> None:
        words = [
            TranscriptionWord(
                word_index=0,
                text="测",
                start_time=0.0,
                end_time=0.1,
                speaker_id="未知",
            ),
            TranscriptionWord(
                word_index=1,
                text="试",
                start_time=0.1,
                end_time=0.2,
                speaker_id="未知",
            ),
        ]
        payload = {
            "scene_analysis": {
                "scene_type": "极端测试",
                "speaker_roles": "未提供内部 QA，基于行业常识推断",
            },
            "total_score": 0,
            "risk_points": [],
        }
        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(message=MagicMock(content=json.dumps(payload, ensure_ascii=False)))
        ]

        with patch("llm_judge.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_resp

            report = evaluate_pitch(
                words,
                model_choice="deepseek",
                explicit_context={
                    "biz_type": "",
                    "exact_roles": "",
                    "project_name": "",
                },
                qa_text="",
            )
            self.assertEqual(report.total_score, 0)
            self.assertEqual(len(report.risk_points), 0)


class TestPaddedWindowPhysicalCap(unittest.TestCase):
    """V6.2：超长词级窗口须被物理截断为 180s。"""

    def test_caps_duration_at_180(self) -> None:
        t0, dur = _padded_window_sec(0.0, 400.0, None)
        self.assertEqual(t0, 0.0)
        self.assertEqual(dur, PHYSICAL_MAX_DURATION)
        self.assertEqual(PHYSICAL_MAX_DURATION, 180.0)

    def test_short_window_unchanged(self) -> None:
        t0, dur = _padded_window_sec(10.0, 20.0, None)
        self.assertEqual(t0, max(0.0, 10.0 - 1.5))
        self.assertLessEqual(dur, 30.0)


class TestSliceAudioExtreme(unittest.TestCase):
    """越界时间戳 + 非对称缓冲：ffmpeg 路径不得致命崩溃；有 ffmpeg 时应能导出 Base64。"""

    def test_negative_start_huge_end(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "silent.wav"
            _write_silent_wav(wav, 5.0)
            b64 = slice_audio_file_to_base64(wav, -50.0, 99999.0)
        self.assertIsInstance(b64, str)
        if not b64:
            self.skipTest("ffmpeg 不可用或切片被环境拦截，跳过长度断言")
        self.assertGreater(len(b64), 50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
