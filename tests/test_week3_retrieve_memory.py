"""Week 3：retrieve_memory 节点与 job_pipeline LangGraph 路径不预加载。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture
def mem():
    from schema import ExecutiveMemory

    return ExecutiveMemory(tag="张", raw_text="易错", correction="口径", weight=2.0)


def test_node_retrieve_memory_skips_episodic_when_tenant_unknown():
    from agent_nodes import node_retrieve_memory

    state = {
        "tenant_id": "unknown",
        "trace_id": "t1",
        "explicit_context": {"interviewee": "张三"},
        "qa_text": "",
        "words": [],
    }
    with (
        patch("agent_nodes.load_top_executive_memories_for_prompt") as mock_load,
        patch("agent_nodes.load_asset_index", return_value=[]),
    ):
        out = node_retrieve_memory(state)  # type: ignore[arg-type]
    mock_load.assert_not_called()
    assert out["memory_io_enabled"] is False
    assert out["historical_memories"] is None


def test_node_retrieve_memory_loads_when_company_and_interviewee_ok(mem):
    from agent_nodes import node_retrieve_memory

    state = {
        "tenant_id": "co1",
        "trace_id": "t1",
        "explicit_context": {"interviewee": "张三"},
        "qa_text": "芯片",
        "words": [],
    }
    fake_asset = [{"filename": "a.pdf", "summary": "芯片设计", "tags": [], "relative_path": ""}]
    with (
        patch(
            "agent_nodes.load_top_executive_memories_for_prompt",
            return_value=[mem],
        ) as mock_load,
        patch("agent_nodes.record_executive_memory_prompt_hits") as mock_rec,
        patch("agent_nodes.load_asset_index", return_value=fake_asset),
    ):
        out = node_retrieve_memory(state)  # type: ignore[arg-type]
    mock_load.assert_called_once_with("co1", "张三", limit=5)
    mock_rec.assert_called_once()
    assert out["memory_io_enabled"] is True
    assert out["memory_company_id"] == "co1"
    assert out["historical_memories"] == [mem]
    assert out["asset_index_count"] == 1
    assert len(out["asset_hits"]) >= 1


def test_job_pipeline_langgraph_skips_preload_load_top(tmp_path, mem):
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
    )
    with (
        patch.object(jp, "transcribe_audio", return_value=mock_words),
        patch.object(jp, "polish_transcription_text", side_effect=lambda w, **kw: w),
        patch.object(jp, "run_pitch_evaluation_via_langgraph", return_value=mock_report),
        patch.object(jp, "evaluate_pitch") as mock_legacy,
        patch.object(jp, "mask_words_for_llm", side_effect=lambda w, _: w),
        patch.object(
            jp,
            "load_top_executive_memories_for_prompt",
            return_value=[mem],
        ) as mock_load,
        patch.object(jp, "record_executive_memory_prompt_hits") as mock_rec,
        patch.object(jp, "apply_asr_original_text_override", return_value=mock_report),
        patch.object(jp, "generate_html_report", MagicMock()),
    ):
        audio = tmp_path / "t.wav"
        audio.write_bytes(b"x")
        jp.run_pitch_file_job(audio, params, skip_html_export=True)
    mock_load.assert_not_called()
    mock_rec.assert_not_called()
    mock_legacy.assert_not_called()
