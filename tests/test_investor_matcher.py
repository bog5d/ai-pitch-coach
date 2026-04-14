"""
test_investor_matcher.py — Sprint 3 投资人匹配引擎单元测试

设计原则：
  - 全部Mock，不读真实 analytics 文件，不调用LLM
  - 验证：关键词匹配 / 得分计算 / 排序 / 边界case
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from investor_matcher import (
    CompanySnapshot,
    InstitutionMatchResult,
    match_institutions,
    build_institution_profile_from_analytics,
    calculate_match_score,
    format_match_report,
)


# ─────────────────────────────────────────────
# 测试夹具
# ─────────────────────────────────────────────

@pytest.fixture
def company_snapshot():
    """模拟泽天智航的公司快照。"""
    return CompanySnapshot(
        company_name="泽天智航",
        industry_tags=["军工电子", "人工智能", "RTOS", "低空经济", "军民融合"],
        stage="B轮",
        revenue_rmb_wan=6000,
        model_tags=["ToB", "硬科技", "嵌入式"],
        highlights=["已完成A轮融资", "军工资质完整", "核心团队有国防背景"],
    )


@pytest.fixture
def analytics_records_matching():
    """高匹配度的analytics记录（军工+硬科技+B轮）。"""
    return [
        {
            "institution_id": "dikce_capital",
            "institution_name": "迪策资本",
            "high_freq_topics": ["军工电子", "硬科技", "国防", "B轮", "ToB"],
            "focus_keywords": ["军工", "硬科技", "嵌入式", "RTOS"],
            "preferred_stages": ["B轮", "C轮"],
            "session_count": 3,
        }
    ]


@pytest.fixture
def analytics_records_mismatching():
    """低匹配度的analytics记录（消费品+早期）。"""
    return [
        {
            "institution_id": "consumer_fund",
            "institution_name": "消费基金X",
            "high_freq_topics": ["消费品", "新零售", "天使轮"],
            "focus_keywords": ["电商", "直播", "C端"],
            "preferred_stages": ["天使轮", "Pre-A"],
            "session_count": 1,
        }
    ]


# ─────────────────────────────────────────────
# 1. 机构画像构建测试
# ─────────────────────────────────────────────

class TestBuildInstitutionProfile:
    def test_basic_profile_from_analytics(self, analytics_records_matching):
        profile = build_institution_profile_from_analytics(analytics_records_matching)
        assert profile["institution_id"] == "dikce_capital"
        assert profile["institution_name"] == "迪策资本"
        assert "军工电子" in profile.get("all_keywords", [])

    def test_empty_records_returns_none(self):
        profile = build_institution_profile_from_analytics([])
        assert profile is None

    def test_multiple_records_merged(self):
        records = [
            {
                "institution_id": "test_fund",
                "institution_name": "测试基金",
                "high_freq_topics": ["AI", "硬科技"],
                "focus_keywords": ["算法"],
                "preferred_stages": ["A轮"],
                "session_count": 1,
            },
            {
                "institution_id": "test_fund",
                "institution_name": "测试基金",
                "high_freq_topics": ["军工", "ToB"],
                "focus_keywords": ["嵌入式", "RTOS"],
                "preferred_stages": ["B轮"],
                "session_count": 2,
            },
        ]
        profile = build_institution_profile_from_analytics(records)
        all_kw = profile.get("all_keywords", [])
        assert "AI" in all_kw
        assert "军工" in all_kw
        assert profile["session_count"] == 3


# ─────────────────────────────────────────────
# 2. 匹配分计算测试
# ─────────────────────────────────────────────

class TestCalculateMatchScore:
    def test_high_overlap_gives_high_score(self, company_snapshot):
        inst_profile = {
            "institution_id": "dikce",
            "institution_name": "迪策",
            "all_keywords": ["军工电子", "硬科技", "RTOS", "军民融合", "ToB"],
            "preferred_stages": ["B轮"],
            "session_count": 3,
        }
        score = calculate_match_score(company_snapshot, inst_profile)
        assert score >= 60

    def test_no_overlap_gives_low_score(self, company_snapshot):
        inst_profile = {
            "institution_id": "consumer",
            "institution_name": "消费基金",
            "all_keywords": ["消费品", "电商", "C端"],
            "preferred_stages": ["天使轮"],
            "session_count": 1,
        }
        score = calculate_match_score(company_snapshot, inst_profile)
        assert score <= 30

    def test_stage_match_boosts_score(self, company_snapshot):
        """阶段完全匹配应该有加分。"""
        same_stage = {
            "institution_id": "a",
            "institution_name": "A基金",
            "all_keywords": ["军工"],
            "preferred_stages": ["B轮"],
            "session_count": 1,
        }
        diff_stage = {
            "institution_id": "b",
            "institution_name": "B基金",
            "all_keywords": ["军工"],
            "preferred_stages": ["天使轮"],
            "session_count": 1,
        }
        assert calculate_match_score(company_snapshot, same_stage) > calculate_match_score(company_snapshot, diff_stage)

    def test_score_in_0_100_range(self, company_snapshot):
        inst_profile = {
            "institution_id": "x",
            "institution_name": "X",
            "all_keywords": [],
            "preferred_stages": [],
            "session_count": 0,
        }
        score = calculate_match_score(company_snapshot, inst_profile)
        assert 0 <= score <= 100


# ─────────────────────────────────────────────
# 3. 完整匹配流程测试
# ─────────────────────────────────────────────

class TestMatchInstitutions:
    def test_returns_sorted_by_score_desc(self, company_snapshot, analytics_records_matching, analytics_records_mismatching):
        all_records = {
            "dikce_capital": analytics_records_matching,
            "consumer_fund": analytics_records_mismatching,
        }

        with patch("investor_matcher._load_analytics_by_institution", return_value=all_records):
            results = match_institutions(company_snapshot, workspace_root="/fake/workspace")

        assert len(results) >= 2
        # 分数应该从高到低排序
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_matching_inst_ranks_above_mismatching(self, company_snapshot, analytics_records_matching, analytics_records_mismatching):
        all_records = {
            "dikce_capital": analytics_records_matching,
            "consumer_fund": analytics_records_mismatching,
        }

        with patch("investor_matcher._load_analytics_by_institution", return_value=all_records):
            results = match_institutions(company_snapshot, workspace_root="/fake/workspace")

        inst_ids = [r.institution_id for r in results]
        assert inst_ids.index("dikce_capital") < inst_ids.index("consumer_fund")

    def test_empty_analytics_returns_empty_list(self, company_snapshot):
        with patch("investor_matcher._load_analytics_by_institution", return_value={}):
            results = match_institutions(company_snapshot, workspace_root="/fake/workspace")
        assert results == []

    def test_top_n_limits_results(self, company_snapshot):
        records = {
            f"fund_{i}": [{
                "institution_id": f"fund_{i}",
                "institution_name": f"基金{i}",
                "high_freq_topics": ["军工"],
                "focus_keywords": [],
                "preferred_stages": [],
                "session_count": 1,
            }]
            for i in range(10)
        }
        with patch("investor_matcher._load_analytics_by_institution", return_value=records):
            results = match_institutions(company_snapshot, workspace_root="/fake", top_n=5)
        assert len(results) <= 5


# ─────────────────────────────────────────────
# 4. 报告格式化测试
# ─────────────────────────────────────────────

class TestFormatMatchReport:
    def test_output_contains_institution_name(self, company_snapshot):
        results = [
            InstitutionMatchResult(
                institution_id="dikce",
                institution_name="迪策资本",
                score=82,
                matched_keywords=["军工电子", "硬科技"],
                stage_match=True,
                session_count=3,
                match_reason="行业标签高度重合（军工电子、硬科技），阶段吻合（B轮）",
            )
        ]
        text = format_match_report(company_snapshot, results)
        assert "迪策资本" in text
        assert "82" in text

    def test_empty_results_shows_no_data(self, company_snapshot):
        text = format_match_report(company_snapshot, [])
        assert "暂无" in text or "0" in text or "未找到" in text
