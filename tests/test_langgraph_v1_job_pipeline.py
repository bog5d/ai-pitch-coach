"""Week 1：job_pipeline 在 use_langgraph_v1 时分流到 LangGraph 入口。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 与仓库其它测试一致：src 在 path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture
def one_word():
    from schema import TranscriptionWord

    return [
        TranscriptionWord(
            word_index=0,
            text="测",
            start_time=0.0,
            end_time=0.1,
            speaker_id="S1",
        )
    ]


@pytest.fixture
def mock_report():
    from schema import AnalysisReport, SceneAnalysis

    return AnalysisReport(
        scene_analysis=SceneAnalysis(
            scene_type="测试场景",
            speaker_roles="双方",
        ),
        total_score=100,
        total_score_deduction_reason="",
        risk_points=[],
    )


def test_run_pitch_file_job_uses_langgraph_when_flag_true(
    tmp_path, mock_report, one_word
):
    import job_pipeline as jp

    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    trans = tmp_path / "t.json"
    analysis = tmp_path / "a.json"
    html = tmp_path / "out.html"

    params = jp.PitchFileJobParams(
        transcription_json_path=trans,
        analysis_json_path=analysis,
        html_output_path=html,
        sensitive_words=[],
        explicit_context={"interviewee": "未指定", "project_name": "P"},
        qa_text="",
        use_langgraph_v1=True,
    )

    with (
        patch.object(jp, "transcribe_audio", return_value=one_word),
        patch.object(jp, "polish_transcription_text", side_effect=lambda w, **kw: w),
        patch.object(
            jp,
            "run_pitch_evaluation_via_langgraph_with_state",
            return_value=(mock_report, {}),
        ) as mock_lg,
        patch.object(jp, "evaluate_pitch") as mock_legacy,
        patch.object(jp, "mask_words_for_llm", side_effect=lambda w, _: w),
        patch.object(jp, "load_top_executive_memories_for_prompt", return_value=[]),
        patch.object(jp, "record_executive_memory_prompt_hits", MagicMock()),
        patch.object(jp, "apply_asr_original_text_override", side_effect=lambda r, _: r),
        patch.object(jp, "generate_html_report", MagicMock()),
    ):
        jp.run_pitch_file_job(audio, params, skip_html_export=True)

    mock_lg.assert_called_once()
    mock_legacy.assert_not_called()


def test_run_pitch_file_job_uses_legacy_by_default(tmp_path, mock_report, one_word):
    import job_pipeline as jp

    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    trans = tmp_path / "t.json"
    analysis = tmp_path / "a.json"
    html = tmp_path / "out.html"

    params = jp.PitchFileJobParams(
        transcription_json_path=trans,
        analysis_json_path=analysis,
        html_output_path=html,
        sensitive_words=[],
        explicit_context={"interviewee": "未指定", "project_name": "P"},
        qa_text="",
        use_langgraph_v1=False,
    )

    with (
        patch.object(jp, "transcribe_audio", return_value=one_word),
        patch.object(jp, "polish_transcription_text", side_effect=lambda w, **kw: w),
        patch.object(jp, "run_pitch_evaluation_via_langgraph") as mock_lg,
        patch.object(jp, "evaluate_pitch", return_value=mock_report),
        patch.object(jp, "mask_words_for_llm", side_effect=lambda w, _: w),
        patch.object(jp, "load_top_executive_memories_for_prompt", return_value=[]),
        patch.object(jp, "record_executive_memory_prompt_hits", MagicMock()),
        patch.object(jp, "apply_asr_original_text_override", side_effect=lambda r, _: r),
        patch.object(jp, "generate_html_report", MagicMock()),
    ):
        jp.run_pitch_file_job(audio, params, skip_html_export=True)

    mock_lg.assert_not_called()
