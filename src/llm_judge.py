# 依赖：pip install openai python-dotenv pydantic
"""
LLM 逻辑打分模块 2.0：三巨头模型路由（DeepSeek / Kimi / Qwen-Max）+ AnalysisReport 契约。
支持显式业务上下文与 QA 知识库注入，结构化防幻觉 Prompt。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, List

from dotenv import load_dotenv
from openai import APIError, OpenAI
from pydantic import ValidationError

from schema import AnalysisReport, TranscriptionWord
from runtime_paths import get_writable_app_root

# ---------------------------------------------------------------------------
load_dotenv(get_writable_app_root() / ".env")

logger = logging.getLogger(__name__)

# 三巨头官方兼容 OpenAI 的路由配置
ROUTER: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "KIMI_API_KEY",
        "model": "moonshot-v1-32k",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "model": "qwen-max",
    },
}

DISPLAY_NAME = {
    "deepseek": "DeepSeek-V3 (deepseek-chat)",
    "kimi": "Kimi (Moonshot moonshot-v1-32k)",
    "qwen": "Qwen-Max (DashScope 兼容模式)",
}


def choose_model_with_timeout(timeout: float = 3) -> str:
    """
    终端 3 秒内可选 k / q 切换模型；超时或未输入则默认 deepseek。
    Windows 下 stdin 无法用 select 可靠做超时，故使用「子线程 readline + 主线程 queue.get 超时」。
    """
    t0 = time.monotonic()
    print(
        "默认使用 DeepSeek-V3 评委。你有 3 秒钟时间输入 k (切换Kimi) 或 q (切换Qwen)，按回车确认。"
        "不输入则默认 DeepSeek...",
        flush=True,
    )

    q: queue.Queue[str] = queue.Queue(maxsize=1)

    def _reader() -> None:
        try:
            line = sys.stdin.readline()
        except Exception:
            q.put("deepseek")
            return
        s = (line or "").strip().lower()
        if s.startswith("k"):
            q.put("kimi")
        elif s.startswith("q"):
            q.put("qwen")
        else:
            q.put("deepseek")

    threading.Thread(target=_reader, daemon=True).start()
    try:
        choice = q.get(timeout=timeout)
        logger.debug("choose_model 耗时 %.2fs", time.monotonic() - t0)
        return choice
    except queue.Empty:
        logger.debug("choose_model 超时，默认 deepseek，耗时 %.2fs", time.monotonic() - t0)
        return "deepseek"


def format_transcript_for_llm(words: List[TranscriptionWord]) -> str:
    """[0]词 [1]词 ..."""
    if not words:
        return ""
    parts: list[str] = []
    for w in words:
        text = (w.text or "").strip()
        parts.append(f"[{w.word_index}]{text}")
    return " ".join(parts)


def _normalize_explicit_context(explicit_context: dict[str, Any] | None) -> dict[str, str]:
    """缺省键时填占位，避免 Prompt 中出现 None。"""
    base = explicit_context or {}
    notes = str(base.get("session_notes") or "").strip()
    rec = str(base.get("recording_label") or "").strip()
    return {
        "biz_type": str(base.get("biz_type") or "未指定"),
        "exact_roles": str(base.get("exact_roles") or "未指定"),
        "project_name": str(base.get("project_name") or "未指定"),
        "interviewee": str(base.get("interviewee") or "未指定"),
        "session_notes": notes if notes else "无",
        "recording_label": rec if rec else "未指定",
    }


def _build_system_prompt(
    schema_str: str,
    explicit_context: dict[str, Any] | None,
    qa_text: str,
) -> str:
    ctx = _normalize_explicit_context(explicit_context)
    kb = (qa_text or "").strip()
    kb_block = kb if kb else "未提供参考QA知识库。"

    return f"""你是一位拥有20年经验的顶级VC合伙人。正在复盘带有词级索引 [index] 的录音逐字稿。
