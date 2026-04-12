"""
多语言检测模块 — V10.3 P3.3

基于字符统计的轻量语言检测：
- 无外部依赖，纯 Python 标准库
- 采样前 N 个词以避免超长文本带来的性能问题
- 默认语言为中文（zh），符合本系统主用场景

支持语言：
  'zh' — 中文（默认）
  'en' — 英文

检测原理：
  统计文本中 CJK 字符（U+4E00–U+9FFF 等区间）和 ASCII 字母字符的比例。
  若 ASCII 字母比例 ≥ ENGLISH_THRESHOLD 且 CJK 比例 < CJK_THRESHOLD → 'en'
  其余情况 → 'zh'
"""
from __future__ import annotations

from typing import Any

# CJK 统一汉字基本区 + 扩展区 A/B 等覆盖范围
_CJK_RANGES = (
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # Extension A
    (0x20000, 0x2A6DF), # Extension B
    (0x2A700, 0x2B73F), # Extension C
    (0x2B740, 0x2B81F), # Extension D
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x2F800, 0x2FA1F), # CJK Compatibility Supplement
)

# 判定为英文的 ASCII 字母占所有"有意义字符"的比例阈值
_ENGLISH_THRESHOLD = 0.60

# 如果 CJK 比例超过此值，强制判定为中文（即使有很多 ASCII）
_CJK_OVERRIDE = 0.15

# 采样词数上限（避免超大文本的性能开销）
_SAMPLE_WORDS = 200


def _is_cjk(ch: str) -> bool:
    """判断单个字符是否属于 CJK 汉字范围。"""
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def detect_language_from_text(text: str) -> str:
    """
    从纯文本字符串检测语言。

    参数：
      text : 待检测文本

    返回：
      'zh' | 'en'
    """
    if not text or not text.strip():
        return "zh"

    cjk_count = 0
    ascii_letter_count = 0
    meaningful_count = 0  # CJK 字符 + ASCII 字母

    for ch in text:
        if _is_cjk(ch):
            cjk_count += 1
            meaningful_count += 1
        elif ch.isascii() and ch.isalpha():
            ascii_letter_count += 1
            meaningful_count += 1

    if meaningful_count == 0:
        return "zh"

    cjk_ratio = cjk_count / meaningful_count
    ascii_ratio = ascii_letter_count / meaningful_count

    # CJK 字符占主导 → 中文
    if cjk_ratio >= _CJK_OVERRIDE:
        return "zh"

    # ASCII 字母占主导 → 英文
    if ascii_ratio >= _ENGLISH_THRESHOLD:
        return "en"

    return "zh"


def detect_language_from_words(words: list[Any]) -> str:
    """
    从 TranscriptionWord 对象列表（或兼容 dict）检测语言。

    采样前 _SAMPLE_WORDS 个词以提高性能。

    参数：
      words : List of TranscriptionWord or dict with 'text' field

    返回：
      'zh' | 'en'
    """
    if not words:
        return "zh"

    sample = words[:_SAMPLE_WORDS]
    combined_parts: list[str] = []

    for w in sample:
        if isinstance(w, dict):
            combined_parts.append(w.get("text", ""))
        else:
            # TranscriptionWord dataclass / object
            combined_parts.append(getattr(w, "text", ""))

    combined = " ".join(combined_parts)
    return detect_language_from_text(combined)


def get_language_prompt_hint(lang: str) -> str:
    """
    根据检测到的语言返回注入 LLM system prompt 的语言指令字符串。

    参数：
      lang : 语言代码 ('zh' | 'en' | ...)

    返回：
      str — 中文时返回空字符串（系统默认），英文时返回英文指令提示
    """
    if lang == "en":
        return (
            "\n\n[LANGUAGE INSTRUCTION] The pitch interview transcript is in English. "
            "You MUST respond entirely in English. "
            "All risk point descriptions, feedback, and analysis should be written in English. "
            "Do not switch to Chinese in your response."
        )
    # 'zh' 或其他未知语言 → 系统默认中文，无需额外提示
    return ""
