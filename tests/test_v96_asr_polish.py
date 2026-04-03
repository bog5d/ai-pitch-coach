"""
V9.6 ASR 润色：词级时间戳映射与 polish_transcription_text（Mock LLM）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from asr_polish import apply_asr_polish_payload_to_words, polish_transcription_text  # noqa: E402
from schema import TranscriptionWord  # noqa: E402


def _w(i: int, text: str) -> TranscriptionWord:
    return TranscriptionWord(
        word_index=i,
        text=text,
        start_time=float(i),
        end_time=float(i) + 0.3,
        speaker_id="S1",
    )


def test_apply_payload_preserves_timestamps_and_indices():
    words = [_w(0, "華为"), _w(2, "雲")]
    payload = {
        "words": [
            {"word_index": 0, "text": "华为"},
            {"word_index": 2, "text": "云"},
        ],
    }
    out = apply_asr_polish_payload_to_words(words, payload)
    assert len(out) == 2
    assert out[0].text == "华为"
    assert out[0].start_time == 0.0
    assert out[0].end_time == 0.3
    assert out[0].word_index == 0
    assert out[1].word_index == 2
    assert out[1].text == "云"


def test_apply_payload_mismatch_returns_original_texts():
    words = [_w(0, "a"), _w(1, "b")]
    payload = {"words": [{"word_index": 0, "text": "A"}]}  # 缺 1
    out = apply_asr_polish_payload_to_words(words, payload)
    assert [x.text for x in out] == ["a", "b"]


def test_polish_transcription_text_mock_llm_updates_text_only():
    words = [_w(0, "错自"), _w(1, "test")]
    payload = {
        "words": [
            {"word_index": 0, "text": "错字"},
            {"word_index": 1, "text": "test"},
        ],
    }

    def _mock_resp(content: str):
        ch = MagicMock()
        ch.message.content = content
        r = MagicMock()
        r.choices = [ch]
        return r

    with patch("llm_judge._make_client") as mk:
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_resp(
            json.dumps(payload, ensure_ascii=False)
        )
        mk.return_value = (client, "deepseek-chat")
        with patch("retry_policy.run_with_backoff", side_effect=lambda fn, **kw: fn()):
            out = polish_transcription_text(
                words,
                company_background="某硬科技公司",
                industry_hot_words=["火控"],
            )

    assert out[0].text == "错字"
    assert out[0].start_time == words[0].start_time
    client.chat.completions.create.assert_called_once()


def test_polish_empty_returns_empty():
    assert polish_transcription_text([]) == []


# ── 极端边界：Task1 硬化测试 ─────────────────────────────────────────────────

def test_apply_payload_llm_merges_words_degrades():
    """LLM 把3个词合并成1个词（词数减少），必须降级返回原列表。"""
    words = [_w(0, "你"), _w(1, "好"), _w(2, "啊")]
    # LLM 只返回 1 项，把 3 词合并了
    payload = {"words": [{"word_index": 0, "text": "你好啊"}]}
    out = apply_asr_polish_payload_to_words(words, payload)
    # 降级：text 保持原样，时间戳不变
    assert [x.text for x in out] == ["你", "好", "啊"]
    assert out[0].start_time == 0.0
    assert out[2].start_time == 2.0


def test_apply_payload_llm_deletes_word_degrades():
    """LLM 删除了语气词（词数减少），必须降级返回原列表。"""
    words = [_w(0, "嗯"), _w(1, "我们"), _w(2, "看")]
    # LLM 删掉了语气词 idx=0
    payload = {"words": [
        {"word_index": 1, "text": "我们"},
        {"word_index": 2, "text": "看"},
    ]}
    out = apply_asr_polish_payload_to_words(words, payload)
    assert [x.text for x in out] == ["嗯", "我们", "看"]
    assert out[0].word_index == 0


def test_apply_payload_duplicate_word_index_degrades():
    """LLM 返回了重复 word_index（如 idx=0 出现两次），必须降级而非静默覆盖。"""
    words = [_w(0, "测"), _w(1, "试")]
    # 故意让 idx=0 重复出现
    payload = {"words": [
        {"word_index": 0, "text": "A"},
        {"word_index": 0, "text": "B"},  # 重复！
        {"word_index": 1, "text": "试"},
    ]}
    out = apply_asr_polish_payload_to_words(words, payload)
    # 必须降级，原文不变
    assert [x.text for x in out] == ["测", "试"]


def test_apply_payload_extra_word_degrades():
    """LLM 多返回了一个 word_index，词数超出原始，必须降级。"""
    words = [_w(0, "a"), _w(1, "b")]
    payload = {"words": [
        {"word_index": 0, "text": "A"},
        {"word_index": 1, "text": "B"},
        {"word_index": 2, "text": "C"},  # 多余！原词无 idx=2
    ]}
    out = apply_asr_polish_payload_to_words(words, payload)
    assert [x.text for x in out] == ["a", "b"]


def test_apply_payload_noncontiguous_indices_preserved():
    """词索引不连续（ASR 跳号）时，时间戳必须按 word_index 精确对齐，不能按位置。"""
    # 模拟 ASR 跳号：索引 0, 5, 10（中间有空缺）
    w0 = TranscriptionWord(word_index=0, text="开始", start_time=0.0, end_time=0.3, speaker_id="S1")
    w5 = TranscriptionWord(word_index=5, text="中间", start_time=2.5, end_time=2.8, speaker_id="S1")
    w10 = TranscriptionWord(word_index=10, text="结束", start_time=5.0, end_time=5.3, speaker_id="S1")
    words = [w0, w5, w10]
    payload = {"words": [
        {"word_index": 0, "text": "开始了"},
        {"word_index": 5, "text": "中间段"},
        {"word_index": 10, "text": "结束了"},
    ]}
    out = apply_asr_polish_payload_to_words(words, payload)
    assert out[0].text == "开始了" and out[0].start_time == 0.0 and out[0].word_index == 0
    assert out[1].text == "中间段" and out[1].start_time == 2.5 and out[1].word_index == 5
    assert out[2].text == "结束了" and out[2].start_time == 5.0 and out[2].word_index == 10
