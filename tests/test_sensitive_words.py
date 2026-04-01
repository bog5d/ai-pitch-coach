"""sensitive_words.parse_sensitive_words 单元测试。仓库发版 V7.5（与 build_release.CURRENT_VERSION 对齐）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sensitive_words import parse_sensitive_words  # noqa: E402


class TestParseSensitiveWords(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(parse_sensitive_words(""), [])
        self.assertEqual(parse_sensitive_words("   \n\t  "), [])

    def test_mixed_separators_dedupe_order(self) -> None:
        raw = "福创投, 迪策；净利润\n华为  华为"
        self.assertEqual(
            parse_sensitive_words(raw),
            ["福创投", "迪策", "净利润", "华为"],
        )

    def test_chinese_comma_semicolon(self) -> None:
        self.assertEqual(
            parse_sensitive_words("甲，乙；丙"),
            ["甲", "乙", "丙"],
        )


if __name__ == "__main__":
    unittest.main()
