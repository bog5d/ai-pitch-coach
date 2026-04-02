"""
V8.6 Prompt 注入：<HISTORICAL_PROFILE> 位置、权重排序与 Top 5 截断。

零 API：仅测 _build_system_prompt 字符串契约。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm_judge import _build_system_prompt  # noqa: E402
from schema import ExecutiveMemory  # noqa: E402


def _minimal_ctx():
    return {
        "biz_type": "测试",
        "exact_roles": "A vs B",
        "project_name": "P",
        "interviewee": "张总",
        "session_notes": "",
        "sniper_targets_json": "[]",
        "recording_label": "x",
    }


class TestHistoricalProfileInjector:
    def test_block_after_company_background(self):
        schema = json.dumps({"type": "object"})
        mems = [
            ExecutiveMemory(tag="张总", raw_text="易错A", correction="口径A", weight=1.0),
        ]
        prompt = _build_system_prompt(
            schema,
            _minimal_ctx(),
            "QA",
            "公司背景一行",
            historical_memories=mems,
        )
        i_bg = prompt.find("</COMPANY_BACKGROUND>")
        i_hist = prompt.find("<HISTORICAL_PROFILE>")
        i_task = prompt.find("<TASK>")
        assert i_bg != -1 and i_hist != -1 and i_task != -1
        assert i_bg < i_hist < i_task

    def test_empty_memories_omits_historical_block(self):
        schema = json.dumps({"type": "object"})
        p = _build_system_prompt(schema, _minimal_ctx(), "QA", "背景", historical_memories=[])
        assert "<HISTORICAL_PROFILE>" not in p

    def test_sorts_by_weight_desc_and_caps_at_five(self):
        schema = json.dumps({"type": "object"})
        mems = [
            ExecutiveMemory(tag="t", raw_text=f"w{i}", correction="c", weight=float(i))
            for i in range(1, 8)
        ]
        prompt = _build_system_prompt(
            schema,
            _minimal_ctx(),
            "QA",
            "",
            historical_memories=mems,
        )
        # Top5 权重 7..3 → w7..w3；w2、w1 截断
        assert "w7" in prompt and "w6" in prompt and "w3" in prompt
        assert "w2" not in prompt and "w1" not in prompt
        assert prompt.find("w7") < prompt.find("w3")


def test_pipeline_passes_historical_memories_to_evaluate(tmp_path):
    """job_pipeline：memory_company_id + interviewee 触发 load_top 并透传 evaluate_pitch。"""
    from unittest.mock import patch

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
    mem = ExecutiveMemory(tag="张", raw_text="易错", correction="口径", weight=2.0)

    params = jp.PitchFileJobParams(
        transcription_json_path=tmp_path / "a.json",
        analysis_json_path=tmp_path / "b.json",
        html_output_path=tmp_path / "c.html",
        sensitive_words=[],
        explicit_context=jp.build_explicit_context("01_机构路演", "项目", "张三"),
        qa_text="",
        memory_company_id="co1",
    )

    with (
        patch("job_pipeline.transcribe_audio", return_value=mock_words),
        patch("job_pipeline.evaluate_pitch", return_value=mock_report) as mock_eval,
        patch("job_pipeline.apply_asr_original_text_override", return_value=mock_report),
        patch(
            "job_pipeline.load_top_executive_memories_for_prompt",
            return_value=[mem],
        ) as mock_load,
    ):
        audio = tmp_path / "t.wav"
        audio.write_bytes(b"RIFF")
        jp.run_pitch_file_job(audio, params, skip_html_export=True)

    mock_load.assert_called_once()
    assert mock_load.call_args[0][0] == "co1"
    assert mock_load.call_args[0][1] == "张三"
    assert mock_eval.call_args.kwargs.get("historical_memories") == [mem]


def test_pipeline_skips_memory_load_when_company_empty(tmp_path):
    from unittest.mock import patch

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
        memory_company_id="",
    )

    with (
        patch("job_pipeline.transcribe_audio", return_value=mock_words),
        patch("job_pipeline.evaluate_pitch", return_value=mock_report) as mock_eval,
        patch("job_pipeline.apply_asr_original_text_override", return_value=mock_report),
        patch("job_pipeline.load_top_executive_memories_for_prompt") as mock_load,
    ):
        audio = tmp_path / "t.wav"
        audio.write_bytes(b"RIFF")
        jp.run_pitch_file_job(audio, params, skip_html_export=True)

    mock_load.assert_not_called()
    assert mock_eval.call_args.kwargs.get("historical_memories") is None
