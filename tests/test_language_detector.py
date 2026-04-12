"""
多语言检测模块测试 — V10.3 P3.3
运行：pytest tests/test_language_detector.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import language_detector as ld


# ── detect_language_from_text ─────────────────────────────────────────────────

def test_chinese_text_detected_as_zh():
    """纯中文文本 → 'zh'。"""
    text = "我们公司主要做人工智能领域的创业，目前已经完成了天使轮融资。"
    assert ld.detect_language_from_text(text) == "zh"


def test_english_text_detected_as_en():
    """纯英文文本 → 'en'。"""
    text = "Our company focuses on artificial intelligence and we have completed our seed round."
    assert ld.detect_language_from_text(text) == "en"


def test_mixed_mostly_chinese_is_zh():
    """中英混杂但中文占主导 → 'zh'。"""
    text = "我们的 AI 产品已经完成了 MVP，有 5 个付费客户。"
    assert ld.detect_language_from_text(text) == "zh"


def test_empty_text_returns_zh():
    """空文本默认返回 'zh'（系统默认语言）。"""
    assert ld.detect_language_from_text("") == "zh"


def test_whitespace_only_returns_zh():
    """纯空白字符默认返回 'zh'。"""
    assert ld.detect_language_from_text("   \n\t  ") == "zh"


def test_numbers_only_returns_zh():
    """纯数字无法判断语言，默认 'zh'。"""
    assert ld.detect_language_from_text("123 456 789") == "zh"


def test_english_threshold_respected():
    """英文字符比例超过阈值时返回 'en'。"""
    # 全部 ASCII 字母 → 英文
    text = "hello world this is a test sentence for language detection"
    assert ld.detect_language_from_text(text) == "en"


# ── detect_language_from_words ────────────────────────────────────────────────

def _make_word(text: str, idx: int = 0) -> dict:
    return {"word_index": idx, "text": text, "start_time": 0.0, "end_time": 1.0, "speaker_id": "S1"}


def test_detect_from_words_chinese():
    """中文 word 列表 → 'zh'。"""
    words = [_make_word(w, i) for i, w in enumerate("我们公司做的是AI医疗".split())]
    assert ld.detect_language_from_words(words) == "zh"


def test_detect_from_words_english():
    """英文 word 列表 → 'en'。"""
    words = [_make_word(w, i) for i, w in enumerate("our product serves enterprise customers in the US".split())]
    assert ld.detect_language_from_words(words) == "en"


def test_detect_from_words_empty():
    """空 word 列表默认 'zh'。"""
    assert ld.detect_language_from_words([]) == "zh"


def test_detect_from_words_uses_sample():
    """当 word 数量超过采样数量时，仍能正确检测。"""
    # 生成 500 个英文词
    words = [_make_word("hello", i) for i in range(500)]
    assert ld.detect_language_from_words(words) == "en"


# ── get_language_prompt_hint ──────────────────────────────────────────────────

def test_language_hint_zh_returns_empty():
    """中文时不需要额外提示（系统默认中文），返回空字符串。"""
    hint = ld.get_language_prompt_hint("zh")
    assert hint == ""


def test_language_hint_en_returns_nonempty():
    """英文时返回指示 LLM 用英文响应的提示语。"""
    hint = ld.get_language_prompt_hint("en")
    assert len(hint) > 0
    assert "English" in hint or "英文" in hint or "english" in hint.lower()


def test_language_hint_unknown_returns_empty():
    """未知语言代码默认返回空字符串。"""
    hint = ld.get_language_prompt_hint("fr")
    assert hint == ""
