"""Week 5：job_pipeline -> app 的 agent_state_collector 透传。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def test_job_pipeline_collects_langgraph_state_excerpt(tmp_path):
    import job_pipeline as jp
    from schema import AnalysisReport, SceneAnalysis, TranscriptionWord

    mock_words = [
        TranscriptionWord(word_index=0, text="x", start_time=0.0, end_time=1.0, speaker_id="S1")
    ]
    mock_report = AnalysisReport(
        scene_analysis=SceneAnalysis(scene_type="t", speaker_roles="t"),
        total_score=100,
        risk_points=[],
    )
    captured: dict = {}

    def _collect(d: dict) -> None:
        captured.update(d)

    params = jp.PitchFileJobParams(
        transcription_json_path=tmp_path / "a.json",
        analysis_json_path=tmp_path / "b.json",
        html_output_path=tmp_path / "c.html",
        sensitive_words=[],
        explicit_context=jp.build_explicit_context("01_机构路演", "项目", "张三"),
        qa_text="",
        memory_company_id="co1",
        skip_asr_polish=True,
        use_langgraph_v1=True,
        agent_state_collector=_collect,
    )

    with (
        patch.object(jp, "transcribe_audio", return_value=mock_words),
        patch.object(jp, "polish_transcription_text", side_effect=lambda w, **kw: w),
        patch.object(
            jp,
            "run_pitch_evaluation_via_langgraph_with_state",
            return_value=(mock_report, {"asset_summary_markdown": "- A.pdf：摘要A"}),
        ),
        patch.object(jp, "mask_words_for_llm", side_effect=lambda w, _: w),
        patch.object(jp, "apply_asr_original_text_override", return_value=mock_report),
        patch.object(jp, "generate_html_report", MagicMock()),
    ):
        audio = tmp_path / "t.wav"
        audio.write_bytes(b"x")
        jp.run_pitch_file_job(audio, params, skip_html_export=True)

    assert captured.get("asset_summary_markdown") == "- A.pdf：摘要A"
