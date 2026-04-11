"""
V10.0 Analytics JSON 导出层测试。

验证：锁定时静默生成 {stem}_analytics.json，包含得分、风险分布、精炼次数等，
为后续跨公司数据分析打基础。失败时静默跳过，不影响主流程。

运行：pytest tests/test_v100_analytics.py -v
所有测试 zero API cost，无外部依赖。
"""
from __future__ import annotations

import json
import sys
import uuid as uuid_mod
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ── 测试辅助 ─────────────────────────────────────────────────────────────────

def _make_report(
    total_score: int = 72,
    risk_points: list | None = None,
    deduction_reason: str = "",
):
    """构造最小 AnalysisReport，无需真实 LLM。"""
    from schema import AnalysisReport, RiskPoint, SceneAnalysis

    if risk_points is None:
        risk_points = [
            RiskPoint(
                risk_level="严重",
                risk_type="数据矛盾",
                tier1_general_critique="营收预测与财务口径存在分歧。后续说明。",
                tier2_qa_alignment="与 QA 第 3 条偏离",
                improvement_suggestion="建议统一口径",
                original_text="我们预计营收...",
                start_word_index=0,
                end_word_index=5,
                score_deduction=15,
            ),
            RiskPoint(
                risk_level="一般",
                risk_type="估值回避",
                tier1_general_critique="估值追问时有回避倾向。",
                tier2_qa_alignment="",
                improvement_suggestion="直接给出区间估值",
                original_text="这个估值嘛...",
                start_word_index=6,
                end_word_index=10,
                score_deduction=8,
                needs_refinement=True,
                refinement_note="建议更具体",
            ),
            RiskPoint(
                risk_level="轻微",
                risk_type="表达模糊",
                tier1_general_critique="供应链话语权描述略显模糊。",
                tier2_qa_alignment="",
                improvement_suggestion="补充具体客户名",
                original_text="我们有很多客户...",
                start_word_index=11,
                end_word_index=15,
                score_deduction=5,
                is_manual_entry=True,
            ),
        ]
    return AnalysisReport(
        scene_analysis=SceneAnalysis(
            scene_type="硬科技机构路演",
            speaker_roles="基金经理 vs 创始人",
        ),
        total_score=total_score,
        total_score_deduction_reason=deduction_reason,
        risk_points=risk_points,
    )


def _make_ctx(tmp_path: Path, company_id: str = "迪策资本") -> dict:
    """构造模拟的 v3_ctx_{stem} 字典。"""
    analysis_json = tmp_path / "迪策资本-李志新20260108_analysis_report.json"
    analysis_json.write_text("{}", encoding="utf-8")
    return {
        "analysis_json": str(analysis_json),
        "company_id": company_id,
        "interviewee": "李志新",
        "project_name": "泽天智航",
        "biz_type": "01_机构路演",
        "audio_path": str(tmp_path / "迪策资本-李志新20260108.m4a"),
    }


# ════════════════════════════════════════════════════════
# TestExportAnalyticsFileCreation — 文件创建
# ════════════════════════════════════════════════════════

