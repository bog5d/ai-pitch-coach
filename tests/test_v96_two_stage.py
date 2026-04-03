"""
V9.6 两阶段评估：evaluate_pitch / deep_evaluate_single_risk（Mock LLM）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm_judge import deep_evaluate_single_risk, evaluate_pitch  # noqa: E402
from schema import (  # noqa: E402
    RiskTargetCandidate,
    TranscriptionWord,
)


def _tw(i: int, text: str) -> TranscriptionWord:
    return TranscriptionWord(
        word_index=i,
        text=text,
        start_time=float(i) * 0.1,
        end_time=float(i) * 0.1 + 0.05,
        speaker_id="spk",
    )


def _resp(content: str) -> MagicMock:
    ch = MagicMock()
    ch.message.content = content
    r = MagicMock()
    r.choices = [ch]
    return r


def test_evaluate_pitch_two_calls_scan_then_deep():
    words = [_tw(0, "你"), _tw(1, "好")]
    scan = {
        "scene_analysis": {"scene_type": "路演", "speaker_roles": "创始人 vs IC"},
        "targets": [
            {
                "start_word_index": 0,
                "end_word_index": 1,
                "problem_description": "开场乏力",
                "risk_type": "表达",
            }
        ],
    }
    rp = {
        "risk_level": "轻微",
        "tier1_general_critique": "T1",
        "tier2_qa_alignment": "T2",
        "improvement_suggestion": "建议这样说：……",
        "original_text": "[0]你 [1]好",
        "start_word_index": 0,
        "end_word_index": 1,
        "score_deduction": 5,
        "deduction_reason": "测试扣分",
        "is_manual_entry": False,
    }

    with patch("llm_judge._make_client") as mk:
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            _resp(json.dumps(scan, ensure_ascii=False)),
            _resp(json.dumps(rp, ensure_ascii=False)),
        ]
        mk.return_value = (client, "deepseek-chat")
        with patch("llm_judge.run_with_backoff", side_effect=lambda fn, **kw: fn()):
            report = evaluate_pitch(words, model_choice="deepseek", qa_text="")

    assert report.scene_analysis.scene_type == "路演"
    assert len(report.risk_points) == 1
    assert report.risk_points[0].improvement_suggestion == "建议这样说：……"
    assert report.total_score == 95
    assert client.chat.completions.create.call_count == 2


def test_evaluate_pitch_no_targets_full_score():
    words = [_tw(0, "x")]
    scan = {
        "scene_analysis": {"scene_type": "短测", "speaker_roles": "—"},
        "targets": [],
    }
    with patch("llm_judge._make_client") as mk:
        client = MagicMock()
        client.chat.completions.create.return_value = _resp(
            json.dumps(scan, ensure_ascii=False)
        )
        mk.return_value = (client, "deepseek-chat")
        with patch("llm_judge.run_with_backoff", side_effect=lambda fn, **kw: fn()):
            report = evaluate_pitch(words, model_choice="deepseek")

    assert report.total_score == 100
    assert report.risk_points == []
    client.chat.completions.create.assert_called_once()


def test_deep_evaluate_single_risk_forces_indices():
    words = [_tw(i, str(i)) for i in range(5)]
    target = RiskTargetCandidate(
        start_word_index=1,
        end_word_index=3,
        problem_description="测",
        risk_type="逻辑",
    )
    rp = {
        "risk_level": "一般",
        "tier1_general_critique": "a",
        "tier2_qa_alignment": "b",
        "improvement_suggestion": "深评建议",
        "original_text": "",
        "start_word_index": 9,
        "end_word_index": 9,
        "score_deduction": 3,
        "deduction_reason": "r",
        "is_manual_entry": False,
    }
    with patch("llm_judge._make_client") as mk:
        client = MagicMock()
        client.chat.completions.create.return_value = _resp(
            json.dumps(rp, ensure_ascii=False)
        )
        mk.return_value = (client, "deepseek-chat")
        with patch("llm_judge.run_with_backoff", side_effect=lambda fn, **kw: fn()):
            out = deep_evaluate_single_risk(words, target, model_choice="deepseek")

    assert out.start_word_index == 1
    assert out.end_word_index == 3
    assert out.improvement_suggestion == "深评建议"


# ── 并发深评行为测试 ─────────────────────────────────────────────────────────

def test_evaluate_pitch_multi_targets_all_collected():
    """多靶点时，所有并发深评结果都必须被收集，顺序与靶点顺序一致。"""
    words = [_tw(i, f"w{i}") for i in range(10)]
    scan = {
        "scene_analysis": {"scene_type": "路演", "speaker_roles": "创始人 vs IC"},
        "targets": [
            {"start_word_index": 0, "end_word_index": 2, "problem_description": "A", "risk_type": "逻辑"},
            {"start_word_index": 3, "end_word_index": 5, "problem_description": "B", "risk_type": "表达"},
            {"start_word_index": 6, "end_word_index": 8, "problem_description": "C", "risk_type": "数据"},
        ],
    }

    def _make_rp(suggestion: str, sw: int, ew: int) -> dict:
        return {
            "risk_level": "轻微",
            "tier1_general_critique": "T1",
            "tier2_qa_alignment": "T2",
            "improvement_suggestion": suggestion,
            "original_text": "",
            "start_word_index": sw,
            "end_word_index": ew,
            "score_deduction": 3,
            "deduction_reason": "test",
            "is_manual_entry": False,
        }

    call_count = {"n": 0}

    def _side_effect(fn, **kw):
        call_count["n"] += 1
        return fn()

    with patch("llm_judge._make_client") as mk:
        client = MagicMock()
        # 阶段一返回 scan，阶段二每个靶点返回一个 rp
        client.chat.completions.create.side_effect = [
            _resp(json.dumps(scan, ensure_ascii=False)),
            _resp(json.dumps(_make_rp("建议A", 0, 2), ensure_ascii=False)),
            _resp(json.dumps(_make_rp("建议B", 3, 5), ensure_ascii=False)),
            _resp(json.dumps(_make_rp("建议C", 6, 8), ensure_ascii=False)),
        ]
        mk.return_value = (client, "deepseek-chat")
        with patch("llm_judge.run_with_backoff", side_effect=_side_effect):
            report = evaluate_pitch(words, model_choice="deepseek")

    assert len(report.risk_points) == 3
    # 结果顺序必须与靶点顺序对应（按 start_word_index 升序）
    suggestions = [rp.improvement_suggestion for rp in report.risk_points]
    assert "建议A" in suggestions
    assert "建议B" in suggestions
    assert "建议C" in suggestions
    # 验证 start_word_index 有序
    indices = [rp.start_word_index for rp in report.risk_points]
    assert indices == sorted(indices)
    # 阶段一 1 次 + 阶段二 3 次 = 4 次 LLM 调用
    assert client.chat.completions.create.call_count == 4


def test_evaluate_pitch_scan_truncated_json_degrades_gracefully():
    """阶段一 JSON 被截断时，能抢救 scene_analysis 并返回空风险点（不崩溃）。"""
    words = [_tw(i, f"w{i}") for i in range(4)]
    # 只有 scene_analysis，targets 被截断缺失
    truncated_scan = '{"scene_analysis": {"scene_type": "路演", "speaker_roles": "创始人 vs IC"}}'

    with patch("llm_judge._make_client") as mk:
        client = MagicMock()
        client.chat.completions.create.return_value = _resp(truncated_scan)
        mk.return_value = (client, "deepseek-chat")
        with patch("llm_judge.run_with_backoff", side_effect=lambda fn, **kw: fn()):
            report = evaluate_pitch(words, model_choice="deepseek")

    assert report.scene_analysis.scene_type == "路演"
    assert report.risk_points == []
    assert report.total_score == 100


def test_evaluate_pitch_single_target_failure_skipped():
    """并发场景下，单个靶点深评异常不影响其他靶点被收集。"""
    words = [_tw(i, f"w{i}") for i in range(6)]
    scan = {
        "scene_analysis": {"scene_type": "路演", "speaker_roles": "—"},
        "targets": [
            {"start_word_index": 0, "end_word_index": 2, "problem_description": "OK", "risk_type": "逻辑"},
            {"start_word_index": 3, "end_word_index": 5, "problem_description": "BAD", "risk_type": "数据"},
        ],
    }
    good_rp = {
        "risk_level": "一般",
        "tier1_general_critique": "ok",
        "tier2_qa_alignment": "ok",
        "improvement_suggestion": "好的建议",
        "original_text": "",
        "start_word_index": 0,
        "end_word_index": 2,
        "score_deduction": 5,
        "deduction_reason": "test",
        "is_manual_entry": False,
    }

    call_results = [
        _resp(json.dumps(scan, ensure_ascii=False)),
        _resp(json.dumps(good_rp, ensure_ascii=False)),
        _resp("INVALID JSON {{{{"),  # 第二个靶点返回垃圾，应被跳过
    ]

    with patch("llm_judge._make_client") as mk:
        client = MagicMock()
        client.chat.completions.create.side_effect = call_results
        mk.return_value = (client, "deepseek-chat")
        with patch("llm_judge.run_with_backoff", side_effect=lambda fn, **kw: fn()):
            report = evaluate_pitch(words, model_choice="deepseek")

    # 坏靶点被跳过，好靶点仍然存在
    assert len(report.risk_points) == 1
    assert report.risk_points[0].improvement_suggestion == "好的建议"
    assert report.total_score == 95
