# 依赖：pip install requests python-dotenv pydantic
# （阿里云兜底走 REST，无需 dashscope；pydantic 供 schema 使用）
"""
真实语音转写模块：硅基流动（主） + 阿里云 DashScope Paraformer（备）。
仓库发版 V7.2（与 build_release.CURRENT_VERSION 对齐）。
严格产出带词级时间戳的 TranscriptionWord 列表，供流水线后续切割使用。
入口 `audio_path` 可由上层在 ASR 前经 audio_preprocess.smart_compress_media 预处理（大文件网关 MP3 等）。
（敏感词替换在转写完成之后由 job_pipeline.mask_words_for_llm 执行，词表经 sensitive_words.parse_sensitive_words 解析并按词长排序后传入。）
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any, List
from urllib import request as urllib_request

import requests
from dotenv import load_dotenv

from retry_policy import run_with_backoff
from schema import TranscriptionWord
from runtime_paths import get_project_root, get_writable_app_root

# ---------------------------------------------------------------------------
# 环境与路径
# ---------------------------------------------------------------------------
load_dotenv(get_writable_app_root() / ".env")

logger = logging.getLogger(__name__)


def _requests_get_with_retry(url: str, **kwargs: Any) -> requests.Response:
    def _do() -> requests.Response:
        r = requests.get(url, **kwargs)
        if r.status_code in (429, 502, 503, 504):
            r.raise_for_status()
        return r

    return run_with_backoff(_do, logger=logger, operation=f"GET {url[:56]}")


def _requests_post_with_retry(url: str, **kwargs: Any) -> requests.Response:
    def _do() -> requests.Response:
        r = requests.post(url, **kwargs)
        if r.status_code in (429, 502, 503, 504):
            r.raise_for_status()
        return r

    return run_with_backoff(_do, logger=logger, operation=f"POST {url[:56]}")


SILICONFLOW_TRANSCRIBE_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
SILICONFLOW_MODEL = "FunAudioLLM/SenseVoiceSmall"

# 上传凭证必须与后续调用的转写模型一致（百炼要求）
ALIYUN_ASR_MODEL = "paraformer-v2"
ALIYUN_UPLOAD_POLICY_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"
# 录音文件识别异步接口（须配合 X-DashScope-Async）；oss:// 临时 URL 须加 X-DashScope-OssResourceResolve
DASHSCOPE_TRANSCRIPTION_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
DASHSCOPE_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"


def _guess_audio_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime and mime.startswith("audio/"):
        return mime
    ext = Path(path).suffix.lower()
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
    }.get(ext, "application/octet-stream")


def _collect_verbose_words(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从 OpenAI/SiliconFlow 兼容的 verbose_json 中收集词级条目（多路径尝试）。"""
    for key in ("words", "word_segments", "word_list"):
        words = payload.get(key)
        if isinstance(words, list) and words:
            return words

    segments = payload.get("segments")
    if isinstance(segments, list):
        merged: list[dict[str, Any]] = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            for wkey in ("words", "tokens", "word_list"):
                w = seg.get(wkey)
                if isinstance(w, list) and w:
                    merged.extend(w)
                    break
        if merged:
            return merged

    # 少数实现把结果包在 data / result 下
    for nest_key in ("data", "result", "output"):
        inner = payload.get(nest_key)
        if isinstance(inner, dict):
            nested = _collect_verbose_words(inner)
            if nested:
                return nested
    return []


def _coerce_seconds_pair(w: dict[str, Any]) -> tuple[float, float] | None:
    """
    从单条词记录中解析起止时间（统一为秒）。
    兼容 OpenAI 风格 start/end（秒）及部分接口的 start_time/end_time、毫秒 begin_time/end_time。
    """
    if not isinstance(w, dict):
        return None

    s = w.get("start")
    e = w.get("end")
    if s is not None and e is not None:
        try:
            return float(s), float(e)
        except (TypeError, ValueError):
            pass

    s = w.get("start_time")
    e = w.get("end_time")
    if s is not None and e is not None:
        try:
            fs, fe = float(s), float(e)
            # 毫秒启发式：大于 300 且明显像毫秒
            if fs > 300 or fe > 300:
                return fs / 1000.0, fe / 1000.0
            return fs, fe
        except (TypeError, ValueError):
            pass

    s = w.get("begin_time")
    e = w.get("end_time")
    if s is not None and e is not None:
        try:
            fs, fe = float(s), float(e)
            if fs > 300 or fe > 300:
                return fs / 1000.0, fe / 1000.0
            return fs, fe
        except (TypeError, ValueError):
            pass

    return None


