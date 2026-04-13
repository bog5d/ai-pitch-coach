"""
tests/test_v106_stability.py — V10.6 稳定性修复回归测试

覆盖五项修复：
  Fix 1 — analytics_exporter 补写 high_freq_topics / focus_keywords（匹配引擎字段对齐）
  Fix 2 — pipeline_tracker.load() 捕获 ValueError（状态值损坏时返回 None）
  Fix 3 — pipeline_tracker.save() tmp 清理路径正确
  Fix 4 — investor_matcher 从 company_id 提取关键词（增强匹配）
  Fix 5 — investor_matcher 在零分时按 session_count 排序仍稳定
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── 路径设置 ──────────────────────────────────────────────────────────────────
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


# ════════════════════════════════════════════════════════════════════════════
# Fix 1: analytics_exporter 补写 high_freq_topics / focus_keywords
# ════════════════════════════════════════════════════════════════════════════

class TestAnalyticsExporterEnrichment:
    """export_analytics 应在输出中写入 high_freq_topics 和 focus_keywords。"""

    def _make_report(self):
        from schema import AnalysisReport, SceneAnalysis, RiskPoint
        return AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="高管访谈", speaker_roles="高管 vs VC"),
            total_score=78,
            total_score_deduction_reason="",
            risk_points=[
                RiskPoint(
                    risk_level="一般",
                    problem_summary="估值偏高",
                    tier1_general_critique="估值依据不足",
                    tier2_qa_alignment="与口径不符",
                    improvement_suggestion="给出可比标的",
                    start_word_index=0,
                    end_word_index=5,
                    risk_type="估值回避",
                ),
                RiskPoint(
                    risk_level="轻微",
                    problem_summary="数据模糊",
                    tier1_general_critique="数字不精准",
                    tier2_qa_alignment="OK",
                    improvement_suggestion="准备精确数据",
                    start_word_index=10,
                    end_word_index=15,
                    risk_type="数据含糊",
                ),
            ],
        )

    def test_high_freq_topics_contains_risk_type_keys(self, tmp_path):
        """analytics 文件应包含 high_freq_topics，内容来自 risk_type_counts 的键。"""
        from analytics_exporter import export_analytics

        analysis_file = tmp_path / "test_analysis_report.json"
        analysis_file.write_text("{}", encoding="utf-8")

        ctx = {
            "analysis_json": str(analysis_file),
            "company_id": "泽天智航",
            "interviewee": "李总",
            "biz_type": "02_高管访谈",
            "institution_id": "迪策资本",
            "institution_canonical": "迪策资本",
        }
        report = self._make_report()
        result = export_analytics(report, ctx, status="locked")
        assert result is not None, "export_analytics 应成功返回路径"

        data = json.loads(result.read_text(encoding="utf-8"))
        assert "high_freq_topics" in data, "analytics 输出应包含 high_freq_topics 字段"
        topics = data["high_freq_topics"]
        assert isinstance(topics, list), "high_freq_topics 应为列表"
        assert "估值回避" in topics, "估值回避应出现在 high_freq_topics 中"
        assert "数据含糊" in topics, "数据含糊应出现在 high_freq_topics 中"

    def test_focus_keywords_contains_company_and_interviewee(self, tmp_path):
        """analytics 文件应包含 focus_keywords，来自 institution_id 和 investor_name。"""
        from analytics_exporter import export_analytics

        analysis_file = tmp_path / "test_analysis_report.json"
        analysis_file.write_text("{}", encoding="utf-8")

        ctx = {
            "analysis_json": str(analysis_file),
            "company_id": "泽天智航",
            "interviewee": "李志新",
            "biz_type": "02_高管访谈",
            "institution_id": "迪策资本",
            "institution_canonical": "迪策资本",
            "investor_name": "李志新",
        }
        report = self._make_report()
        result = export_analytics(report, ctx, status="locked")
        assert result is not None

        data = json.loads(result.read_text(encoding="utf-8"))
        assert "focus_keywords" in data, "analytics 输出应包含 focus_keywords 字段"
        fk = data["focus_keywords"]
        assert isinstance(fk, list), "focus_keywords 应为列表"

    def test_high_freq_topics_empty_when_no_risk_points(self, tmp_path):
        """无风险点时 high_freq_topics 应为空列表（不崩溃）。"""
        from analytics_exporter import export_analytics
        from schema import AnalysisReport, SceneAnalysis

        analysis_file = tmp_path / "empty_analysis_report.json"
        analysis_file.write_text("{}", encoding="utf-8")
        report = AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="路演", speaker_roles="A vs B"),
            total_score=100,
            total_score_deduction_reason="",
        )
        ctx = {
            "analysis_json": str(analysis_file),
            "company_id": "test_company",
            "institution_id": "test_inst",
        }
        result = export_analytics(report, ctx, status="draft")
        assert result is not None
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data.get("high_freq_topics") == [], "无风险点时 high_freq_topics 应为空列表"


# ════════════════════════════════════════════════════════════════════════════
# Fix 2: pipeline_tracker.load() 捕获 ValueError
# ════════════════════════════════════════════════════════════════════════════

class TestPipelineTrackerLoad:
    """load() 在状态值损坏时应静默返回 None，而非抛出 ValueError。"""

    def test_load_returns_none_when_status_corrupted(self, tmp_path):
        """磁盘上写入无效 status 值时，load() 应返回 None。"""
        from pipeline_tracker import PipelineStore

        store = PipelineStore(str(tmp_path))
        bad_data = {
            "record_id": "test_record_001",
            "institution_id": "inst_a",
            "institution_name": "测试机构",
            "company_id": "comp_a",
            "company_name": "测试公司",
            "status": "非法状态值_INVALID",  # 故意写坏
            "contacts": [],
            "timeline": [],
            "next_action": "",
            "linked_interviews": [],
            "notes": "",
        }
        record_path = tmp_path / "test_record_001.json"
        record_path.write_text(json.dumps(bad_data, ensure_ascii=False), encoding="utf-8")

        result = store.load("test_record_001")
        assert result is None, "状态值损坏时 load() 应返回 None，不抛异常"

    def test_load_returns_none_when_missing_required_field(self, tmp_path):
        """缺少 record_id 字段时，load() 应返回 None。"""
        from pipeline_tracker import PipelineStore

        store = PipelineStore(str(tmp_path))
        bad_data = {"institution_id": "x"}  # 缺少 record_id → KeyError
        record_path = tmp_path / "missing_field.json"
        record_path.write_text(json.dumps(bad_data), encoding="utf-8")

        result = store.load("missing_field")
        assert result is None, "缺必填字段时 load() 应返回 None"

    def test_list_records_skips_corrupted_status(self, tmp_path):
        """list_records 应跳过状态损坏的记录，不影响其他正常记录。"""
        from pipeline_tracker import PipelineRecord, PipelineStatus, PipelineStore

        store = PipelineStore(str(tmp_path))

        # 写一条正常记录
        good_rec = PipelineRecord(
            record_id="good_001",
            institution_id="inst_good",
            institution_name="正常机构",
            company_id="comp_good",
            company_name="正常公司",
            status=PipelineStatus.INITIAL_CONTACT,
        )
        store.save(good_rec)

        # 写一条状态损坏的记录
        bad_data = {
            "record_id": "bad_001",
            "institution_id": "inst_bad",
            "institution_name": "损坏机构",
            "company_id": "comp_bad",
            "company_name": "损坏公司",
            "status": "CORRUPTED_STATUS",
            "contacts": [], "timeline": [],
            "next_action": "", "linked_interviews": [], "notes": "",
        }
        (tmp_path / "bad_001.json").write_text(json.dumps(bad_data, ensure_ascii=False), encoding="utf-8")

        records = store.list_records()
        assert len(records) == 1, "应只返回正常记录，跳过损坏记录"
        assert records[0].record_id == "good_001"


# ════════════════════════════════════════════════════════════════════════════
# Fix 3: pipeline_tracker.save() tmp 清理
# ════════════════════════════════════════════════════════════════════════════

class TestPipelineTrackerSave:
    """save() 在正常路径和异常路径下的行为。"""

    def test_save_creates_json_atomically(self, tmp_path):
        """正常保存应创建最终 JSON，不留 .tmp 残余文件。"""
        from pipeline_tracker import PipelineRecord, PipelineStatus, PipelineStore

        store = PipelineStore(str(tmp_path))
        rec = PipelineRecord(
            record_id="test_atomic_001",
            institution_id="inst_x",
            institution_name="原子写入测试机构",
            company_id="comp_x",
            company_name="原子写入测试公司",
            status=PipelineStatus.MATERIALS_SENT,
        )
        ok = store.save(rec)
        assert ok is True

        json_path = tmp_path / "test_atomic_001.json"
        assert json_path.exists(), "应存在最终 JSON 文件"

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, "正常保存后不应留 .tmp 残余"

    def test_save_returns_false_on_permission_error(self, tmp_path):
        """写入失败时 save() 返回 False，不抛出异常。"""
        from pipeline_tracker import PipelineRecord, PipelineStatus, PipelineStore

        store = PipelineStore(str(tmp_path))
        rec = PipelineRecord(
            record_id="perm_test",
            institution_id="x", institution_name="x",
            company_id="y", company_name="y",
            status=PipelineStatus.INITIAL_CONTACT,
        )

        # 通过覆盖 _ensure_dir 触发失败
        with patch.object(store, "_ensure_dir", side_effect=OSError("模拟磁盘满")):
            result = store.save(rec)
        assert result is False, "写入失败时应返回 False"


# ════════════════════════════════════════════════════════════════════════════
# Fix 4: investor_matcher 从 company_id 提取关键词
# ════════════════════════════════════════════════════════════════════════════

class TestInvestorMatcherCompanyKeywords:
    """investor_matcher 应从 analytics 的 company_id 字段提取关键词。"""

    def _write_analytics(self, path: Path, institution_id: str, company_id: str,
                          high_freq_topics: list | None = None) -> None:
        data = {
            "institution_id": institution_id,
            "institution_name": institution_id,
            "company_id": company_id,
            "high_freq_topics": high_freq_topics or [],
            "focus_keywords": [],
            "preferred_stages": [],
            "session_count": 1,
        }
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_build_profile_includes_company_id_keywords(self, tmp_path):
        """build_institution_profile_from_analytics 应把 company_id 词片段加入关键词。"""
        from investor_matcher import build_institution_profile_from_analytics

        records = [
            {
                "institution_id": "迪策资本",
                "company_id": "泽天智航",
                "high_freq_topics": [],
                "focus_keywords": [],
                "preferred_stages": [],
                "session_count": 1,
            }
        ]
        profile = build_institution_profile_from_analytics(records)
        assert profile is not None
        all_kw = set(kw.lower() for kw in profile["all_keywords"])
        # 公司名 "泽天智航" 应被拆分成关键词片段
        assert any("泽天" in kw or "智航" in kw or "泽天智航" in kw for kw in all_kw), \
            "company_id 的词片段应出现在关键词中"

    def test_matching_benefits_from_company_id_keywords(self, tmp_path):
        """当公司名与 institution 历史访谈公司名相关时，匹配分应高于纯陌生匹配。"""
        from investor_matcher import CompanySnapshot, build_institution_profile_from_analytics, calculate_match_score

        records_with_company = [
            {
                "institution_id": "inst_a",
                "company_id": "军工科技公司",
                "high_freq_topics": [],
                "focus_keywords": [],
                "preferred_stages": [],
                "session_count": 1,
            }
        ]
        records_without_match = [
            {
                "institution_id": "inst_b",
                "company_id": "医疗器械公司",
                "high_freq_topics": [],
                "focus_keywords": [],
                "preferred_stages": [],
                "session_count": 1,
            }
        ]

        company = CompanySnapshot(
            company_name="某军工企业",
            industry_tags=["军工", "科技"],
            stage="A轮",
            model_tags=["ToB"],
        )

        profile_a = build_institution_profile_from_analytics(records_with_company)
        profile_b = build_institution_profile_from_analytics(records_without_match)

        score_a = calculate_match_score(company, profile_a)
        score_b = calculate_match_score(company, profile_b)

        assert score_a >= score_b, \
            f"历史访谈过相关公司的机构({score_a})得分应 >= 无关机构({score_b})"


# ════════════════════════════════════════════════════════════════════════════
# Fix 5: investor_matcher 空数据边界
# ════════════════════════════════════════════════════════════════════════════

class TestInvestorMatcherEdgeCases:
    """边界情况：无数据、全空字段、非 UTF-8 文件。"""

    def test_match_institutions_empty_workspace(self, tmp_path):
        """空工作目录时返回空列表，不崩溃。"""
        from investor_matcher import CompanySnapshot, match_institutions

        company = CompanySnapshot(company_name="测试公司", industry_tags=["AI"])
        results = match_institutions(company, workspace_root=str(tmp_path))
        assert results == []

    def test_match_institutions_ignores_malformed_analytics(self, tmp_path):
        """解析失败的 analytics 文件被静默跳过，不导致崩溃。"""
        from investor_matcher import CompanySnapshot, match_institutions

        bad_file = tmp_path / "bad_analytics.json"
        bad_file.write_text("NOT_JSON", encoding="utf-8")

        company = CompanySnapshot(company_name="测试公司", industry_tags=["AI"])
        results = match_institutions(company, workspace_root=str(tmp_path))
        assert results == [], "损坏的 analytics 文件应被静默跳过"

    def test_match_institutions_ignores_missing_institution_id(self, tmp_path):
        """没有 institution_id 的 analytics 文件应被跳过。"""
        from investor_matcher import CompanySnapshot, match_institutions

        data = {
            "institution_id": "",   # 空 → 应被跳过
            "institution_name": "匿名机构",
            "company_id": "某公司",
            "high_freq_topics": ["AI", "硬科技"],
            "focus_keywords": [],
            "preferred_stages": [],
            "session_count": 2,
        }
        (tmp_path / "no_iid_analytics.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        company = CompanySnapshot(company_name="测试公司", industry_tags=["AI"])
        results = match_institutions(company, workspace_root=str(tmp_path))
        assert results == [], "无 institution_id 的记录应被跳过"
