"""
多语言检测模块 — V10.3.1 P3.3（审计修复版）

基于字符统计的轻量语言检测：
- 无外部依赖，纯 Python 标准库
- 均匀采样（等间距），避免"前段偏差"问题
- 默认语言为中文（zh），符合本系统主用场景

支持语言：
  'zh' — 中文（默认）
  'en' — 英文

检测原理：
  统计文本中 CJK 字符和 ASCII 字母字符的比例。
  CJK 比例 ≥ _CJK_OVERRIDE(0.35)          → 'zh'（中文主导）
  ASCII 字母比例 ≥ _ENGLISH_THRESHOLD(0.70) → 'en'（英文主导）
  其余情况                                   → 'zh'（保守默认）

阈值说明：
  旧版 _ENGLISH_THRESHOLD=0.60 / _CJK_OVERRIDE=0.15 对混合文本过于激进，
  新版调整为 0.70 / 0.35，在中英混杂融资文档场景下误判率更低。

注意：暂仅支持中英两种语言；日文/韩文等会被判为中文（CJK 字符范围重叠）。
"""
from __future__ import annotations

from typing import Any

# CJK 统一汉字基本区 + 扩展区 A/B/C/D 等覆盖范围
_CJK_RANGES = (
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs（核心汉字区）
    (0x3400, 0x4DBF),    # Extension A
    (0x20000, 0x2A6DF),  # Extension B
    (0x2A700, 0x2B73F),  # Extension C
    (0x2B740, 0x2B81F),  # Extension D
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0x2F800, 0x2FA1F),  # CJK Compatibility Supplement
)

# 判定为英文的 ASCII 字母占所有"有意义字符"的比例阈值
# 调整为 0.70（原 0.60），减少对中英混杂文本的误判
_ENGLISH_THRESHOLD = 0.70

# 如果 CJK 比例超过此值，强制判定为中文
# 调整为 0.35（原 0.15），更准确地处理"英文为主+少量汉字"场景
_CJK_OVERRIDE = 0.35

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

    # CJK 字符达到阈值 → 中文（即使有大量英文字母）
    if cjk_ratio >= _CJK_OVERRIDE:
        return "zh"

    # ASCII 字母达到阈值 → 英文
    if ascii_ratio >= _ENGLISH_THRESHOLD:
        return "en"

    # 其余情况：中文优先（保守默认）
    return "zh"


def detect_language_from_words(words: list[Any]) -> str:
    """
    从 TranscriptionWord 对象列表（或兼容 dict）检测语言。

    采用均匀等间距采样，避免只取前 N 词导致"开头偏差"。
    例：前半英文后半中文的访谈，前段采样会高估英文比例；
    均匀采样则能覆盖全程。

    参数：
      words : List of TranscriptionWord or dict with 'text' field

    返回：
      'zh' | 'en'
    """
    if not words:
        return "zh"

    # 均匀等间距采样
    total = len(words)
    sample_size = min(_SAMPLE_WORDS, total)
    step = max(1, total // sample_size)
    sample = words[::step][:_SAMPLE_WORDS]

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
