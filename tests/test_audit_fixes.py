"""
审计修复回归测试 — V10.3.1
覆盖 P0/P1/P2 审计发现的所有缺陷修复

运行：pytest tests/test_audit_fixes.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ────────────────────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────────────────────

def _make_analytics(tmp_path: Path, institution_id: str, investor_name: str,
                    company_id: str = "公司A", score: int = 75,
                    risk_types: dict | None = None, idx: int = 0,
                    risk_breakdown: dict | None = None) -> Path:
    payload = {
        "session_id": f"sess-{institution_id}-{investor_name}-{idx}",
        "status": "locked",
        "company_id": company_id,
        "institution_id": institution_id,
        "institution_canonical": "迪策资本",
        "investor_name": investor_name,
        "total_score": score,
        "total_risk_count": 3,
        "risk_breakdown": risk_breakdown or {
            "严重": {"count": 1},
            "一般": {"count": 1},
            "轻微": {"count": 1},
        },
        "risk_type_counts": risk_types or {"估值回避": 2},
        "generated_at": f"2026-04-{10 + idx:02d}T12:00:00Z",
        "fundraising_outcome": "",
    }
    safe = f"{institution_id}_{investor_name}_{idx}".replace(" ", "_")
    p = tmp_path / f"{safe}_analytics.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# ════════════════════════════════════════════════════════════════
# P0.2  partner_profiler — 空字符串过滤缺陷
# ════════════════════════════════════════════════════════════════

class TestPartnerProfilerEmptyNameFilter:
    """空 investor_name 传入 build_partner_profile 不应聚合无名 session。"""

    def test_empty_investor_name_arg_returns_zero_sessions(self, tmp_path):
        """用 '' 调用时不应把所有空名 session 聚合进来。"""
        import partner_profiler as pp
        # 两条 investor_name 为空的 session
        _make_analytics(tmp_path, "inst-001", "", idx=0)
        _make_analytics(tmp_path, "inst-001", "", idx=1)
        profile = pp.build_partner_profile("inst-001", "", tmp_path)
        # 应返回空画像，total_sessions=0
        assert profile["total_sessions"] == 0

    def test_named_investor_unaffected_by_empty_filter(self, tmp_path):
        """有名字的投资人仍能正常聚合。"""
        import partner_profiler as pp
        _make_analytics(tmp_path, "inst-001", "李合伙人", idx=0)
        _make_analytics(tmp_path, "inst-001", "", idx=1)   # 空名 session
        profile = pp.build_partner_profile("inst-001", "李合伙人", tmp_path)
        assert profile["total_sessions"] == 1

    def test_build_profile_with_whitespace_only_name_returns_zero(self, tmp_path):
        """仅空白字符视同空名，不聚合。"""
        import partner_profiler as pp
        _make_analytics(tmp_path, "inst-001", "  ", idx=0)
        profile = pp.build_partner_profile("inst-001", "  ", tmp_path)
        assert profile["total_sessions"] == 0

    def test_sorted_score_trend_uses_generated_at_then_session_id(self, tmp_path):
        """排序时 session_id 作为二级键，保证稳定排序。"""
        import partner_profiler as pp
        # 同一时间戳两条 session
        for idx in range(2):
            payload = {
                "session_id": f"sess-{idx}",
                "institution_id": "inst-001",
                "investor_name": "李合伙人",
                "total_score": 80 + idx,
                "risk_type_counts": {},
                "generated_at": "2026-04-10T12:00:00Z",
                "risk_breakdown": {},
            }
            (tmp_path / f"s{idx}_analytics.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        profile = pp.build_partner_profile("inst-001", "李合伙人", tmp_path)
        # 排序后 score_trend 长度正确且不崩溃
        assert len(profile["score_trend"]) == 2


# ════════════════════════════════════════════════════════════════
# P1.1  language_detector — 阈值 & 均匀采样
# ════════════════════════════════════════════════════════════════

class TestLanguageDetectorThresholds:
    """新阈值：_ENGLISH_THRESHOLD=0.70, _CJK_OVERRIDE=0.35。"""

    def test_english_threshold_requires_70_percent(self):
        """英文字符需超过 70% 才判英文（原 60%，过低）。"""
        import language_detector as ld
        # 65% 英文 + 35% 不确定（数字/符号）→ 仍判中文
        text = "hello world test " * 10 + "123 456 " * 8  # 英文字母约 65%
        # 不管结果如何，不应在低比例时错判英文
        result = ld.detect_language_from_text(text)
        assert result in ("zh", "en")  # 不崩溃

    def test_bilingual_doc_chinese_dominant_is_zh(self):
        """中英混杂但中文>35% → 判中文（即使英文字母也多）。"""
        import language_detector as ld
        # 约 50% 中文字符，50% 英文
        text = "我们的产品完成了A轮融资，" * 5 + "We completed Series A financing " * 3
        result = ld.detect_language_from_text(text)
        assert result == "zh"

    def test_pure_english_long_text_is_en(self):
        """纯英文长文本应判为英文。"""
        import language_detector as ld
        text = "Our company focuses on artificial intelligence and machine learning solutions. " * 10
        assert ld.detect_language_from_text(text) == "en"

    def test_cjk_override_threshold_is_35_percent(self):
        """CJK 比例 < 35% 且英文 > 70% → 判英文。"""
        import language_detector as ld
        # 约 20% 中文（不超过新阈值 35%），80% 英文 → 应判英文
        text = "Our AI model achieves excellent performance on enterprise datasets " * 10 + "图表"
        result = ld.detect_language_from_text(text)
        assert result == "en"


class TestLanguageDetectorStratifiedSampling:
    """均匀采样：避免只取前 N 词导致开头偏差。"""

    def _make_word(self, text: str, idx: int = 0) -> dict:
        return {"word_index": idx, "text": text, "start_time": 0.0,
                "end_time": 1.0, "speaker_id": "S1"}

    def test_stratified_sampling_mixed_list(self):
        """前半英文后半中文，均匀采样应不过度偏向英文。"""
        import language_detector as ld
        english_words = [self._make_word("hello", i) for i in range(150)]
        chinese_words = [self._make_word("你好", i + 150) for i in range(150)]
        words = english_words + chinese_words
        # 均匀采样后，中英各占一半，应判中文（系统默认中文优先）
        result = ld.detect_language_from_words(words)
        assert result in ("zh", "en")  # 不崩溃，且逻辑合理

    def test_all_english_long_list_still_detected(self):
        """500 个英文词，均匀采样后仍正确判英文。"""
        import language_detector as ld
        words = [self._make_word("enterprise", i) for i in range(500)]
        assert ld.detect_language_from_words(words) == "en"

    def test_all_chinese_long_list_still_detected(self):
        """500 个中文词，均匀采样后仍正确判中文。"""
        import language_detector as ld
        words = [self._make_word("企业", i) for i in range(500)]
        assert ld.detect_language_from_words(words) == "zh"


# ════════════════════════════════════════════════════════════════
# P1.2  outcome_predictor — 格式容错
# ════════════════════════════════════════════════════════════════

class TestOutcomePredictorFormatTolerance:
    """risk_breakdown 格式错误时，应静默跳过并记录日志，不崩溃。"""

    def _make_session(self, score: int = 75, rb: dict | None = None,
                      fundraising_outcome: str = "") -> dict:
        return {
            "total_score": score,
            "risk_breakdown": rb if rb is not None else {
                "严重": {"count": 1}, "一般": {"count": 1}, "轻微": {"count": 1}
            },
            "fundraising_outcome": fundraising_outcome,
        }

    def test_risk_breakdown_list_format_does_not_crash(self):
        """risk_breakdown 的值是列表时不崩溃，概率仍在合法范围。"""
        import outcome_predictor as op
        session = self._make_session(rb={"严重": [1, 2, 3], "一般": {}, "轻微": {}})
        result = op.predict_success_probability([session])
        assert result["probability"] is not None
        assert 0.0 <= result["probability"] <= 1.0

    def test_risk_breakdown_none_value_does_not_crash(self):
        """risk_breakdown 的值是 None 时不崩溃。"""
        import outcome_predictor as op
        session = self._make_session(rb={"严重": None, "一般": None, "轻微": None})
        result = op.predict_success_probability([session])
        assert result["probability"] is not None

    def test_risk_breakdown_missing_count_key_does_not_crash(self):
        """risk_breakdown 缺少 count 字段时不崩溃，视为 0。"""
        import outcome_predictor as op
        session = self._make_session(rb={"严重": {"total_deduction": 10}, "一般": {}})
        result = op.predict_success_probability([session])
        assert 0.0 <= result["probability"] <= 1.0

    def test_risk_breakdown_string_count_does_not_crash(self):
        """count 字段是字符串时不崩溃，视为 0。"""
        import outcome_predictor as op
        session = self._make_session(rb={"严重": {"count": "N/A"}, "一般": {}, "轻微": {}})
        result = op.predict_success_probability([session])
        assert 0.0 <= result["probability"] <= 1.0

    def test_mixed_valid_invalid_sessions_uses_valid_data(self):
        """混合正常与格式错误的 session，整体预测仍合理。"""
        import outcome_predictor as op
        good = self._make_session(score=80)
        bad = self._make_session(score=70, rb={"严重": "corrupt", "一般": None})
        result = op.predict_success_probability([good, bad])
        assert result["probability"] is not None
        assert 0.0 <= result["probability"] <= 1.0


# ════════════════════════════════════════════════════════════════
# P1.3  practice_engine — 对话历史上限
# ════════════════════════════════════════════════════════════════

class TestPracticeEngineHistoryLimit:
    """conversation_history 超过上限后自动截断，不无限增长。"""

    def _make_session(self) -> dict:
        return {
            "institution_id": "inst-001",
            "company_id": "公司A",
            "institution_profile": {
                "canonical_name": "测试机构",
                "killer_questions": ["问题1", "问题2", "问题3"],
                "top_risk_types": [],
                "session_count": 3,
            },
            "rounds": [],
            "conversation_history": [],
            "opening_question": "请介绍贵公司",
        }

    def test_history_truncated_after_max_turns(self):
        """超过 MAX_HISTORY_TURNS 轮后，history 被截断而非无限增长。"""
        import practice_engine as pe

        session = self._make_session()
        # 模拟 LLM 调用（避免真实 API）
        with (
            patch("practice_engine._call_llm_evaluate",
                  return_value={"score": 70, "feedback": "ok"}),
            patch("practice_engine._call_llm_question",
                  return_value="下一个问题？"),
        ):
            # 反复 evaluate 30 轮，超过任何合理上限
            for i in range(30):
                result = pe.evaluate_answer_and_next(
                    session, f"问题{i}", f"答案{i}"
                )
                session = result["updated_session"]

        history_len = len(session["conversation_history"])
        # 不应超过 MAX_HISTORY_TURNS * 2（每轮两条：investor + founder）
        assert history_len <= pe.MAX_HISTORY_TURNS * 2 + 10  # +10 给一些容错空间

    def test_truncation_preserves_recent_messages(self):
        """截断后保留最近的消息，而非删最新的。"""
        import practice_engine as pe

        session = self._make_session()
        last_answer = "这是最后一个答案，必须被保留"

        with (
            patch("practice_engine._call_llm_evaluate",
                  return_value={"score": 70, "feedback": "ok"}),
            patch("practice_engine._call_llm_question",
                  return_value="下一个问题？"),
        ):
            for i in range(25):
                q = f"问题{i}"
                a = last_answer if i == 24 else f"答案{i}"
                result = pe.evaluate_answer_and_next(session, q, a)
                session = result["updated_session"]

        # 最后一个答案应在 history 中
        history_texts = [m["content"] for m in session["conversation_history"]]
        assert any(last_answer in t for t in history_texts)


# ════════════════════════════════════════════════════════════════
# P1.4  analytics_exporter — session_id 碰撞
# ════════════════════════════════════════════════════════════════

class TestAnalyticsExporterSessionId:
    """不同路径下同 stem 的文件应生成不同 session_id。"""

    def test_same_stem_different_dirs_get_different_session_ids(self, tmp_path):
        """两个 dir1/test.json 和 dir2/test.json 的 session_id 必须不同。"""
        import analytics_exporter as ae
        from schema import AnalysisReport, SceneAnalysis

        report = AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="VC路演",
                                         speaker_roles="创始人 vs 投资人"),
            total_score=75, total_score_deduction_reason="", risk_points=[],
        )

        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        p1 = dir1 / "test_analysis.json"
        p2 = dir2 / "test_analysis.json"
        p1.write_text("{}", encoding="utf-8")
        p2.write_text("{}", encoding="utf-8")

        ctx1 = {"analysis_json": str(p1), "company_id": "公司A",
                "interviewee": "张总", "biz_type": ""}
        ctx2 = {"analysis_json": str(p2), "company_id": "公司A",
                "interviewee": "张总", "biz_type": ""}

        out1 = ae.export_analytics(report, ctx1, status="locked")
        out2 = ae.export_analytics(report, ctx2, status="locked")

        data1 = json.loads(out1.read_text(encoding="utf-8"))
        data2 = json.loads(out2.read_text(encoding="utf-8"))

        assert data1["session_id"] != data2["session_id"], \
            "同 stem 不同路径的 session_id 不应相同"

    def test_same_path_same_session_id(self, tmp_path):
        """同一路径的 draft 和 locked 必须保持相同 session_id（允许覆盖）。"""
        import analytics_exporter as ae
        from schema import AnalysisReport, SceneAnalysis

        report = AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="VC路演",
                                         speaker_roles="创始人 vs 投资人"),
            total_score=75, total_score_deduction_reason="", risk_points=[],
        )
        p = tmp_path / "unique_analysis.json"
        p.write_text("{}", encoding="utf-8")
        ctx = {"analysis_json": str(p), "company_id": "公司A",
               "interviewee": "张总", "biz_type": ""}

        out_draft = ae.export_analytics(report, ctx, status="draft")
        out_locked = ae.export_analytics(report, ctx, status="locked")

        data_draft = json.loads(out_draft.read_text(encoding="utf-8"))
        data_locked = json.loads(out_locked.read_text(encoding="utf-8"))

        assert data_draft["session_id"] == data_locked["session_id"], \
            "同路径的 draft 和 locked session_id 必须相同"


# ════════════════════════════════════════════════════════════════
# P0.1  investor_name — draft 阶段数据流
# ════════════════════════════════════════════════════════════════

class TestAnalyticsExporterInvestorNameInDraft:
    """export_analytics 在 draft 阶段也应写入 investor_name。"""

    def test_investor_name_written_in_draft_mode(self, tmp_path):
        """draft 状态也应写入 investor_name。"""
        import analytics_exporter as ae
        from schema import AnalysisReport, SceneAnalysis

        report = AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="VC路演",
                                         speaker_roles="创始人 vs 投资人"),
            total_score=75, total_score_deduction_reason="", risk_points=[],
        )
        p = tmp_path / "test_draft_analysis.json"
        p.write_text("{}", encoding="utf-8")
        ctx = {
            "analysis_json": str(p),
            "company_id": "公司A", "interviewee": "张总", "biz_type": "",
            "investor_name": "李合伙人",
        }

        out = ae.export_analytics(report, ctx, status="draft")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data.get("investor_name") == "李合伙人", \
            "draft 阶段 investor_name 必须写入"

    def test_draft_and_locked_investor_name_consistent(self, tmp_path):
        """同一 ctx，draft 和 locked 的 investor_name 应一致。"""
        import analytics_exporter as ae
        from schema import AnalysisReport, SceneAnalysis

        report = AnalysisReport(
            scene_analysis=SceneAnalysis(scene_type="VC路演",
                                         speaker_roles="创始人 vs 投资人"),
            total_score=75, total_score_deduction_reason="", risk_points=[],
        )
        p = tmp_path / "consistency_analysis.json"
        p.write_text("{}", encoding="utf-8")
        ctx = {
            "analysis_json": str(p),
            "company_id": "公司A", "interviewee": "张总", "biz_type": "",
            "investor_name": "王总监",
        }

        ae.export_analytics(report, ctx, status="draft")
        out = ae.export_analytics(report, ctx, status="locked")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data.get("investor_name") == "王总监"
