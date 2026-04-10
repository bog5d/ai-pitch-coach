# 依赖：pip install requests python-dotenv pydantic
# （阿里云兜底走 REST，无需 dashscope；pydantic 供 schema 使用）
"""
真实语音转写模块：硅基流动（主） + 阿里云 DashScope Paraformer（备）。
仓库发版 V7.5（与 build_release.CURRENT_VERSION 对齐）。
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
from typing import Any, List, Optional
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


def _speaker_id_from_vendor_word(
    w: dict[str, Any], sentence: dict[str, Any] | None = None
) -> str | None:
    """优先用词级字段，其次句级；返回 None 表示厂商未提供说话人。"""
    for src in (w, sentence):
        if not isinstance(src, dict):
            continue
        for key in (
            "speaker_id",
            "speaker",
            "spk_id",
            "spk",
            "channel_id",
            "speaker_label",
        ):
            if key not in src:
                continue
            val = src.get(key)
            if val is None:
                continue
            s = str(val).strip()
            if s:
                return s
    return None


def _assign_auto_speaker_ids(raw_speaker_labels: list[str | None]) -> list[str]:
    """
    将 None 替换为 auto_spk_0, auto_spk_1, …（按「首次出现的占位」递增，同一段未知共用一个 id）。
    """
    out: list[str] = []
    next_auto = 0
    anon_slot: str | None = None
    for lab in raw_speaker_labels:
        if lab:
            anon_slot = None
            out.append(lab)
            continue
        if anon_slot is None:
            anon_slot = f"auto_spk_{next_auto}"
            next_auto += 1
        out.append(anon_slot)
    return out


def _build_siliconflow_segment_punct_map(payload: dict[str, Any]) -> dict[int, str]:
    """
    P2 修复：从 SiliconFlow verbose_json 的 segment 级别提取末尾标点，
    构建 {有效词全局索引 → 句末标点字符} 的映射表。

    逻辑：对每个 segment，若 segment.text 以句末标点结尾，则将该标点映射到
    该 segment 内最后一个「有效词」（有合法时间戳的词）的全局索引。

    返回空 dict 表示无需追加（payload 无 segments 或均无标点），调用方安全消费。
    架构师红线：仅用于在 _map_siliconflow_to_schema 中追加 text，不影响时间戳。
    """
    result: dict[int, str] = {}
    segments = payload.get("segments")
    if not isinstance(segments, list):
        return result

    global_valid_idx = 0  # 全局有效词计数（跨 segment 累计）
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_text = str(seg.get("text") or "").rstrip()
        terminal = (
            seg_text[-1]
            if seg_text and seg_text[-1] in _SENTENCE_PUNCT_CHARS
            else ""
        )
        # 收集本 segment 内的有效词（有合法时间戳）
        words_in_seg: list[dict[str, Any]] = []
        for wkey in ("words", "tokens", "word_list"):
            raw_w = seg.get(wkey)
            if isinstance(raw_w, list) and raw_w:
                words_in_seg = raw_w
                break
        n_valid = sum(
            1 for w in words_in_seg
            if isinstance(w, dict) and _siliconflow_word_has_times(w)
        )
        if n_valid > 0:
            if terminal:
                result[global_valid_idx + n_valid - 1] = terminal
            global_valid_idx += n_valid

    return result


def _map_siliconflow_to_schema(
    raw_words: list[dict[str, Any]],
    *,
    punct_map: dict[int, str] | None = None,
) -> List[TranscriptionWord]:
    """
    将 SiliconFlow 词级列表映射为 TranscriptionWord 列表。

    P2 修复：接受可选 punct_map（来自 _build_siliconflow_segment_punct_map）。
    若某有效词索引在 punct_map 中，则在其 text 末尾追加对应标点符号。
    架构师红线：仅修改 text，start_time / end_time / word_index 严禁变动。
    """
    raw_labels: list[str | None] = []
    for w in raw_words:
        pair = _coerce_seconds_pair(w)
        if pair is None:
            continue
        raw_labels.append(_speaker_id_from_vendor_word(w, None))

    speaker_ids = _assign_auto_speaker_ids(raw_labels)
    out: List[TranscriptionWord] = []
    si = 0  # 有效词计数（与 punct_map 的 key 对齐）
    for w in raw_words:
        pair = _coerce_seconds_pair(w)
        if pair is None:
            continue
        t0, t1 = pair
        text = str(w.get("word") or w.get("text") or w.get("token") or "").strip()
        # P2 修复：段末词追加句末标点（仅 text 变化，时间戳不变）
        if punct_map and si in punct_map and text:
            text = text + punct_map[si]
        sid = speaker_ids[si]
        si += 1
        out.append(
            TranscriptionWord(
                word_index=len(out),
                text=text or "(空)",
                start_time=t0,
                end_time=t1,
                speaker_id=sid,
            )
        )
    return out


def transcribe_siliconflow(
    file_path: str,
    *,
    hot_words: list[str] | None = None,
) -> List[TranscriptionWord]:
    """
    引擎 1：硅基流动 OpenAI 兼容 /v1/audio/transcriptions。
    要求 verbose_json + 词级时间戳；否则抛出 ValueError 以触发上层降级。
    hot_words 非空时注入 initial_prompt 字段作为专有名词提示（最优努力，API 不支持时无副作用）。
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
        if hot_words:
            prompt_str = "，".join(str(w).strip() for w in hot_words if str(w).strip())
            if prompt_str:
                files.append(("initial_prompt", (None, prompt_str)))
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

    # P2 修复：从 segment 级提取末尾标点，传入映射函数追加到段末词 text
    punct_map = _build_siliconflow_segment_punct_map(data)
    mapped = _map_siliconflow_to_schema(raw_words, punct_map=punct_map or None)
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


