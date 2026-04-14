"""
会前演练模式测试 — V10.3 P2.1
运行：pytest tests/test_practice_engine.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import practice_engine as pe


def _make_mock_profile() -> dict:
    """最小化机构画像 mock。"""
    return {
        "institution_id": "inst-001",
        "canonical_name": "迪策资本",
        "total_sessions": 5,
        "avg_score": 72.0,
        "top_risk_types": [
            {"risk_type": "估值回避", "count": 4, "ratio": 0.4},
            {"risk_type": "数据含糊", "count": 3, "ratio": 0.3},
        ],
        "killer_questions": [
            "你们的 ARR 增长率是多少，为什么比行业平均低？",
            "你的估值依据是什么？",
        ],
        "sessions": [],
    }


# ── 角色模板（P3）──────────────────────────────────────────────────────────────

def test_get_practice_role_templates_contains_ceo_cfo_cto():
    templates = pe.get_practice_role_templates()
    assert "CEO（战略增长）" in templates
    assert "CFO（财务审慎）" in templates
    assert "CTO（技术深挖）" in templates


def test_build_role_opening_hint_contains_role_focus_and_custom_hint():
    hint = pe.build_role_opening_hint(
        "CFO（财务审慎）",
        custom_hint="重点追问现金流和回款周期",
    )
    assert "财务" in hint or "现金流" in hint
    assert "重点追问现金流和回款周期" in hint


def test_build_role_opening_hint_unknown_role_falls_back_to_custom():
    hint = pe.build_role_opening_hint(
        "未知角色",
        custom_hint="只问客户留存",
    )
    assert "只问客户留存" in hint


# ── 会话初始化 ────────────────────────────────────────────────────────────────

def test_start_session_returns_dict_with_required_keys(tmp_path):
    """start_practice_session 应返回含 question/session_id/rounds 的 dict。"""
    with patch("practice_engine.build_institution_profile", return_value=_make_mock_profile()):
        with patch("practice_engine._call_llm_question", return_value="你们的商业模式是什么？"):
            result = pe.start_practice_session("inst-001", "company_001", tmp_path)
    assert "opening_question" in result
    assert "rounds" in result
    assert result["rounds"] == []
    assert "institution_profile" in result
    assert result["institution_id"] == "inst-001"
    assert result["company_id"] == "company_001"


def test_start_session_opening_question_not_empty(tmp_path):
    """开场问题不应为空。"""
    with patch("practice_engine.build_institution_profile", return_value=_make_mock_profile()):
        with patch("practice_engine._call_llm_question", return_value="请介绍你们的核心产品。"):
            result = pe.start_practice_session("inst-001", "company_001", tmp_path)
    assert result["opening_question"].strip() != ""


def test_start_session_fallback_when_no_llm(tmp_path):
    """LLM 不可用时，开场问题应降级为机构历史致命问题之一，不崩溃。"""
    with patch("practice_engine.build_institution_profile", return_value=_make_mock_profile()):
        with patch("practice_engine._call_llm_question", side_effect=Exception("no api key")):
            result = pe.start_practice_session("inst-001", "company_001", tmp_path)
    assert result["opening_question"].strip() != ""  # 降级而非崩溃


# ── 答题评分 ─────────────────────────────────────────────────────────────────

def test_evaluate_answer_returns_score_and_feedback(tmp_path):
    """evaluate_answer_and_next 应返回 score / feedback / next_question。"""
    session = {
        "institution_id": "inst-001",
        "company_id": "company_001",
        "opening_question": "请介绍商业模式",
        "rounds": [],
        "institution_profile": _make_mock_profile(),
    }
    mock_eval = {"score": 75, "feedback": "回答较清晰，但缺少数据支撑。"}
    with patch("practice_engine._call_llm_evaluate", return_value=mock_eval):
        with patch("practice_engine._call_llm_question", return_value="好的，那你们的竞争优势是？"):
            result = pe.evaluate_answer_and_next(
                session,
                question="请介绍商业模式",
                answer="我们通过 SaaS 订阅实现收入，月 ARR 增长 15%。",
            )
    assert "score" in result
    assert 0 <= result["score"] <= 100
    assert "feedback" in result
    assert "next_question" in result
    assert "updated_session" in result


def test_evaluate_answer_appends_to_rounds(tmp_path):
    """每次评分后，updated_session.rounds 应增加一条记录。"""
    session = {
        "institution_id": "inst-001",
        "company_id": "company_001",
        "opening_question": "介绍产品",
        "rounds": [],
        "institution_profile": _make_mock_profile(),
    }
    mock_eval = {"score": 80, "feedback": "不错"}
    with patch("practice_engine._call_llm_evaluate", return_value=mock_eval):
        with patch("practice_engine._call_llm_question", return_value="下一个问题"):
            result = pe.evaluate_answer_and_next(session, question="介绍产品", answer="我们的产品是…")
    assert len(result["updated_session"]["rounds"]) == 1
    round0 = result["updated_session"]["rounds"][0]
    assert round0["question"] == "介绍产品"
    assert round0["answer"] == "我们的产品是…"
    assert round0["score"] == 80


def test_evaluate_answer_fallback_when_llm_fails(tmp_path):
    """LLM 评分失败时，返回默认分数（50）和提示信息，不崩溃。"""
    session = {
        "institution_id": "inst-001",
        "company_id": "company_001",
        "opening_question": "介绍产品",
        "rounds": [],
        "institution_profile": _make_mock_profile(),
    }
    with patch("practice_engine._call_llm_evaluate", side_effect=Exception("timeout")):
        with patch("practice_engine._call_llm_question", side_effect=Exception("timeout")):
            result = pe.evaluate_answer_and_next(session, question="介绍产品", answer="产品介绍")
    assert result["score"] == 50  # 降级默认分
    assert result["feedback"] != ""


# ── 会话总结 ─────────────────────────────────────────────────────────────────

def test_get_session_summary_empty_rounds():
    """无 rounds 时，总结应返回零值，不崩溃。"""
    session = {
        "rounds": [],
        "institution_profile": _make_mock_profile(),
    }
    summary = pe.get_session_summary(session)
    assert summary["total_rounds"] == 0
    assert summary["avg_score"] == 0.0


def test_get_session_summary_calculates_avg():
    """avg_score 应等于各 round score 的平均值。"""
    session = {
        "rounds": [
            {"question": "Q1", "answer": "A1", "score": 80, "feedback": ""},
            {"question": "Q2", "answer": "A2", "score": 60, "feedback": ""},
        ],
        "institution_profile": _make_mock_profile(),
    }
    summary = pe.get_session_summary(session)
    assert summary["total_rounds"] == 2
    assert abs(summary["avg_score"] - 70.0) < 0.01


def test_get_session_summary_weak_areas():
    """low-score rounds 应归入 weak_areas。"""
    session = {
        "rounds": [
            {"question": "估值问题", "answer": "...", "score": 45, "feedback": ""},
            {"question": "增长问题", "answer": "...", "score": 90, "feedback": ""},
        ],
        "institution_profile": _make_mock_profile(),
    }
    summary = pe.get_session_summary(session)
    # score < 60 的问题应标记为弱项
    assert any("估值" in w or w for w in summary["weak_areas"])


# ── LLM 调用层（隔离测试） ────────────────────────────────────────────────────

def test_call_llm_question_returns_string():
    """_call_llm_question mock 路径正确。"""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "你们的 LTV/CAC 是多少？"
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("practice_engine.OpenAI", return_value=mock_client):
        with patch("os.getenv", return_value="fake-key"):
            q = pe._call_llm_question(
                institution_profile=_make_mock_profile(),
                conversation_history=[],
                system_hint="test",
            )
    assert q.strip() != ""


def test_call_llm_evaluate_returns_dict():
    """_call_llm_evaluate mock 路径正确，返回 score + feedback。"""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = json.dumps({
        "score": 78,
        "feedback": "数据支撑充分，逻辑清晰。"
    }, ensure_ascii=False)
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("practice_engine.OpenAI", return_value=mock_client):
        with patch("os.getenv", return_value="fake-key"):
            result = pe._call_llm_evaluate(
                question="你们的估值依据是什么？",
                answer="我们采用 DCF + 行业对标法",
                institution_profile=_make_mock_profile(),
            )
    assert result["score"] == 78
    assert "feedback" in result
