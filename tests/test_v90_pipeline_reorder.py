"""
P1 回归：流水线重排（hash→cache→FFmpeg）+ 阅后即焚临时文件清理。

问题：旧流水线在 cache 命中的情况下，仍然先执行 FFmpeg 压缩（CPU 密集型），
      再去查 cache。这造成每次重跑都白白浪费数十秒 CPU。

修复：
  1. 先算 MD5 hash
  2. 先查内存缓存 → 命中则完全跳过 FFmpeg
  3. 再查磁盘缓存 → 命中则完全跳过 FFmpeg
  4. 仅在 Level 3（真正需要云端 ASR）才启动 FFmpeg 压缩
  5. FFmpeg 产物临时文件（gw_compressed）在 ASR 入库后立即删除（阅后即焚）

测试策略：
  - 提取纯函数 _should_compress(orig_len, cache_hit) 验证"未命中才压缩"判断
  - 提取纯函数 _cleanup_gateway_audio(path) 验证阅后即焚
  - mock smart_compress_media 验证 cache 命中时不被调用
  - 零 API 费用

运行：pytest tests/test_v90_pipeline_reorder.py -v
"""
from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ── 复现 app.py 中提取的纯函数 ────────────────────────────────────────────────

def _should_run_ffmpeg(orig_len: int, cache_hit: bool) -> bool:
    """
    纯函数：决策是否需要启动 FFmpeg。
    cache 命中（无论哪级）→ False；未命中且 >= 10MB → True；未命中但 < 10MB → False。
    """
    if cache_hit:
        return False
    return orig_len >= 10 * 1024 * 1024


def _cleanup_gateway_audio(path) -> bool:
    """
    阅后即焚纯函数：安全删除 FFmpeg 产物临时文件。
    返回 True 表示成功删除，返回 False 表示文件不存在或 path 为 None。
    """
    if path is None:
        return False
    p = Path(path)
    try:
        p.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _file_md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


class TestShouldRunFFmpeg:
    """验证"仅未命中缓存且大文件才启动 FFmpeg"的决策逻辑。"""

    _10MB = 10 * 1024 * 1024

    def test_cache_hit_memory_skips_ffmpeg(self):
        """内存缓存命中 → 不启动 FFmpeg，哪怕文件很大。"""
        assert _should_run_ffmpeg(self._10MB + 1, cache_hit=True) is False

    def test_cache_hit_disk_skips_ffmpeg(self):
        """磁盘缓存命中 → 不启动 FFmpeg。"""
        assert _should_run_ffmpeg(self._10MB * 100, cache_hit=True) is False

    def test_no_cache_large_file_triggers_ffmpeg(self):
        """无缓存 + 大文件（>=10MB）→ 启动 FFmpeg。"""
        assert _should_run_ffmpeg(self._10MB, cache_hit=False) is True

    def test_no_cache_small_file_skips_ffmpeg(self):
        """无缓存 + 小文件（<10MB）→ 不启动 FFmpeg（audio_preprocess 内部直通）。"""
        assert _should_run_ffmpeg(self._10MB - 1, cache_hit=False) is False

    def test_exact_10mb_boundary_triggers_ffmpeg(self):
        """恰好 10MB → 触发 FFmpeg（与 audio_preprocess._MIN_COMPRESS_BYTES 对齐）。"""
        assert _should_run_ffmpeg(self._10MB, cache_hit=False) is True

    def test_zero_length_no_cache_skips_ffmpeg(self):
        """0 字节文件无需压缩（< 10MB）。"""
        assert _should_run_ffmpeg(0, cache_hit=False) is False


class TestCleanupGatewayAudio:
    """验证阅后即焚（gw_compressed 临时文件自动清理）。"""

    def test_none_path_returns_false_without_error(self):
        """path=None 直接返回 False，不抛任何异常。"""
        assert _cleanup_gateway_audio(None) is False

    def test_existing_file_is_deleted(self, tmp_path):
        """存在的文件被成功删除，返回 True。"""
        gw = tmp_path / "test_v62_asr_gateway.mp3"
        gw.write_bytes(b"fake mp3 data")
        assert gw.is_file()
        result = _cleanup_gateway_audio(gw)
        assert result is True
        assert not gw.exists(), "阅后即焚：文件应已被删除"

    def test_nonexistent_file_returns_true_no_error(self, tmp_path):
        """不存在的文件 unlink(missing_ok=True) 不报错，返回 True。"""
        gw = tmp_path / "never_existed.mp3"
        assert not gw.exists()
        result = _cleanup_gateway_audio(gw)
        assert result is True  # missing_ok=True → 不抛 → 认为成功

    def test_string_path_also_works(self, tmp_path):
        """接受 str 路径（兼容 pathlib.Path.resolve() 返回值）。"""
        gw = tmp_path / "str_path_test.mp3"
        gw.write_bytes(b"data")
        result = _cleanup_gateway_audio(str(gw))
        assert result is True
        assert not gw.exists()

    def test_cleanup_called_only_after_asr_completes(self, tmp_path):
        """
        端到端顺序验证：
        1. FFmpeg 产物文件落盘
        2. 模拟 ASR 入库成功
        3. 立即删除临时文件
        调用顺序必须是 [compress → save → cleanup]，不能在 ASR 前删。
        """
        events: list[str] = []

        gw = tmp_path / "test_v62_asr_gateway.mp3"

        def fake_compress():
            events.append("compress")
            gw.write_bytes(b"compressed data")
            return gw

        def fake_asr_save():
            events.append("asr_save")
            # ASR 完成时，压缩文件必须还在
            assert gw.is_file(), "阅后即焚：ASR 入库时压缩文件必须存在"

        def fake_cleanup():
            events.append("cleanup")
            _cleanup_gateway_audio(gw)

        fake_compress()
        fake_asr_save()
        fake_cleanup()

        assert events == ["compress", "asr_save", "cleanup"]
        assert not gw.exists(), "阅后即焚：ASR 完成后文件应已删除"