_SENTENCE_PUNCT_CHARS = frozenset("。？！，…")


def _map_aliyun_paraformer_to_schema(result: dict[str, Any]) -> List[TranscriptionWord]:
    """
    解析 Paraformer 录音文件识别结果 JSON（transcription_url 下载内容）。
    词时间单位为毫秒 -> 秒；按 transcripts/sentences/words 顺序展平。

    P2 修复：将 sentence.text 的末尾标点追加到该句最后一个有效词的 text 字段，
    使词级 text 携带标点信息（供 format_transcript_plain_by_speaker 拆句换行）。
    架构师红线：仅修改 text，start_time / end_time / word_index 严禁变动。
    """
    transcripts = result.get("transcripts")
    if not isinstance(transcripts, list):
        raise ValueError("阿里云识别结果缺少 transcripts")

    raw_rows: list[tuple[float, float, str, str | None]] = []
    for tr in transcripts:
        if not isinstance(tr, dict):
            continue
        sentences = tr.get("sentences") or []
        if not isinstance(sentences, list):
            continue
        for sent in sentences:
            if not isinstance(sent, dict):
                continue
            # 预先收集本句有效词（有 begin_time/end_time），以确定"末词"位置
            sent_word_list = sent.get("words") or []
            if not isinstance(sent_word_list, list):
                continue
            valid_words = [
                w for w in sent_word_list
                if isinstance(w, dict)
                and w.get("begin_time") is not None
                and w.get("end_time") is not None
            ]
            if not valid_words:
                continue
            # 提取句末标点（来自 sentence 级别的 text 字段，包含 ASR 预测标点）
            sent_text = str(sent.get("text") or "").rstrip()
            sent_terminal_punct = (
                sent_text[-1]
                if sent_text and sent_text[-1] in _SENTENCE_PUNCT_CHARS
                else ""
            )
            last_valid_idx = len(valid_words) - 1
            for local_idx, w in enumerate(valid_words):
                bt = w.get("begin_time")
                et = w.get("end_time")
                text = str(w.get("text") or "").strip()
                spk = _speaker_id_from_vendor_word(w, sent)
                # 仅末词追加句末标点，其余词不变（保持时间戳对齐语义）
                if local_idx == last_valid_idx and sent_terminal_punct and text:
                    text = text + sent_terminal_punct
                raw_rows.append(
                    (float(bt) / 1000.0, float(et) / 1000.0, text or "(空)", spk)
                )

    speaker_ids = _assign_auto_speaker_ids([r[3] for r in raw_rows])
    out: List[TranscriptionWord] = []
    for i, (t0, t1, text, _) in enumerate(raw_rows):
        out.append(
            TranscriptionWord(
                word_index=i,
                text=text,
                start_time=t0,
                end_time=t1,
                speaker_id=speaker_ids[i],
            )
        )

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
            "enable_punctuation_prediction": True,
            "disfluency_removal_enabled": True,
            "diarization_enabled": True,  # P2 修复：开启说话人分离，返回词级 spk_id
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
        resp = _requests_get_with_retry(url, headers=headers, timeout=120)
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


