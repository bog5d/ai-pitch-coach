"""
job_pipeline 纯函数与上下文契约（不调用外网 API）。
仓库发版 V7.0（与根目录 build_release.py → CURRENT_VERSION 对齐）。
运行：python tests/test_job_pipeline.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from job_pipeline import (  # noqa: E402
    OTHER_SCENE_KEY,
    apply_html_filename_masks,
    build_explicit_context,
    mask_words_for_llm,
)
from report_builder import HtmlExportOptions, _report_for_html_display  # noqa: E402
from schema import AnalysisReport, RiskPoint, SceneAnalysis, TranscriptionWord  # noqa: E402


class TestJobPipeline(unittest.TestCase):
    def test_build_explicit_context_other_scene_uses_custom_roles(self) -> None:
        ctx = build_explicit_context(
            OTHER_SCENE_KEY,
            "批次A",
            "张三",
            custom_roles_other="采购总监 vs 买方基金",
        )
        self.assertEqual(ctx["exact_roles"], "采购总监 vs 买方基金")

    def test_apply_html_filename_masks_long_key_first(self) -> None:
        m = {"迪策资本": "DC", "资本": "X"}
        self.assertEqual(
            apply_html_filename_masks("迪策资本-会议", m),
            "DC-会议",
        )

    def test_mask_words_for_llm(self) -> None:
        words = [
            TranscriptionWord(
                word_index=0,
                text="福创投很好",
                start_time=0.0,
                end_time=0.5,
                speaker_id="S1",
            )
        ]
        out = mask_words_for_llm(words, ["福创投"])
        self.assertEqual(out[0].text, "***很好")

    def test_mask_words_long_keyword_first(self) -> None:
        """长词先于短词替换，避免短词破坏长词匹配。"""
        words = [
            TranscriptionWord(
                word_index=0,
                text="我们使用华为云服务",
                start_time=0.0,
                end_time=0.5,
                speaker_id="S1",
            )
        ]
        out = mask_words_for_llm(words, ["华为", "华为云"])
        self.assertEqual(out[0].text, "我们使用***服务")

    def test_report_html_display_masking(self) -> None:
        report = AnalysisReport(
            scene_analysis=SceneAnalysis(
                scene_type="迪策资本路演",
                speaker_roles="邓勇发言",
            ),
            total_score=80,
            risk_points=[
                RiskPoint(
                    risk_level="轻微",
                    tier1_general_critique="迪策资本",
                    tier2_qa_alignment="邓勇",
                    improvement_suggestion="建议",
                    start_word_index=0,
                    end_word_index=1,
                )
            ],
        )
        masks = {"迪策资本": "DC", "邓勇": "DY"}
        disp = _report_for_html_display(report, masks)
        self.assertIn("DC", disp.scene_analysis.scene_type)
        self.assertIn("DY", disp.scene_analysis.speaker_roles)
        self.assertEqual(disp.risk_points[0].tier1_general_critique, "DC")

    def test_html_export_options_defaults(self) -> None:
        o = HtmlExportOptions()
        self.assertIsNone(o.content_replace_map)
        self.assertEqual(o.footer_watermark, "")


if __name__ == "__main__":
    unittest.main()
