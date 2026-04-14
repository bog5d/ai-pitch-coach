"""
V8.0 磁盘级 ASR 缓存单元测试。

覆盖：保存/读取、未命中返回 None、不同 hash 独立、自动创建目录、同 hash 覆写。

运行：pytest tests/test_v80_disk_cache.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from disk_asr_cache import load_asr_cache, save_asr_cache  # noqa: E402


# ──────────────────────────────────────────────────────────────────
class TestSaveAndLoad:
    def test_save_then_load_returns_same_data(self, tmp_path):
        words = [{"word_index": 0, "text": "测试", "start_time": 0.0, "end_time": 0.5, "speaker_id": "spk_a"}]
        plain = "[发言人 1]: 测试"

        save_asr_cache("abc123", words, plain, cache_dir=tmp_path)
        result = load_asr_cache("abc123", cache_dir=tmp_path)

        assert result is not None
        assert result["words"] == words
        assert result["plain"] == plain

    def test_load_missing_hash_returns_none(self, tmp_path):
        result = load_asr_cache("nonexistent_hash", cache_dir=tmp_path)
        assert result is None

    def test_different_hashes_are_independent(self, tmp_path):
        save_asr_cache("hash1", [{"text": "A"}], "A", cache_dir=tmp_path)
        save_asr_cache("hash2", [{"text": "B"}], "B", cache_dir=tmp_path)

        r1 = load_asr_cache("hash1", cache_dir=tmp_path)
        r2 = load_asr_cache("hash2", cache_dir=tmp_path)

        assert r1 is not None and r1["plain"] == "A"
        assert r2 is not None and r2["plain"] == "B"

    def test_save_creates_nested_dir(self, tmp_path):
        cache_dir = tmp_path / "deep" / "nested" / ".asr_cache"
        save_asr_cache("h1", [], "empty", cache_dir=cache_dir)
        assert cache_dir.is_dir()

    def test_overwrite_same_hash_updates_data(self, tmp_path):
        save_asr_cache("h1", [{"text": "旧数据"}], "旧", cache_dir=tmp_path)
        save_asr_cache("h1", [{"text": "新数据"}], "新", cache_dir=tmp_path)

        result = load_asr_cache("h1", cache_dir=tmp_path)
        assert result is not None
        assert result["plain"] == "新"

    def test_saved_file_is_valid_json(self, tmp_path):
        save_asr_cache("abc", [{"w": 1}], "hello", cache_dir=tmp_path)
        cache_file = tmp_path / "abc.json"
        assert cache_file.is_file()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert "words" in data
        assert "plain" in data

    def test_load_empty_words_list(self, tmp_path):
        save_asr_cache("empty_words", [], "无词稿", cache_dir=tmp_path)
        result = load_asr_cache("empty_words", cache_dir=tmp_path)
        assert result is not None
        assert result["words"] == []

    def test_load_unicode_plain_text(self, tmp_path):
        plain = "[发言人 1]: 机构投资人问了关于 PE 估值倍数的问题\n\n[发言人 2]: 我们的答案是..."
        save_asr_cache("unicode_test", [], plain, cache_dir=tmp_path)
        result = load_asr_cache("unicode_test", cache_dir=tmp_path)
        assert result is not None
        assert result["plain"] == plain


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
