"""
V6.2 智能音频网关：ASR 前对大文件做视频抽轨 + 语音极限压缩（可选）。
发版主线与根目录 build_release.py → CURRENT_VERSION 对齐。
失败时回退为原始字节，由调用方落盘与转写。
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 小于此字节数跳过压缩（10MB）
_MIN_COMPRESS_BYTES = 10 * 1024 * 1024


def _subprocess_stealth_kwargs() -> dict:
    kw: dict = {}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kw["startupinfo"] = si
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


@dataclass(frozen=True)
class CompressResult:
    """did_compress=True 表示已成功走 FFmpeg 网关；used_fallback=True 表示尝试压缩但已回退原稿。"""

    data: bytes
    did_compress: bool
    used_fallback: bool


def smart_compress_media(data: bytes, *, filename_hint: str = "audio.bin") -> CompressResult:
    """
    探针：<10MB 直通；否则 FFmpeg 抽视频轨、16kHz 单声道 MP3 16kbps，-threads 1。
    任意失败返回原字节流，used_fallback=True（仅在大文件分支上为 True）。
    """
    orig_len = len(data)
    if orig_len < _MIN_COMPRESS_BYTES:
        return CompressResult(data, did_compress=False, used_fallback=False)

    try:
        import imageio_ffmpeg
    except ImportError as e:
        logger.warning("audio_preprocess: 缺少 imageio-ffmpeg，跳过压缩：%s", e)
        return CompressResult(data, did_compress=False, used_fallback=True)

    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        logger.warning("audio_preprocess: get_ffmpeg_exe 失败：%s", e)
        return CompressResult(data, did_compress=False, used_fallback=True)

    if not exe or not Path(exe).is_file():
        return CompressResult(data, did_compress=False, used_fallback=True)

    suf = Path(filename_hint).suffix.lower()
    if not suf or len(suf) > 10:
        suf = ".mp4"
    tmp_in: Path | None = None
    tmp_out: Path | None = None
    try:
        fd_in, str_in = tempfile.mkstemp(suffix=suf)
        os.close(fd_in)
        tmp_in = Path(str_in)
        tmp_in.write_bytes(data)

        fd_out, str_out = tempfile.mkstemp(suffix=".mp3")
        os.close(fd_out)
        tmp_out = Path(str_out)

        cmd = [
            exe,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-threads",
            "1",
            "-i",
            str(tmp_in.resolve()),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "16k",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(tmp_out.resolve()),
        ]
        r = subprocess.run(
            cmd,
            capture_output=True,
            timeout=3600,
            check=False,
            **_subprocess_stealth_kwargs(),
        )
        if r.returncode != 0:
            err = (r.stderr or b"")[:600].decode("utf-8", errors="replace")
            logger.warning("audio_preprocess: ffmpeg rc=%s %s", r.returncode, err)
            return CompressResult(data, did_compress=False, used_fallback=True)
        if not tmp_out.is_file():
            return CompressResult(data, did_compress=False, used_fallback=True)
        out = tmp_out.read_bytes()
        if len(out) < 32:
            logger.warning("audio_preprocess: 输出过短，回退原文件")
            return CompressResult(data, did_compress=False, used_fallback=True)
        return CompressResult(out, did_compress=True, used_fallback=False)
    except subprocess.TimeoutExpired:
        logger.warning("audio_preprocess: ffmpeg 超时，回退原文件")
        return CompressResult(data, did_compress=False, used_fallback=True)
    except Exception as e:
        logger.warning("audio_preprocess: 异常回退：%s", e)
        return CompressResult(data, did_compress=False, used_fallback=True)
    finally:
        for p in (tmp_in, tmp_out):
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