def transcribe_aliyun(
    file_path: str,
    *,
    hot_words: list[str] | None = None,
) -> List[TranscriptionWord]:
    """
    引擎 2：百炼 Paraformer-v2 录音文件识别（纯 REST，不用 dashscope SDK）。
    本地文件 -> 临时 OSS (oss://) -> REST 提交（带 OssResourceResolve）-> 轮询 -> 下载 transcription_url JSON。
    hot_words 当前在阿里云引擎侧暂不支持直接注入（需预创建 vocabulary），静默忽略。
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


def _human_speaker_label_zh(ordinal_zero_based: int) -> str:
    """按首次出现顺序编号：发言人 1、发言人 2…（与 LLM 用 [0][1] 词索引区分）。"""
    return f"发言人 {ordinal_zero_based + 1}"


def format_transcript_plain_by_speaker(words: List[TranscriptionWord]) -> str:
    """
    人类可读视图：按 speaker_id 聚类，段格式为 ``[发言人 1]: ...``。
    - 不同说话人之间空一行（\\n\\n）
    - 同一说话人内，遇到句末标点（。？！…）自动换行（\\n），实现段内拆句
    - 绝不输出词级 ``[0]`` 索引

    P2 修复：利用词 text 末尾的句末标点（由 _map_*_to_schema 注入）触发段内换行，
    使一大坨面条字变为自然分句的可读段落。
    对齐免疫：仅修改展示格式，TranscriptionWord 列表本身不变。
    """
    if not words:
        return ""
    ordered: list[str] = []
    for w in words:
        sid = (w.speaker_id or "").strip() or "auto_spk_0"
        if sid not in ordered:
            ordered.append(sid)
    labels = {sid: _human_speaker_label_zh(i) for i, sid in enumerate(ordered)}

    lines: list[str] = []           # 最终说话人块列表（\n\n 连接）
    cur: str | None = None
    buf: list[str] = []             # 当前句的词缓冲
    sent_lines: list[str] = []      # 当前说话人块内已完成的句子列表

    def _flush_speaker_block() -> None:
        """将当前 speaker 的缓冲输出为一个完整的说话人块。"""
        nonlocal buf, sent_lines
        if buf:
            sent_lines.append("".join(buf))
            buf = []
        if sent_lines and cur is not None:
            label = labels.get(cur, cur)
            block_text = "\n".join(sent_lines)
            lines.append(f"[{label}]: {block_text}")
            sent_lines = []

    for w in words:
        t = (w.text or "").strip()
        if not t or t == "(空)":
            continue
        sid = (w.speaker_id or "").strip() or "auto_spk_0"
        if cur is not None and sid != cur:
            _flush_speaker_block()
        cur = sid
        buf.append(t)
        # P2 修复：词末句终标点 → 封闭当前句，追加到 sent_lines，重置 buf
        if t[-1] in _SENTENCE_PUNCT_CHARS:
            sent_lines.append("".join(buf))
            buf = []

    if cur is not None:
        _flush_speaker_block()

    return "\n\n".join(lines)


def transcribe_audio(
    audio_path: str | Path,
    *,
    out_json_path: str | Path | None = None,
    hot_words: list[str] | None = None,
) -> List[TranscriptionWord]:
    """
    双引擎调度：优先硅基流动；任意异常则打印警告并切换阿里云。
    返回词级转写列表；若提供 out_json_path 则额外写入 JSON（便于调试或归档）。
    hot_words 非空时向 SiliconFlow 注入 initial_prompt 作为专有名词提示。
    """
    path_str = str(Path(audio_path).resolve())
    try:
        words = transcribe_siliconflow(path_str, hot_words=hot_words)
    except Exception as e:
        logger.warning("硅基流动转写未成功，切换阿里云兜底: %s", e, exc_info=False)
        print(
            f"[transcriber] WARN: 硅基流动失败，已切换阿里云兜底。原因: {e}",
            file=sys.stderr,
        )
        words = transcribe_aliyun(path_str, hot_words=hot_words)

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
