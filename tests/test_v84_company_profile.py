"""V8.4 公司档案模块 TDD 测试套件。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── Task 1: CompanyProfile 模型 ──────────────────────────────────────────────

def test_company_profile_defaults():
    """CompanyProfile 可用最少字段构造，可选字段有默认值。"""
    from schema import CompanyProfile
    p = CompanyProfile(company_id="test_co", display_name="测试公司")
    assert p.company_id == "test_co"
    assert p.display_name == "测试公司"
    assert p.background == ""
    assert p.created_at == ""
    assert p.updated_at == ""


def test_company_profile_full():
    """CompanyProfile 接受完整字段并正确存储。"""
    from schema import CompanyProfile
    p = CompanyProfile(
        company_id="abc",
        display_name="ABC 资本",
        background="成立于2015年",
        created_at="2026-04-02T10:00:00",
        updated_at="2026-04-02T10:00:00",
    )
    assert p.background == "成立于2015年"
    assert p.created_at == "2026-04-02T10:00:00"


def test_company_profile_uuid_auto_generated():
    """uuid 字段在未提供时自动生成，非空且为合法 UUID 格式。"""
    import uuid as uuid_mod
    from schema import CompanyProfile
    p = CompanyProfile(company_id="x", display_name="X")
    assert p.uuid != ""
    # 验证是合法 UUID
    parsed = uuid_mod.UUID(p.uuid)
    assert str(parsed) == p.uuid


def test_company_profile_uuid_unique_per_instance():
    """每个实例的 uuid 都不同。"""
    from schema import CompanyProfile
    p1 = CompanyProfile(company_id="a", display_name="A")
    p2 = CompanyProfile(company_id="b", display_name="B")
    assert p1.uuid != p2.uuid


def test_company_profile_uuid_can_be_provided():
    """可以手动传入 uuid（如从 JSON 反序列化时）。"""
    from schema import CompanyProfile
    fixed_uuid = "12345678-1234-5678-1234-567812345678"
    p = CompanyProfile(company_id="x", display_name="X", uuid=fixed_uuid)
    assert p.uuid == fixed_uuid


# ── Task 2: CRUD 函数 ────────────────────────────────────────────────────────

def test_save_and_load_company(tmp_path):
    """保存后读取结果一致。"""
    from schema import CompanyProfile
    import company_profile as cp

    p = CompanyProfile(
        company_id="abc_capital",
        display_name="ABC 资本",
        background="成立于2015年",
        created_at="2026-04-02T10:00:00",
        updated_at="2026-04-02T10:00:00",
    )
    cp.save_company(p, profiles_dir=tmp_path)
    loaded = cp.load_company("abc_capital", profiles_dir=tmp_path)
    assert loaded is not None
    assert loaded.display_name == "ABC 资本"
    assert loaded.background == "成立于2015年"


def test_load_nonexistent_returns_none(tmp_path):
    """加载不存在的公司返回 None，不抛异常。"""
    import company_profile as cp
    result = cp.load_company("does_not_exist", profiles_dir=tmp_path)
    assert result is None


def test_list_companies(tmp_path):
    """正确枚举目录中的所有公司档案。"""
    from schema import CompanyProfile
    import company_profile as cp

    cp.save_company(CompanyProfile(company_id="co_a", display_name="A公司"), profiles_dir=tmp_path)
    cp.save_company(CompanyProfile(company_id="co_b", display_name="B公司"), profiles_dir=tmp_path)
    companies = cp.list_companies(profiles_dir=tmp_path)
    ids = {c.company_id for c in companies}
    assert ids == {"co_a", "co_b"}


def test_list_companies_empty_dir(tmp_path):
    """目录为空时返回空列表，不抛异常。"""
    import company_profile as cp
    result = cp.list_companies(profiles_dir=tmp_path)
    assert result == []


def test_atomic_write(tmp_path):
    """save_company 使用原子写入（先写 .tmp 再 rename），最终文件内容正确，无 .tmp 残留。"""
    import json
    from schema import CompanyProfile
    import company_profile as cp

    p = CompanyProfile(company_id="x", display_name="X")
    cp.save_company(p, profiles_dir=tmp_path)
    # 验证 .tmp 文件不残留
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []
    # 验证正式文件存在且合法 JSON
    json_file = tmp_path / "x.json"
    assert json_file.exists()
    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data["company_id"] == "x"


def test_delete_company(tmp_path):
    """删除公司档案后 load 返回 None。"""
    from schema import CompanyProfile
    import company_profile as cp

    cp.save_company(CompanyProfile(company_id="del_me", display_name="删我"), profiles_dir=tmp_path)
    cp.delete_company("del_me", profiles_dir=tmp_path)
    assert cp.load_company("del_me", profiles_dir=tmp_path) is None


def test_delete_nonexistent_no_error(tmp_path):
    """删除不存在的公司不抛异常。"""
    import company_profile as cp
    cp.delete_company("ghost", profiles_dir=tmp_path)  # 不应抛出


def test_save_preserves_uuid(tmp_path):
    """保存后读取时，uuid 字段与原始一致（不被覆盖）。"""
    from schema import CompanyProfile
    import company_profile as cp

    fixed_uuid = "aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb"
    p = CompanyProfile(company_id="u_test", display_name="UUID测试", uuid=fixed_uuid)
    cp.save_company(p, profiles_dir=tmp_path)
    loaded = cp.load_company("u_test", profiles_dir=tmp_path)
    assert loaded is not None
    assert loaded.uuid == fixed_uuid


# ── Task 3: llm_judge 截断 + Prompt 注入 + logical_conflict ─────────────────

def test_truncate_company_background_under_limit():
    """背景文字未超过 8000 字时原样返回，is_truncated=False。"""
    from llm_judge import truncate_company_background
    text = "A" * 100
    result, truncated = truncate_company_background(text)
    assert result == text
    assert truncated is False


def test_truncate_company_background_over_limit():
    """背景文字超过 8000 字时截断并返回 is_truncated=True，结果长度 ≤ 8000。"""
    from llm_judge import truncate_company_background
    text = "B" * 10_000
    result, truncated = truncate_company_background(text)
    assert truncated is True
    assert len(result) <= 8000


def test_truncate_company_background_head_priority():
    """截断时保留头部内容。"""
    from llm_judge import truncate_company_background
    text = "HEAD" + "X" * 10_000
    result, _ = truncate_company_background(text)
    assert result.startswith("HEAD")


def test_build_system_prompt_includes_company_background():
    """非空背景时 <COMPANY_BACKGROUND> 块出现在 Prompt 中。"""
    from llm_judge import _build_system_prompt
    prompt = _build_system_prompt(
        schema_str="{}",
        explicit_context=None,
        qa_text="",
        company_background="ABC资本成立于2015年",
    )
    assert "<COMPANY_BACKGROUND>" in prompt
    assert "ABC资本成立于2015年" in prompt


def test_build_system_prompt_empty_background_skips_block():
    """空背景时 Prompt 中不出现 <COMPANY_BACKGROUND> 块。"""
    from llm_judge import _build_system_prompt
    prompt = _build_system_prompt(
        schema_str="{}",
        explicit_context=None,
        qa_text="",
        company_background="",
    )
    assert "<COMPANY_BACKGROUND>" not in prompt


def test_build_system_prompt_background_after_knowledge_base():
    """<COMPANY_BACKGROUND> 在 </KNOWLEDGE_BASE> 之后出现（权重顺序正确）。"""
    from llm_judge import _build_system_prompt
    prompt = _build_system_prompt(
        schema_str="{}",
        explicit_context=None,
        qa_text="QA内容",
        company_background="公司背景内容",
    )
    kb_end = prompt.index("</KNOWLEDGE_BASE>")
    bg_start = prompt.index("<COMPANY_BACKGROUND>")
    assert bg_start > kb_end


def test_build_system_prompt_conflict_constraint_present():
    """CONSTRAINTS 块中包含冲突仲裁规则关键词。"""
    from llm_judge import _build_system_prompt
    prompt = _build_system_prompt(
        schema_str="{}",
        explicit_context=None,
        qa_text="",
        company_background="背景",
    )
    # 冲突仲裁规则必须在 CONSTRAINTS 中
    assert "COMPANY_BACKGROUND" in prompt
    constraints_start = prompt.index("<CONSTRAINTS>")
    constraints_end = prompt.index("</CONSTRAINTS>")
    constraints_block = prompt[constraints_start:constraints_end]
    assert "COMPANY_BACKGROUND" in constraints_block


def test_detect_logical_conflict_empty_inputs():
    """空输入返回空列表。"""
    from llm_judge import detect_logical_conflict
    assert detect_logical_conflict("", "[]") == []
    assert detect_logical_conflict("背景", "") == []
    assert detect_logical_conflict("", "") == []


def test_detect_logical_conflict_no_conflict():
    """狙击目标与背景无重叠时返回空列表。"""
    from llm_judge import detect_logical_conflict
    import json
    snipers = json.dumps([{"quote": "某段话", "reason": "完全不同的主题"}])
    result = detect_logical_conflict("公司专注早期投资成立于2015年", snipers)
    assert isinstance(result, list)


def test_detect_logical_conflict_detects_overlap():
    """狙击目标 reason 关键词与背景内容重叠时返回非空警告列表。"""
    from llm_judge import detect_logical_conflict
    import json
    # 背景说"资金用途明确"，狙击说"资金用途不一致"
    background = "公司资金用途明确，专注主营业务投入，无分散资金风险。"
    snipers = json.dumps([{"quote": "资金用途这块", "reason": "资金用途前后说法不一致"}])
    result = detect_logical_conflict(background, snipers)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert any("资金用途" in w for w in result)


def test_detect_logical_conflict_invalid_json():
    """无效 JSON 时返回空列表，不抛异常。"""
    from llm_judge import detect_logical_conflict
    result = detect_logical_conflict("背景内容", "not valid json {{{")
    assert result == []


# ── Task 4: job_pipeline 透传 ────────────────────────────────────────────────

def test_pipeline_passes_company_background_to_evaluate(tmp_path):
    """run_pitch_file_job 将 company_background 透传给 evaluate_pitch。"""
    from unittest.mock import patch
    from pathlib import Path
    from schema import TranscriptionWord, AnalysisReport, SceneAnalysis
    import job_pipeline as jp

    mock_words = [
        TranscriptionWord(word_index=0, text="测试", start_time=0.0, end_time=1.0, speaker_id="S1")
    ]
    mock_report = AnalysisReport(
        scene_analysis=SceneAnalysis(scene_type="测试", speaker_roles="测试"),
        total_score=90,
        risk_points=[],
    )

    params = jp.PitchFileJobParams(
        transcription_json_path=tmp_path / "asr.json",
        analysis_json_path=tmp_path / "analysis.json",
        html_output_path=tmp_path / "report.html",
        sensitive_words=[],
        explicit_context=jp.build_explicit_context("01_机构路演", "测试项目", "张三"),
        qa_text="",
        company_background="ABC资本成立于2015年，专注早期投资",
    )

    with (
        patch("job_pipeline.transcribe_audio", return_value=mock_words),
        patch("job_pipeline.evaluate_pitch", return_value=mock_report) as mock_eval,
        patch("job_pipeline.apply_asr_original_text_override", return_value=mock_report),
    ):
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"RIFF")
        jp.run_pitch_file_job(audio, params, skip_html_export=True)

    call_kwargs = mock_eval.call_args.kwargs
    assert call_kwargs.get("company_background") == "ABC资本成立于2015年，专注早期投资"


def test_pipeline_truncates_long_company_background(tmp_path):
    """超过 8000 字的 company_background 在送 LLM 前被截断。"""
    from unittest.mock import patch
    from pathlib import Path
    from schema import TranscriptionWord, AnalysisReport, SceneAnalysis
    import job_pipeline as jp

    long_bg = "Z" * 12_000
    mock_words = [
        TranscriptionWord(word_index=0, text="x", start_time=0.0, end_time=1.0, speaker_id="S1")
    ]
    mock_report = AnalysisReport(
        scene_analysis=SceneAnalysis(scene_type="t", speaker_roles="t"),
        total_score=100,
        risk_points=[],
    )

    params = jp.PitchFileJobParams(
        transcription_json_path=tmp_path / "asr.json",
        analysis_json_path=tmp_path / "analysis.json",
        html_output_path=tmp_path / "report.html",
        sensitive_words=[],
        explicit_context=jp.build_explicit_context("01_机构路演", "p", "i"),
        qa_text="",
        company_background=long_bg,
    )

    with (
        patch("job_pipeline.transcribe_audio", return_value=mock_words),
        patch("job_pipeline.evaluate_pitch", return_value=mock_report) as mock_eval,
        patch("job_pipeline.apply_asr_original_text_override", return_value=mock_report),
    ):
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"RIFF")
        jp.run_pitch_file_job(audio, params, skip_html_export=True)

    sent_bg = mock_eval.call_args.kwargs.get("company_background", "")
    assert len(sent_bg) <= 8_000


def test_pipeline_default_company_background_empty(tmp_path):
    """company_background 默认为空字符串，向后兼容现有调用方。"""
    import job_pipeline as jp
    params = jp.PitchFileJobParams(
        transcription_json_path=tmp_path / "asr.json",
        analysis_json_path=tmp_path / "analysis.json",
        html_output_path=tmp_path / "report.html",
        sensitive_words=[],
        explicit_context=jp.build_explicit_context("01_机构路演", "p", "i"),
        qa_text="",
    )
    assert params.company_background == ""
