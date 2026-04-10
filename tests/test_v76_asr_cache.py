"""
V7.6 ASR 内存缓存机制单元测试。

覆盖目标：
1. run_pitch_file_job 接受 cached_words 时，transcribe_audio 不被调用
2. run_pitch_file_job 不传 cached_words 时，transcribe_audio 正常调用
3. 提供 cached_words 时，转写 JSON 仍按词列表写入磁盘
4. cached_words 返回值与入参一致

运行：pytest tests/test_v76_asr_cache.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import AnalysisReport, RiskPoint, SceneAnalysis, TranscriptionWord  # noqa: E402


# ─────────────────────────── helpers ────────────────────────────

def _tw(i: int, text: str, speaker: str = "spk_a") -> TranscriptionWord:
    return TranscriptionWord(
        word_index=i,
        text=text,
        start_time=float(i) * 0.5,
        end_time=float(i) * 0.5 + 0.4,
        speaker_id=speaker,
    )


def _make_words(texts=("你好", "世界")) -> list[TranscriptionWord]:
    return [_tw(i, t) for i, t in enumerate(texts)]


def _make_report() -> AnalysisReport:
    return AnalysisReport(
        scene_analysis=SceneAnalysis(scene_type="测试场景", speaker_roles="A vs B"),
        total_score=85,
        total_score_deduction_reason="测试扣分",
        risk_points=[],
    )


def _make_params(tmp_path: Path):
    from job_pipeline import PitchFileJobParams

    return PitchFileJobParams(
        transcription_json_path=tmp_path / "trans.json",
        analysis_json_path=tmp_path / "analysis.json",
        html_output_path=tmp_path / "report.html",
        sensitive_words=[],
        explicit_context={
            "biz_type": "01_机构路演",
            "exact_roles": "企业 vs 投资机构",
            "project_name": "测试项目",
            "interviewee": "张三",
            "session_notes": "",
            "sniper_targets_json": "[]",
            "recording_label": "test.wav",
        },
        qa_text="",
        model_choice="deepseek",
        skip_asr_polish=True,
    )


# ─────────────────────────── tests ──────────────────────────────

class TestCachedWordsSkipsASR:
    """cached_words 传入时，云端 ASR 调用被跳过。"""

    def test_transcribe_audio_not_called(self, tmp_path):
        from job_pipeline import run_pitch_file_job

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake-audio-bytes")
        cached = _make_words()
        report = _make_report()

        with (
            patch("job_pipeline.transcribe_audio") as mock_asr,
            patch("job_pipeline.evaluate_pitch", return_value=report),
            patch("job_pipeline.apply_asr_original_text_override", return_value=report),
        ):
            words_out, _ = run_pitch_file_job(
                audio,
                _make_params(tmp_path),
                skip_html_export=True,
                cached_words=cached,
            )

        mock_asr.assert_not_called()
        assert words_out == cached

    def test_returned_words_identical_to_cache(self, tmp_path):
        from job_pipeline import run_pitch_file_job

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"x")
        cached = _make_words(("一", "二", "三"))
        report = _make_report()

        with (
            patch("job_pipeline.transcribe_audio"),
            patch("job_pipeline.evaluate_pitch", return_value=report),
            patch("job_pipeline.apply_asr_original_text_override", return_value=report),
        ):
            words_out, _ = run_pitch_file_job(
                audio,
                _make_params(tmp_path),
                skip_html_export=True,
                cached_words=cached,
            )

        assert len(words_out) == 3
        assert [w.text for w in words_out] == ["一", "二", "三"]


class TestNoCacheFallsBackToASR:
    """未传 cached_words 时，transcribe_audio 正常调用。"""

    def test_transcribe_audio_called_once(self, tmp_path):
        from job_pipeline import run_pitch_file_job

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake")
        words = _make_words()
        report = _make_report()

        with (
            patch("job_pipeline.transcribe_audio", return_value=words) as mock_asr,
            patch("job_pipeline.evaluate_pitch", return_value=report),
            patch("job_pipeline.apply_asr_original_text_override", return_value=report),
        ):
            run_pitch_file_job(
                audio,
                _make_params(tmp_path),
                skip_html_export=True,
            )

        mock_asr.assert_called_once()

    def test_no_cached_words_returns_asr_words(self, tmp_path):
        from job_pipeline import run_pitch_file_job

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake")
        asr_words = _make_words(("ASR词A", "ASR词B"))
        report = _make_report()

        with (
            patch("job_pipeline.transcribe_audio", return_value=asr_words),
            patch("job_pipeline.evaluate_pitch", return_value=report),
            patch("job_pipeline.apply_asr_original_text_override", return_value=report),
        ):
            words_out, _ = run_pitch_file_job(
                audio,
                _make_params(tmp_path),
                skip_html_export=True,
            )

        assert [w.text for w in words_out] == ["ASR词A", "ASR词B"]


class TestCachedWordsWritesTranscriptionJson:
    """提供 cached_words 时，转写 JSON 仍须落盘（归档一致性）。"""

    def test_transcription_json_written_from_cache(self, tmp_path):
        from job_pipeline import run_pitch_file_job

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake")
        cached = _make_words(("缓存词甲", "缓存词乙"))
        report = _make_report()
        trans_path = tmp_path / "trans.json"

        with (
            patch("job_pipeline.transcribe_audio"),
            patch("job_pipeline.evaluate_pitch", return_value=report),
            patch("job_pipeline.apply_asr_original_text_override", return_value=report),
        ):
            run_pitch_file_job(
                audio,
                _make_params(tmp_path),
                skip_html_export=True,
                cached_words=cached,
            )

        assert trans_path.exists(), "转写 JSON 文件必须存在"
        data = json.loads(trans_path.read_text(encoding="utf-8"))
        assert isinstance(data, list), "转写 JSON 应为列表"
        texts = [item["text"] for item in data]
        assert "缓存词甲" in texts
        assert "缓存词乙" in texts

    def test_transcription_json_word_count_matches_cache(self, tmp_path):
        from job_pipeline import run_pitch_file_job

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake")
        cached = _make_words(("甲", "乙", "丙", "丁"))
        report = _make_report()

        with (
            patch("job_pipeline.transcribe_audio"),
            patch("job_pipeline.evaluate_pitch", return_value=report),
            patch("job_pipeline.apply_asr_original_text_override", return_value=report),
        ):
            run_pitch_file_job(
                audio,
                _make_params(tmp_path),
                skip_html_export=True,
                cached_words=cached,
            )

        data = json.loads((tmp_path / "trans.json").read_text(encoding="utf-8"))
        assert len(data) == 4


class TestCachedWordsSkipsPolish:
    """cached_words 传入时不应调用 polish_transcription_text（缓存内已是润色版）。"""

    def test_polish_not_called_when_cache_hit(self, tmp_path):
        """内存/磁盘缓存命中时，跳过 ASR 润色（防二次润色漂移）。"""
        from job_pipeline import run_pitch_file_job, PitchFileJobParams
        from schema import TranscriptionWord, AnalysisReport, SceneAnalysis

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake")
        cached = [
            TranscriptionWord(
                word_index=i, text=t, start_time=float(i) * 0.5,
                end_time=float(i) * 0.5 + 0.4, speaker_id="spk_a"
            )
            for i, t in enumerate(("已润色词甲", "已润色词乙"))
        ]

        report = AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="测试", speaker_roles="A vs B"),
            total_score=85, total_score_deduction_reason="测试", risk_points=[],
        )
        params = PitchFileJobParams(
            transcription_json_path=tmp_path / "trans.json",
            analysis_json_path=tmp_path / "analysis.json",
            html_output_path=tmp_path / "report.html",
            sensitive_words=[],
            explicit_context={
                "biz_type": "01_机构路演", "exact_roles": "A vs B",
                "project_name": "proj", "interviewee": "张三",
                "session_notes": "", "sniper_targets_json": "[]",
                "recording_label": "test.wav",
            },
            qa_text="", model_choice="deepseek",
            skip_asr_polish=False,  # 不手动关闭，验证缓存命中时自动跳过
        )

        with (
            patch("job_pipeline.transcribe_audio"),
            patch("job_pipeline.evaluate_pitch", return_value=report),
            patch("job_pipeline.apply_asr_original_text_override", return_value=report),
            patch("job_pipeline.polish_transcription_text") as mock_polish,
        ):
            run_pitch_file_job(audio, params, skip_html_export=True, cached_words=cached)

        mock_polish.assert_not_called()

    def test_polish_called_when_no_cache(self, tmp_path):
        """非缓存路径（首次转写）在 skip_asr_polish=False 时仍应调用润色。"""
        from job_pipeline import run_pitch_file_job, PitchFileJobParams
        from schema import TranscriptionWord, AnalysisReport, SceneAnalysis

        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake")
        words = [
            TranscriptionWord(
                word_index=i, text=f"词{i}", start_time=float(i),
                end_time=float(i) + 0.9, speaker_id="spk_a"
            )
            for i in range(2)
        ]
        report = AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="测试", speaker_roles="A vs B"),
            total_score=85, total_score_deduction_reason="测试", risk_points=[],
        )
        params = PitchFileJobParams(
            transcription_json_path=tmp_path / "trans.json",
            analysis_json_path=tmp_path / "analysis.json",
            html_output_path=tmp_path / "report.html",
            sensitive_words=[],
            explicit_context={
                "biz_type": "01_机构路演", "exact_roles": "A vs B",
                "project_name": "proj", "interviewee": "张三",
                "session_notes": "", "sniper_targets_json": "[]",
                "recording_label": "test.wav",
            },
            qa_text="", model_choice="deepseek",
            skip_asr_polish=False,
        )

        with (
            patch("job_pipeline.transcribe_audio", return_value=words),
            patch("job_pipeline.evaluate_pitch", return_value=report),
            patch("job_pipeline.apply_asr_original_text_override", return_value=report),
            patch("job_pipeline.polish_transcription_text", return_value=words) as mock_polish,
        ):
            run_pitch_file_job(audio, params, skip_html_export=True)

        mock_polish.assert_called_once()


class TestFileMd5Logic:
    """MD5 缓存键逻辑（纯函数层面验证）。"""

    def test_same_bytes_produce_same_hash(self):
        import hashlib
        data = b"test audio content"
        h1 = hashlib.md5(data).hexdigest()
        h2 = hashlib.md5(data).hexdigest()
        assert h1 == h2

    def test_different_bytes_produce_different_hash(self):
        import hashlib
        h1 = hashlib.md5(b"audio_A").hexdigest()
        h2 = hashlib.md5(b"audio_B").hexdigest()
        assert h1 != h2

    def test_hash_is_32_hex_chars(self):
        import hashlib
        h = hashlib.md5(b"something").hexdigest()
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