<CONTEXT>
当前业务场景：{ctx["biz_type"]}
双方角色设定公理：{ctx["exact_roles"]}
当前投资机构/项目名称：{ctx["project_name"]}
被访谈对象（标识）：{ctx["interviewee"]}
当前录音文件标识：{ctx["recording_label"]}
本段补充说明（用户备注，可与转写对照）：{ctx["session_notes"]}
</CONTEXT>
<KNOWLEDGE_BASE>
{kb_block}
</KNOWLEDGE_BASE>
<TASK>
1【角色精准绑定】：首先推断哪个 speaker 是投资机构，哪个是被尽调方。找茬时绝不能把板子打错人！
2【毒舌复盘】：找出被尽调方的避重就轻、数据打架。
</TASK>
<CONSTRAINTS>
必须提供两层剖析：
- Tier 1: 商业逻辑致命伤。
- Tier 2: 如果 <KNOWLEDGE_BASE> 为空或未提供有效内部 QA，必须直接回答「未提供内部 QA，基于行业常识推断」，绝对禁止凭空捏造虚假规定！若有知识库，则对比是否违背标准。
必须严格按照 JSON Schema 输出，start/end index 必须精确。
【极度重要】：输出 start_word_index 和 end_word_index 时切忌只圈出错片段的几个词；必须向外扩展索引边界，包含投资人完整提问与创始人完整回答段落，使切割音频能呈现完整交锋语境。
</CONSTRAINTS>
<JSON_SCHEMA>
{schema_str}
</JSON_SCHEMA>
"""


def _make_client(model_key: str) -> tuple[OpenAI, str]:
    if model_key not in ROUTER:
        raise ValueError(f"未知模型键: {model_key}，应为 deepseek / kimi / qwen")
    cfg = ROUTER[model_key]
    api_key = os.getenv(cfg["api_key_env"])
    if not api_key:
        raise ValueError(f"未设置环境变量 {cfg['api_key_env']}")
    client = OpenAI(
        base_url=cfg["base_url"],
        api_key=api_key,
    )
    return client, cfg["model"]


def evaluate_pitch(
    words: List[TranscriptionWord],
    model_choice: str = "deepseek",
    *,
    explicit_context: dict[str, Any] | None = None,
    qa_text: str = "",
) -> AnalysisReport:
    """
    使用三巨头之一对逐字稿做场景洞察 + 双层诊断，返回 AnalysisReport。
    explicit_context 建议包含：biz_type, exact_roles, project_name, interviewee；
    可选 session_notes（本段备注）、recording_label（录音文件名标识）。
    """
    if model_choice not in ROUTER:
        raise ValueError('model_choice 必须是 "deepseek"、"kimi" 或 "qwen"')

    transcript = format_transcript_for_llm(words)
    if not transcript.strip():
        raise ValueError("转写词列表为空，无法评估")

    schema_str = json.dumps(
        AnalysisReport.model_json_schema(),
        ensure_ascii=False,
    )
    system_prompt = _build_system_prompt(schema_str, explicit_context, qa_text)
    user_prompt = (
        "以下是本场沟通转写（每个词前有 [索引]，请仅使用这些索引作为 start_word_index / end_word_index）：\n\n"
        f"{transcript}"
    )

    client, model_name = _make_client(model_choice)

    logger.info(
        "调用 LLM: router_key=%s model=%s 词数=%d",
        model_choice,
        model_name,
        len(words),
    )

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
    except APIError as e:
        logger.exception("LLM API 请求失败")
        raise RuntimeError(f"LLM API 请求失败: {e}") from e
    except Exception as e:
        logger.exception("LLM 调用异常")
        raise RuntimeError(f"LLM 调用异常: {e}") from e

    choice = response.choices[0] if response.choices else None
    if choice is None or not choice.message or choice.message.content is None:
        raise RuntimeError("LLM 返回空内容")

    raw_json = choice.message.content.strip()
    logger.debug("LLM 原始 JSON 长度: %d", len(raw_json))

    try:
        return AnalysisReport.model_validate_json(raw_json)
    except ValidationError as e:
        logger.error("AnalysisReport 校验失败: %s\n原始片段: %s", e, raw_json[:2000])
        raise ValueError(f"模型输出不符合 AnalysisReport 契约: {e}") from e


def load_transcription_words(path: Path) -> List[TranscriptionWord]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"JSON 根节点必须是数组: {path}")
    out: List[TranscriptionWord] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i} 项不是对象")
        out.append(TranscriptionWord.model_validate(item))
    return out


def _save_report(path: Path, report: AnalysisReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("已写入: %s", path)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stdout,
    )

    transcription_path = get_writable_app_root() / "output" / "real_transcription.json"
    if not transcription_path.is_file():
        raise SystemExit(f"未找到转写文件: {transcription_path}")

    try:
        words = load_transcription_words(transcription_path)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as e:
        raise SystemExit(f"加载转写失败: {e}") from e

    logger.info("已加载转写词数: %d", len(words))

    selected = choose_model_with_timeout(3)
    label = DISPLAY_NAME.get(selected, selected)
    print(f"正在召唤 {label} 大脑，阅读分析中...", flush=True)

    cli_ctx = {
        "biz_type": "CLI默认",
        "exact_roles": "未指定",
        "project_name": "未指定",
        "interviewee": "未指定",
        "session_notes": "无",
        "recording_label": "未指定",
    }

    try:
        report = evaluate_pitch(
            words,
            model_choice=selected,
            explicit_context=cli_ctx,
            qa_text="",
        )
    except Exception as e:
        logger.exception("评估失败")
        print(f"失败: {e}", file=sys.stderr, flush=True)
        raise SystemExit(1) from e

    out_path = get_writable_app_root() / "output" / "real_analysis_report.json"
    _save_report(out_path, report)
    print(f"已保存: {out_path}", flush=True)
