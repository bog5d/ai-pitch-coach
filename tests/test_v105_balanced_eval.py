"""
V10.5 平衡评估：positive_highlights 字段 + HTML 模板亮点展示区。
遵循铁律五：全量 Mock，零 API 费用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import (  # noqa: E402
    AnalysisReport,
    RiskPoint,
    RiskScanResult,
    SceneAnalysis,
    TranscriptionWord,
)


# ─── 工具函数 ────────────────────────────────────────────────────────────────

def _tw(i: int, text: str = "词") -> TranscriptionWord:
    return TranscriptionWord(
        word_index=i,
        text=text,
        start_time=float(i) * 0.1,
        end_time=float(i) * 0.1 + 0.08,
        speaker_id="spk_a",
    )


def _resp(content: str) -> MagicMock:
    ch = MagicMock()
    ch.message.content = content
    r = MagicMock()
    r.choices = [ch]
    return r


# ─── Schema 层测试 ────────────────────────────────────────────────────────────

class TestPositiveHighlightsSchema:
    def test_analysis_report_has_positive_highlights_field(self):
        """AnalysisReport 模型含 positive_highlights 字段，默认为空列表。"""
        report = AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="路演", speaker_roles="创始人 vs VC"),
            total_score=85,
        )
        assert hasattr(report, "positive_highlights")
        assert isinstance(report.positive_highlights, list)
        assert report.positive_highlights == []

    def test_analysis_report_accepts_highlights(self):
        """positive_highlights 可以正常赋值与读取。"""
        highlights = ["清晰表达了核心产品定位", "数据列举精准，有具体数字支撑"]
        report = AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="尽调", speaker_roles="高管 vs IC"),
            total_score=78,
            positive_highlights=highlights,
        )
        assert report.positive_highlights == highlights

    def test_risk_scan_result_has_highlights_field(self):
        """RiskScanResult 模型含 highlights 字段，默认为空列表。"""
        scan = RiskScanResult(
            scene_analysis=SceneAnalysis(scene_type="路演", speaker_roles="创始人"),
        )
        assert hasattr(scan, "highlights")
        assert isinstance(scan.highlights, list)
        assert scan.highlights == []

    def test_risk_scan_result_accepts_highlights(self):
        """RiskScanResult highlights 字段可正常赋值。"""
        scan = RiskScanResult(
            scene_analysis=SceneAnalysis(scene_type="路演", speaker_roles="创始人"),
            highlights=["回答条理清晰", "主动补充背景数据"],
        )
        assert len(scan.highlights) == 2

    def test_existing_json_without_highlights_still_valid(self):
        """旧版 JSON（无 positive_highlights）仍能通过 Pydantic 验证（向后兼容）。"""
        old_json = {
            "scene_analysis": {"scene_type": "路演", "speaker_roles": "创始人"},
            "total_score": 80,
            "total_score_deduction_reason": "扣20分",
            "risk_points": [],
        }
        report = AnalysisReport.model_validate(old_json)
        assert report.positive_highlights == []

    def test_highlights_serialized_in_model_dump(self):
        """positive_highlights 能正常序列化为 dict/JSON。"""
        report = AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="尽调", speaker_roles="高管"),
            total_score=90,
            positive_highlights=["表达流畅", "逻辑清晰"],
        )
        dumped = report.model_dump()
        assert "positive_highlights" in dumped
        assert dumped["positive_highlights"] == ["表达流畅", "逻辑清晰"]

        json_str = json.dumps(report.model_dump(), ensure_ascii=False)
        restored = AnalysisReport.model_validate_json(json_str)
        assert restored.positive_highlights == ["表达流畅", "逻辑清晰"]


# ─── LLM 管道层测试 ──────────────────────────────────────────────────────────

class TestEvaluatePitchHighlights:
    """验证 evaluate_pitch 能将 scan.highlights 传递给 AnalysisReport。"""

    def test_highlights_passed_from_scan_to_report(self):
        """阶段一扫描返回的 highlights 应出现在最终 AnalysisReport 中。"""
        from llm_judge import evaluate_pitch

        words = [_tw(i) for i in range(10)]

        scan_payload = {
            "scene_analysis": {"scene_type": "路演", "speaker_roles": "创始人 vs IC"},
            "targets": [],
            "highlights": ["开场思路清晰", "数据引用准确"],
        }
        with patch("llm_judge._make_client") as mk:
            client = MagicMock()
            client.chat.completions.create.return_value = _resp(
                json.dumps(scan_payload, ensure_ascii=False)
            )
            mk.return_value = (client, "deepseek-chat")
            with patch("llm_judge.run_with_backoff", side_effect=lambda fn, **kw: fn()):
                report = evaluate_pitch(words, model_choice="deepseek")

        assert report.positive_highlights == ["开场思路清晰", "数据引用准确"]

    def test_missing_highlights_in_scan_defaults_to_empty(self):
        """旧版扫描结果不含 highlights 字段时，AnalysisReport.positive_highlights 为空列表。"""
        from llm_judge import evaluate_pitch

        words = [_tw(i) for i in range(10)]

        scan_payload = {
            "scene_analysis": {"scene_type": "尽调", "speaker_roles": "高管"},
            "targets": [],
            # 无 highlights 字段（旧版格式）
        }
        with patch("llm_judge._make_client") as mk:
            client = MagicMock()
            client.chat.completions.create.return_value = _resp(
                json.dumps(scan_payload, ensure_ascii=False)
            )
            mk.return_value = (client, "deepseek-chat")
            with patch("llm_judge.run_with_backoff", side_effect=lambda fn, **kw: fn()):
                report = evaluate_pitch(words, model_choice="deepseek")

        assert report.positive_highlights == []

    def test_highlights_with_risk_points(self):
        """同时含 highlights 和 risk_points 时两者均正确传递。"""
        from llm_judge import evaluate_pitch

        words = [_tw(i) for i in range(20)]

        scan_payload = {
            "scene_analysis": {"scene_type": "路演", "speaker_roles": "创始人 vs IC"},
            "targets": [
                {
                    "start_word_index": 0,
                    "end_word_index": 5,
                    "problem_description": "数据含糊",
                    "risk_type": "数据含糊",
                }
            ],
            "highlights": ["逻辑框架清晰", "主动补充市场数据"],
        }
        rp_payload = {
            "risk_level": "一般",
            "tier1_general_critique": "数据引用不精确",
            "tier2_qa_alignment": "未提供 QA，基于行业常识推断",
            "improvement_suggestion": "建议提供精确数字",
            "original_text": "大约这么多",
            "start_word_index": 0,
            "end_word_index": 5,
            "score_deduction": 5,
            "deduction_reason": "数据模糊",
            "is_manual_entry": False,
        }
        with patch("llm_judge._make_client") as mk:
            client = MagicMock()
            client.chat.completions.create.side_effect = [
                _resp(json.dumps(scan_payload, ensure_ascii=False)),
                _resp(json.dumps(rp_payload, ensure_ascii=False)),
            ]
            mk.return_value = (client, "deepseek-chat")
            with patch("llm_judge.run_with_backoff", side_effect=lambda fn, **kw: fn()):
                report = evaluate_pitch(words, model_choice="deepseek")

        assert report.positive_highlights == ["逻辑框架清晰", "主动补充市场数据"]
        assert len(report.risk_points) == 1
        assert report.total_score == 95


# ─── HTML 报告层测试 ──────────────────────────────────────────────────────────

class TestHtmlHighlights:
    """验证亮点能正确渲染到 HTML 报告中。"""

    def _make_report(self, highlights: list[str]) -> AnalysisReport:
        return AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="路演", speaker_roles="创始人"),
            total_score=85,
            positive_highlights=highlights,
        )

    def _gen_html(self, report: AnalysisReport) -> str:
        from report_builder import generate_html_report
        import tempfile, os
        # 使用 tests/dummy.wav 作为音频占位
        audio_path = ROOT / "tests" / "dummy.wav"
        if not audio_path.is_file():
            pytest.skip("dummy.wav 不存在，跳过 HTML 生成测试")
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            out = Path(f.name)
        try:
            generate_html_report(audio_path, [], report, out)
            html = out.read_text(encoding="utf-8")
        finally:
            out.unlink(missing_ok=True)
        return html

    def test_highlights_appear_in_html(self):
        """HTML 报告中应包含亮点文字。"""
        report = self._make_report(["回答结构清晰", "引用了精确数字"])
        html = self._gen_html(report)
        assert "回答结构清晰" in html
        assert "引用了精确数字" in html
        assert "做得好的地方" in html  # V10.4：标题改为"做得好的地方"

    def test_no_highlights_section_when_empty(self):
        """无亮点时 HTML 中不应出现亮点 div 渲染块（CSS 类定义仍存在于 style）。"""
        report = self._make_report([])
        html = self._gen_html(report)
        # 当 highlights 为空时，Jinja2 的 {% if %} 块不渲染，div 元素不出现
        assert '<div class="highlights-section">' not in html
        assert "做得好的地方" not in html

    def test_highlights_escaped_safely(self):
        """亮点文本中的 HTML 特殊字符应被安全转义。"""
        report = self._make_report(["表述中引用了<数据>与&符号"])
        html = self._gen_html(report)
        assert "<数据>" not in html  # 已被转义
        assert "&lt;数据&gt;" in html
