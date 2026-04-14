"""
V8.0 热词库单元测试。

验证 hot_words 参数被正确注入 SiliconFlow 请求（initial_prompt），
以及 Aliyun 引擎静默忽略（不抛错）。
也验证 job_pipeline 层的 hot_words 透传。

运行：pytest tests/test_v80_hot_words.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import AnalysisReport, SceneAnalysis, TranscriptionWord  # noqa: E402


def _tw(i: int, text: str) -> TranscriptionWord:
    return TranscriptionWord(
        word_index=i, text=text,
        start_time=float(i) * 0.5, end_time=float(i) * 0.5 + 0.4,
        speaker_id="spk_a",
    )


def _make_report() -> AnalysisReport:
    return AnalysisReport(
        scene_analysis=SceneAnalysis(scene_type="测试", speaker_roles="A vs B"),
        total_score=90,
        total_score_deduction_reason="",
        risk_points=[],
    )


# ──────────────────────────────────────────────────────────────────
class TestTranscribeAudioHotWords:
    """transcribe_audio 层：hot_words 被向下透传，None 时不影响行为。"""

    def test_hot_words_none_still_transcribes(self, tmp_path):
        """hot_words=None 时，transcribe_audio 正常调用，不抛错。"""
        from transcriber import transcribe_audio

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake-audio")
        mock_words = [_tw(0, "你好")]

        with patch("transcriber.transcribe_siliconflow", return_value=mock_words) as mock_sf:
            result = transcribe_audio(audio, hot_words=None)

        mock_sf.assert_called_once()
        assert result == mock_words

    def test_hot_words_passed_to_siliconflow(self, tmp_path):
        """hot_words 非空时，transcribe_siliconflow 接收到相同 hot_words。"""
        from transcriber import transcribe_audio

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake-audio")
        mock_words = [_tw(0, "测试")]

        captured_kwargs: dict = {}

        def fake_sf(path: str, hot_words=None):
            captured_kwargs["hot_words"] = hot_words
            return mock_words

        with patch("transcriber.transcribe_siliconflow", side_effect=fake_sf):
            transcribe_audio(audio, hot_words=["迪策", "净利润"])

        assert captured_kwargs.get("hot_words") == ["迪策", "净利润"]

    def test_hot_words_fallback_to_aliyun_no_error(self, tmp_path):
        """硅基失败时，降级 Aliyun 不因 hot_words 而崩溃。"""
        from transcriber import transcribe_audio

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake-audio")
        mock_words = [_tw(0, "阿里云")]

        def sf_fail(*a, **kw):
            raise ValueError("硅基流动模拟失败")

        def fake_aliyun(path: str, hot_words=None):
            return mock_words

        with (
            patch("transcriber.transcribe_siliconflow", side_effect=sf_fail),
            patch("transcriber.transcribe_aliyun", side_effect=fake_aliyun),
        ):
            result = transcribe_audio(audio, hot_words=["热词A"])

        assert result == mock_words


# ──────────────────────────────────────────────────────────────────
class TestSiliconflowInitialPrompt:
    """transcribe_siliconflow 层：hot_words 注入 initial_prompt 字段。"""

    def test_initial_prompt_in_form_when_hot_words(self, tmp_path):
        """hot_words 非空时，multipart form 中有 initial_prompt 字段。"""
        import os
        from transcriber import transcribe_siliconflow

        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake-mp3")

        captured_files: list = []

        def fake_post(url, **kwargs):
            captured_files.extend(kwargs.get("files", []))
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "words": [
                    {"word": "测", "start": 0.0, "end": 0.3},
                    {"word": "试", "start": 0.3, "end": 0.6},
                ]
            }
            return mock_resp

        with (
            patch.dict(os.environ, {"SILICONFLOW_API_KEY": "fake-key"}),
            patch("transcriber._requests_post_with_retry", side_effect=fake_post),
        ):
            try:
                transcribe_siliconflow(str(audio), hot_words=["迪策", "净利润"])
            except Exception:
                pass  # words 为空可能抛 ValueError，不影响断言

        field_names = [f[0] for f in captured_files]
        assert "initial_prompt" in field_names, (
            f"期望 initial_prompt 在 multipart fields 中，实际: {field_names}"
        )
        # 验证内容包含热词
        prompt_val = next(
            (f[1][1] for f in captured_files if f[0] == "initial_prompt"), None
        )
        assert prompt_val is not None
        assert "迪策" in prompt_val

    def test_no_initial_prompt_when_no_hot_words(self, tmp_path):
        """hot_words 为 None/空时，不发送 initial_prompt 字段。"""
        import os
        from transcriber import transcribe_siliconflow

        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake-mp3")

        captured_files: list = []

        def fake_post(url, **kwargs):
            captured_files.extend(kwargs.get("files", []))
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"words": []}
            return mock_resp

        with (
            patch.dict(os.environ, {"SILICONFLOW_API_KEY": "fake-key"}),
            patch("transcriber._requests_post_with_retry", side_effect=fake_post),
        ):
            try:
                transcribe_siliconflow(str(audio), hot_words=None)
            except Exception:
                pass

        field_names = [f[0] for f in captured_files]
        assert "initial_prompt" not in field_names


# ──────────────────────────────────────────────────────────────────
class TestPipelineHotWordsPassthrough:
    """run_pitch_file_job 通过 PitchFileJobParams.hot_words 把热词透传到 transcribe_audio。"""

    def test_hot_words_passed_to_transcribe_when_no_cache(self, tmp_path):
        from job_pipeline import PitchFileJobParams, run_pitch_file_job

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake")
        words = [_tw(0, "测试")]
        report = _make_report()

        captured_asr_kwargs: dict = {}

        def fake_asr(path, **kwargs):
            captured_asr_kwargs.update(kwargs)
            return words

        params = PitchFileJobParams(
            transcription_json_path=tmp_path / "t.json",
            analysis_json_path=tmp_path / "a.json",
            html_output_path=tmp_path / "r.html",
            sensitive_words=[],
            explicit_context={"biz_type": "01_机构路演", "exact_roles": "A vs B",
                              "project_name": "P", "interviewee": "I",
                              "session_notes": "", "sniper_targets_json": "[]",
                              "recording_label": "test.wav"},
            qa_text="",
            model_choice="deepseek",
            hot_words=["迪策", "净利润"],
            skip_asr_polish=True,
        )

        with (
            patch("job_pipeline.transcribe_audio", side_effect=fake_asr),
            patch("job_pipeline.evaluate_pitch", return_value=report),
            patch("job_pipeline.apply_asr_original_text_override", return_value=report),
        ):
            run_pitch_file_job(audio, params, skip_html_export=True)

        assert captured_asr_kwargs.get("hot_words") == ["迪策", "净利润"]

    def test_no_hot_words_asr_called_without_kwarg(self, tmp_path):
        """hot_words 为 None 时，不传该参数（或传 None）给 ASR。"""
        from job_pipeline import PitchFileJobParams, run_pitch_file_job

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake")
        words = [_tw(0, "测")]
        report = _make_report()

        captured_kwargs: dict = {}

        def fake_asr(path, **kwargs):
            captured_kwargs.update(kwargs)
            return words

        params = PitchFileJobParams(
            transcription_json_path=tmp_path / "t.json",
            analysis_json_path=tmp_path / "a.json",
            html_output_path=tmp_path / "r.html",
            sensitive_words=[],
            explicit_context={"biz_type": "01_机构路演", "exact_roles": "A vs B",
                              "project_name": "P", "interviewee": "I",
                              "session_notes": "", "sniper_targets_json": "[]",
                              "recording_label": "test.wav"},
            qa_text="",
            model_choice="deepseek",
            skip_asr_polish=True,
        )

        with (
            patch("job_pipeline.transcribe_audio", side_effect=fake_asr),
            patch("job_pipeline.evaluate_pitch", return_value=report),
            patch("job_pipeline.apply_asr_original_text_override", return_value=report),
        ):
            run_pitch_file_job(audio, params, skip_html_export=True)

        # hot_words 传 None 或不传均合法
        assert captured_kwargs.get("hot_words") is None or "hot_words" not in captured_kwargs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