def _siliconflow_word_has_times(w: dict[str, Any]) -> bool:
    return _coerce_seconds_pair(w) is not None


def _map_siliconflow_to_schema(raw_words: list[dict[str, Any]]) -> List[TranscriptionWord]:
    out: List[TranscriptionWord] = []
    for w in raw_words:
        pair = _coerce_seconds_pair(w)
        if pair is None:
            continue
        t0, t1 = pair
        text = str(w.get("word") or w.get("text") or w.get("token") or "").strip()
        out.append(
            TranscriptionWord(
                word_index=len(out),
                text=text or "(空)",
                start_time=t0,
                end_time=t1,
                speaker_id="未知",
            )
        )
    return out


def transcribe_siliconflow(file_path: str) -> List[TranscriptionWord]:
    """
    引擎 1：硅基流动 OpenAI 兼容 /v1/audio/transcriptions。
    要求 verbose_json + 词级时间戳；否则抛出 ValueError 以触发上层降级。
    """
    api_key = os.getenv("SILICONFLOW_API_KEY")
    if not api_key:
        raise ValueError("未设置环境变量 SILICONFLOW_API_KEY")

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"音频文件不存在: {file_path}")

    headers = {"Authorization": f"Bearer {api_key}"}
    mime = _guess_audio_mime(str(path))

    # multipart：与 OpenAI 一致，使用 timestamp_granularities[]=word
    with open(path, "rb") as audio_fp:
        files = [
            ("file", (path.name, audio_fp, mime)),
            ("model", (None, SILICONFLOW_MODEL)),
            ("response_format", (None, "verbose_json")),
            ("timestamp_granularities[]", (None, "word")),
        ]
        resp = _requests_post_with_retry(
            SILICONFLOW_TRANSCRIBE_URL,
            headers=headers,
            files=files,
            timeout=600,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"硅基流动 HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise RuntimeError(f"硅基流动返回非 JSON: {resp.text[:300]}") from e

    raw_words = _collect_verbose_words(data)
    if not raw_words:
        logger.warning(
            "硅基流动响应无词级列表（顶层键: %s）。"
            "多数情况下为该模型/网关尚未返回与 OpenAI 一致的 verbose_json.words，属平台能力限制。",
            list(data.keys())[:20],
        )
        raise ValueError("硅基流动未返回词级时间戳，触发降级")

    bad = [w for w in raw_words if not isinstance(w, dict) or not _siliconflow_word_has_times(w)]
    if bad:
        logger.warning("硅基流动词条中有 %d 条缺少可解析的起止时间", len(bad))
        raise ValueError("硅基流动未返回词级时间戳，触发降级")

    mapped = _map_siliconflow_to_schema(raw_words)
    if not mapped:
        raise ValueError("硅基流动未返回词级时间戳，触发降级")
    return mapped


def _dashscope_get_upload_policy(api_key: str, model_name: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    params = {"action": "getPolicy", "model": model_name}
    r = _requests_get_with_retry(
        ALIYUN_UPLOAD_POLICY_URL, headers=headers, params=params, timeout=60
    )
    if r.status_code != 200:
        raise RuntimeError(f"获取 DashScope 上传凭证失败 HTTP {r.status_code}: {r.text[:500]}")
    body = r.json()
    if "data" not in body:
        raise RuntimeError(f"上传凭证响应异常: {body}")
    return body["data"]


def _dashscope_upload_file(policy_data: dict[str, Any], file_path: str) -> str:
    """上传本地文件到百炼临时 OSS，返回 oss:// 形式的 URL（供转写任务引用）。"""
    path = Path(file_path)
    key = f"{policy_data['upload_dir']}/{path.name}"
    with open(path, "rb") as f:
        form_files = {
            "OSSAccessKeyId": (None, policy_data["oss_access_key_id"]),
            "Signature": (None, policy_data["signature"]),
            "policy": (None, policy_data["policy"]),
            "x-oss-object-acl": (None, policy_data["x_oss_object_acl"]),
            "x-oss-forbid-overwrite": (None, policy_data["x_oss_forbid_overwrite"]),
            "key": (None, key),
            "success_action_status": (None, "200"),
            "file": (path.name, f),
        }
        up = _requests_post_with_retry(
            policy_data["upload_host"], files=form_files, timeout=600
        )
    if up.status_code != 200:
        raise RuntimeError(f"上传音频到 DashScope 临时存储失败 HTTP {up.status_code}: {up.text[:500]}")
    return f"oss://{key}"


def _fetch_json_from_url(url: str) -> dict[str, Any]:
    raw = urllib_request.urlopen(url, timeout=120).read().decode("utf-8")
    return json.loads(raw)


def _map_aliyun_paraformer_to_schema(result: dict[str, Any]) -> List[TranscriptionWord]:
    """
    解析 Paraformer 录音文件识别结果 JSON（transcription_url 下载内容）。
    词时间单位为毫秒 -> 秒；按 transcripts/sentences/words 顺序展平。
    """
    transcripts = result.get("transcripts")
    if not isinstance(transcripts, list):
        raise ValueError("阿里云识别结果缺少 transcripts")

    out: List[TranscriptionWord] = []
    idx = 0
    for tr in transcripts:
        if not isinstance(tr, dict):
            continue
        sentences = tr.get("sentences") or []
        if not isinstance(sentences, list):
            continue
        for sent in sentences:
            if not isinstance(sent, dict):
                continue
            words = sent.get("words") or []
            if not isinstance(words, list):
                continue
            for w in words:
                if not isinstance(w, dict):
                    continue
                bt = w.get("begin_time")
                et = w.get("end_time")
                if bt is None or et is None:
                    continue
                text = str(w.get("text") or "").strip()
                out.append(
                    TranscriptionWord(
                        word_index=idx,
                        text=text or "(空)",
                        start_time=float(bt) / 1000.0,
                        end_time=float(et) / 1000.0,
                        speaker_id="未知",
                    )
                )
                idx += 1

    if not out:
        raise ValueError("阿里云识别结果中未找到带 begin_time/end_time 的词级数组")

    return out


def _dashscope_submit_transcription_rest(api_key: str, oss_url: str) -> str:
    """
    通过 REST 提交异步转写任务。
    使用 oss:// 临时 URL 时必须在 Header 中开启 X-DashScope-OssResourceResolve，
    否则服务端无法拉取文件，子任务会报 FILE_DOWNLOAD_FAILED（与 Python SDK 默认行为一致）。
    文档：https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-restful-api
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
        "X-DashScope-OssResourceResolve": "enable",
    }
    body = {
        "model": ALIYUN_ASR_MODEL,
        "input": {"file_urls": [oss_url]},
        "parameters": {
            "channel_id": [0],
            "language_hints": ["zh", "en"],
        },
    }
    r = _requests_post_with_retry(
        DASHSCOPE_TRANSCRIPTION_URL,
        headers=headers,
        data=json.dumps(body),
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"阿里云提交转写任务失败 HTTP {r.status_code}: {r.text[:800]}")
    payload = r.json()
    out = payload.get("output") or {}
    task_id = out.get("task_id")
    if not task_id:
        raise RuntimeError(f"阿里云提交转写未返回 task_id: {payload}")
    return str(task_id)


def _dashscope_poll_task_rest(api_key: str, task_id: str) -> list[Any]:
    """轮询任务直到 SUCCEEDED / FAILED / 超时。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    url = DASHSCOPE_TASK_URL.format(task_id=task_id)
    deadline = time.time() + 3600
    poll_interval = 2.0

    while time.time() < deadline:
        resp = _requests_post_with_retry(url, headers=headers, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"阿里云查询任务 HTTP {resp.status_code}: {resp.text[:800]}")
        body = resp.json()
        out = body.get("output") or {}
        status = out.get("task_status")
        if status == "SUCCEEDED":
            results = out.get("results")
            if not results:
                raise RuntimeError(f"阿里云任务成功但无 results: {body}")
            return results
        if status == "FAILED":
            raise RuntimeError(f"阿里云转写任务失败: {body}")
        if status not in ("PENDING", "RUNNING", None):
            raise RuntimeError(f"阿里云转写未知任务状态 {status!r}: {body}")
        time.sleep(poll_interval)

    raise TimeoutError("阿里云转写等待超时（>3600s）")


def transcribe_aliyun(file_path: str) -> List[TranscriptionWord]:
    """
    引擎 2：百炼 Paraformer-v2 录音文件识别（纯 REST，不用 dashscope SDK）。
    本地文件 -> 临时 OSS (oss://) -> REST 提交（带 OssResourceResolve）-> 轮询 -> 下载 transcription_url JSON。
    """
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("未设置环境变量 DASHSCOPE_API_KEY")

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"音频文件不存在: {file_path}")

    policy = _dashscope_get_upload_policy(api_key, ALIYUN_ASR_MODEL)
    oss_url = _dashscope_upload_file(policy, file_path)
    logger.info("DashScope 临时文件已上传: %s", oss_url)

    task_id = _dashscope_submit_transcription_rest(api_key, oss_url)
    logger.info("阿里云转写任务已提交 task_id=%s", task_id)

    results = _dashscope_poll_task_rest(api_key, task_id)
    first = results[0]
    if isinstance(first, dict):
        sub = first.get("subtask_status")
        turl = first.get("transcription_url")
        err_code = first.get("code")
        err_msg = first.get("message")
    else:
        sub = getattr(first, "subtask_status", None)
        turl = getattr(first, "transcription_url", None)
        err_code = getattr(first, "code", None)
        err_msg = getattr(first, "message", None)

    if sub != "SUCCEEDED":
        raise RuntimeError(
            f"阿里云子任务未成功: subtask_status={sub!r}, code={err_code!r}, message={err_msg!r}, raw={first!r}"
        )

    if not turl:
        raise RuntimeError("阿里云结果缺少 transcription_url")

    result_json = _fetch_json_from_url(turl)
    return _map_aliyun_paraformer_to_schema(result_json)


def transcribe_audio(
    audio_path: str | Path,
    *,
    out_json_path: str | Path | None = None,
) -> List[TranscriptionWord]:
    """
    双引擎调度：优先硅基流动；任意异常则打印警告并切换阿里云。
    返回词级转写列表；若提供 out_json_path 则额外写入 JSON（便于调试或归档）。
    """
    path_str = str(Path(audio_path).resolve())
    try:
        words = transcribe_siliconflow(path_str)
    except Exception as e:
        logger.warning("硅基流动转写未成功，切换阿里云兜底: %s", e, exc_info=False)
        print(
            f"[transcriber] WARN: 硅基流动失败，已切换阿里云兜底。原因: {e}",
            file=sys.stderr,
        )
        words = transcribe_aliyun(path_str)

    if out_json_path is not None:
        out = Path(out_json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                [w.model_dump() for w in words],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("转写 JSON 已写入: %s", out)

    return words


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="语音转写 CLI")
    parser.add_argument(
        "--audio",
        type=Path,
        default=get_project_root() / "tests" / "real_pitch.m4a",
        help="输入音频路径",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=get_writable_app_root() / "output" / "real_transcription.json",
        help="输出词级 JSON 路径（与 --no-save 互斥）",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="仅打印词数，不写 JSON",
    )
    args = parser.parse_args()

    if not args.audio.is_file():
        raise SystemExit(f"缺少音频文件: {args.audio}")

    out_arg = None if args.no_save else args.out_json
    words = transcribe_audio(args.audio, out_json_path=out_arg)
    print(f"转写完成，共 {len(words)} 条词级记录")
    if out_arg:
        print(f"已写入: {out_arg}")