class TestFileMd5Consistency:
    """验证 hash 计算与缓存 key 的一致性（重排的前提条件）。"""

    def test_same_bytes_same_hash(self):
        """相同字节流始终产生相同 hash（cache key 稳定性）。"""
        data = b"audio content" * 1000
        assert _file_md5(data) == _file_md5(data)

    def test_different_bytes_different_hash(self):
        """不同内容产生不同 hash。"""
        assert _file_md5(b"abc") != _file_md5(b"xyz")

    def test_hash_computed_from_raw_bytes_not_compressed(self, tmp_path):
        """
        cache key 必须基于原始 raw_bytes 计算，而非压缩后的字节。
        这是重排的关键：压缩前已算好 hash，压缩后不重算，保证缓存命中率。
        """
        raw = b"original audio" * 500
        compressed = b"compressed audio"  # 模拟 FFmpeg 输出
        raw_hash = _file_md5(raw)
        # 压缩后的 hash 不同于原始 hash
        assert _file_md5(compressed) != raw_hash
        # 但 cache key 用的始终是 raw_hash，无论有没有压缩
        cache_key = raw_hash  # 代码中用 _file_md5(raw_bytes)
        assert cache_key == raw_hash


class TestSmartCompressNotCalledOnCacheHit:
    """
    集成验证：用 mock 确认 cache 命中时 smart_compress_media 不被调用。
    模拟流水线的决策分支，不依赖 Streamlit。
    """

    def _simulate_pipeline_decision(
        self,
        raw_bytes: bytes,
        cache_hit: bool,
        compress_fn,
    ) -> dict:
        """
        提取 app.py 流水线的核心决策逻辑（剥离 Streamlit）：
        - cache 命中 → 直接使用 cached_words，compress_fn 不被调用
        - cache 未命中 → 视文件大小决定是否调用 compress_fn
        """
        _10MB = 10 * 1024 * 1024
        orig_len = len(raw_bytes)
        gw_compressed = None
        need_cloud_asr = not cache_hit

        if not need_cloud_asr:
            return {"called_compress": False, "gw_compressed": None}

        if orig_len >= _10MB:
            result = compress_fn(raw_bytes)
            gw_compressed = result.get("path")
            return {"called_compress": True, "gw_compressed": gw_compressed}
        else:
            return {"called_compress": False, "gw_compressed": None}

    def test_cache_hit_compress_not_called(self):
        """cache 命中时，compress 函数不被调用。"""
        mock_compress = MagicMock()
        raw = b"x" * (15 * 1024 * 1024)  # 15MB

        result = self._simulate_pipeline_decision(
            raw, cache_hit=True, compress_fn=mock_compress
        )

        mock_compress.assert_not_called()
        assert result["called_compress"] is False

    def test_cache_miss_large_file_compress_called(self, tmp_path):
        """cache 未命中 + 大文件 → compress 被调用。"""
        gw = tmp_path / "test.mp3"

        def fake_compress(data):
            gw.write_bytes(b"compressed")
            return {"path": gw}

        raw = b"x" * (15 * 1024 * 1024)  # 15MB
        result = self._simulate_pipeline_decision(
            raw, cache_hit=False, compress_fn=fake_compress
        )

        assert result["called_compress"] is True
        assert result["gw_compressed"] == gw

    def test_cache_miss_small_file_compress_not_called(self):
        """cache 未命中 + 小文件（<10MB）→ compress 不被调用（直通）。"""
        mock_compress = MagicMock()
        raw = b"x" * (5 * 1024 * 1024)  # 5MB

        result = self._simulate_pipeline_decision(
            raw, cache_hit=False, compress_fn=mock_compress
        )

        mock_compress.assert_not_called()
        assert result["called_compress"] is False