class TestExportAnalyticsFileCreation:
    """export_analytics 生成正确的文件路径。"""

    def test_creates_analytics_json_file(self, tmp_path):
        """调用后，同目录下应生成 _analytics.json 文件。"""
        from analytics_exporter import export_analytics

        report = _make_report()
        ctx = _make_ctx(tmp_path)
        result = export_analytics(report, ctx)

        assert result is not None, "返回值不应为 None"
        assert result.is_file(), f"Analytics 文件应存在：{result}"
        assert result.name.endswith("_analytics.json"), "文件名应以 _analytics.json 结尾"

    def test_analytics_file_is_valid_json(self, tmp_path):
        """生成的文件必须是合法 JSON。"""
        from analytics_exporter import export_analytics

        report = _make_report()
        ctx = _make_ctx(tmp_path)
        result = export_analytics(report, ctx)

        data = json.loads(result.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_analytics_file_next_to_analysis_json(self, tmp_path):
        """analytics 文件应与 analysis_json 在同一目录。"""
        from analytics_exporter import export_analytics

        report = _make_report()
        ctx = _make_ctx(tmp_path)
        result = export_analytics(report, ctx)

        expected_dir = Path(ctx["analysis_json"]).parent
        assert result.parent == expected_dir


# ════════════════════════════════════════════════════════
# TestExportAnalyticsContent — 数据字段正确性
# ════════════════════════════════════════════════════════

class TestExportAnalyticsContent:
    """analytics JSON 包含正确的字段值。"""

    def _load(self, tmp_path) -> dict:
        from analytics_exporter import export_analytics

        report = _make_report()
        ctx = _make_ctx(tmp_path)
        result = export_analytics(report, ctx)
        return json.loads(result.read_text(encoding="utf-8"))

    def test_required_top_level_keys(self, tmp_path):
        """必须包含所有必要顶层字段。"""
        data = self._load(tmp_path)
        required = {
            "session_id", "generated_at", "version",
            "company_id", "interviewee", "biz_type",
            "total_score", "total_risk_count",
            "risk_breakdown", "refinement_count", "ai_miss_count",
            "stage1_truncated",
        }
        missing = required - set(data.keys())
        assert not missing, f"缺少字段：{missing}"

    def test_company_id_from_ctx(self, tmp_path):
        """company_id 应来自 ctx。"""
        data = self._load(tmp_path)
        assert data["company_id"] == "迪策资本"

    def test_total_score_from_report(self, tmp_path):
        """total_score 应来自 report。"""
        data = self._load(tmp_path)
        assert data["total_score"] == 72

    def test_total_risk_count(self, tmp_path):
        """total_risk_count 应等于 risk_points 数量。"""
        data = self._load(tmp_path)
        assert data["total_risk_count"] == 3

    def test_risk_breakdown_structure(self, tmp_path):
        """risk_breakdown 应包含三个级别，每个有 count 和 total_deduction。"""
        data = self._load(tmp_path)
        rb = data["risk_breakdown"]
        assert "严重" in rb
        assert rb["严重"]["count"] == 1
        assert rb["严重"]["total_deduction"] == 15
        assert rb["一般"]["count"] == 1
        assert rb["一般"]["total_deduction"] == 8
        assert rb["轻微"]["count"] == 1
        assert rb["轻微"]["total_deduction"] == 5

    def test_refinement_count(self, tmp_path):
        """refinement_count 应等于 needs_refinement=True 的条目数。"""
        data = self._load(tmp_path)
        assert data["refinement_count"] == 1  # 只有第二条 needs_refinement=True

    def test_ai_miss_count(self, tmp_path):
        """ai_miss_count 应等于 is_manual_entry=True 的条目数。"""
        data = self._load(tmp_path)
        assert data["ai_miss_count"] == 1  # 第三条 is_manual_entry=True

    def test_stage1_truncated_false(self, tmp_path):
        """正常报告 stage1_truncated 应为 False。"""
        data = self._load(tmp_path)
        assert data["stage1_truncated"] is False

    def test_stage1_truncated_true_when_marker_present(self, tmp_path):
        """deduction_reason 含截断标记时，stage1_truncated 应为 True。"""
        from analytics_exporter import export_analytics

        report = _make_report(deduction_reason="⚠️【注意】阶段一扫描 JSON 被截断，风险点列表可能不完整。")
        ctx = _make_ctx(tmp_path)
        result = export_analytics(report, ctx)
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["stage1_truncated"] is True

    def test_session_id_is_valid_uuid(self, tmp_path):
        """session_id 应为合法 UUID 格式。"""
        data = self._load(tmp_path)
        parsed = uuid_mod.UUID(data["session_id"])  # 不合法时抛异常
        assert str(parsed) == data["session_id"]

    def test_version_field(self, tmp_path):
        """version 字段应为 V10.1。"""
        data = self._load(tmp_path)
        assert data["version"] == "V10.1"

    def test_default_status_is_locked(self, tmp_path):
        """默认（不传 status）写入的状态应为 locked。"""
        data = self._load(tmp_path)
        assert data["status"] == "locked"

    def test_draft_status_field(self, tmp_path):
        """传 status='draft' 时字段应为 draft。"""
        from analytics_exporter import export_analytics
        report = _make_report()
        ctx = _make_ctx(tmp_path)
        result = export_analytics(report, ctx, status="draft")
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["status"] == "draft"
        assert data["locked_at"] is None, "draft 时 locked_at 应为 None"

    def test_session_id_deterministic(self, tmp_path):
        """同一 stem 两次调用应生成相同的 session_id。"""
        from analytics_exporter import export_analytics
        report = _make_report()
        ctx = _make_ctx(tmp_path)
        r1 = export_analytics(report, ctx, status="draft")
        r2 = export_analytics(report, ctx, status="locked")
        d1 = json.loads(r1.read_text(encoding="utf-8"))
        d2 = json.loads(r2.read_text(encoding="utf-8"))
        assert d1["session_id"] == d2["session_id"], "同 stem draft→locked 覆写后 session_id 不变"

    def test_draft_to_locked_preserves_generated_at(self, tmp_path):
        """draft→locked 覆写时，generated_at 应保留 draft 首次写入时间。"""
        import time
        from analytics_exporter import export_analytics
        report = _make_report()
        ctx = _make_ctx(tmp_path)
        r_draft = export_analytics(report, ctx, status="draft")
        d_draft = json.loads(r_draft.read_text(encoding="utf-8"))
        generated_at_draft = d_draft["generated_at"]

        time.sleep(0.05)  # 让时间有变化
        r_locked = export_analytics(report, ctx, status="locked")
        d_locked = json.loads(r_locked.read_text(encoding="utf-8"))

        assert d_locked["generated_at"] == generated_at_draft, \
            "locked 覆写应保留 draft 的 generated_at（记录首次运行时间）"
        assert d_locked["locked_at"] is not None, "locked 覆写后 locked_at 应有值"


# ════════════════════════════════════════════════════════
# TestExportAnalyticsEdgeCases — 边界情况
# ════════════════════════════════════════════════════════

class TestExportAnalyticsEdgeCases:
    """边界情况：空风险点、缺失字段、写入失败静默。"""

    def test_empty_risk_points(self, tmp_path):
        """零风险点时，risk_breakdown 所有 count 均为 0。"""
        from analytics_exporter import export_analytics
        from schema import AnalysisReport, SceneAnalysis

        report = AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="测试", speaker_roles="A vs B"),
            total_score=100,
            total_score_deduction_reason="",
            risk_points=[],
        )
        ctx = _make_ctx(tmp_path)
        result = export_analytics(report, ctx)
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["total_risk_count"] == 0
        assert data["risk_breakdown"]["严重"]["count"] == 0
        assert data["risk_breakdown"]["一般"]["count"] == 0
        assert data["risk_breakdown"]["轻微"]["count"] == 0

    def test_missing_company_id_in_ctx(self, tmp_path):
        """ctx 中没有 company_id 时，字段为空字符串，不抛异常。"""
        from analytics_exporter import export_analytics

        report = _make_report()
        ctx = _make_ctx(tmp_path)
        del ctx["company_id"]
        result = export_analytics(report, ctx)
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["company_id"] == ""

    def test_write_failure_returns_none(self, tmp_path):
        """写入路径不可写时，返回 None 而不抛异常（静默降级）。"""
        from analytics_exporter import export_analytics

        report = _make_report()
        ctx = _make_ctx(tmp_path)
        # 指向不存在的深层目录中的一个只读路径
        ctx["analysis_json"] = "/nonexistent/deep/path/report.json"
        result = export_analytics(report, ctx)
        assert result is None, "写入失败应返回 None，不抛异常"

    def test_two_calls_overwrite(self, tmp_path):
        """同一路径第二次调用应覆盖（幂等）。"""
        from analytics_exporter import export_analytics

        report1 = _make_report(total_score=72)
        report2 = _make_report(total_score=85)
        ctx = _make_ctx(tmp_path)

        export_analytics(report1, ctx)
        export_analytics(report2, ctx)

        result_path = Path(ctx["analysis_json"]).parent / (
            Path(ctx["analysis_json"]).stem + "_analytics.json"
        )
        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert data["total_score"] == 85, "第二次调用应覆盖第一次结果"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
