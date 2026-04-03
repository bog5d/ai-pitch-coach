"""
V9.6：ASR 口述实录轻量润色（DeepSeek）。
在词级锚点不变的前提下仅修正各词的 text，保留 word_index / start_time / end_time / speaker_id。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from openai import APIError

from schema import TranscriptionWord

logger = logging.getLogger(__name__)


def apply_asr_polish_payload_to_words(
    words: list[TranscriptionWord],
    payload: dict[str, Any],
) -> list[TranscriptionWord]:
    """
    将 LLM 返回的 JSON 合并回词列表。
    要求：根对象含 \"words\" 数组，每项含 word_index 与 text；索引集合与输入完全一致，否则降级返回原列表。
    """
    if not words:
        return words
    raw_items = payload.get("words")
    if not isinstance(raw_items, list):
        return list(words)
    by_idx: dict[int, str] = {}
    seen_indices: set[int] = set()
    for it in raw_items:
        if not isinstance(it, dict):
            return list(words)
        try:
            idx = int(it.get("word_index"))
        except (TypeError, ValueError):
            return list(words)
        t = it.get("text")
        if t is None or not isinstance(t, str):
            return list(words)
        if idx in seen_indices:
            logger.warning(
                "ASR 润色 JSON 含重复 word_index=%d，已跳过润色（可能是合并词幻觉）",
                idx,
            )
            return list(words)
        seen_indices.add(idx)
        by_idx[idx] = t
    expected = {w.word_index for w in words}
    if set(by_idx.keys()) != expected or len(by_idx) != len(expected):
        logger.warning(
            "ASR 润色 JSON 与词索引集合不一致，已跳过润色（expected=%d got=%d）",
            len(expected),
            len(by_idx),
        )
        return list(words)
    return [w.model_copy(update={"text": by_idx[w.word_index]}) for w in words]


def polish_transcription_text(
    words: list[TranscriptionWord],
    *,
    company_background: str = "",
    industry_hot_words: list[str] | None = None,
    on_notice: Callable[[str], None] | None = None,
) -> list[TranscriptionWord]:
    """
    调用 DeepSeek 对逐词文本做错别字与标点轻量修正。
    不改变词边界与条数：仅替换每个 TranscriptionWord.text，时间与索引原样保留。
    失败或契约不符时返回输入副本（不抛异常中断流水线）。
    """
    if not words:
        return []

    from llm_judge import MAX_COMPLETION_TOKENS_BY_MODEL, _make_client
    from retry_policy import run_with_backoff

    hot = industry_hot_words or []
    hot_line = "、".join(str(h).strip() for h in hot if str(h).strip()) or "（无）"
    bg = (company_background or "").strip()
    bg_block = f"<COMPANY_BACKGROUND>\n{bg}\n</COMPANY_BACKGROUND>\n" if bg else ""

    lines = [f"{w.word_index}\t{w.text}" for w in words]
    user_body = "\n".join(lines)

    schema_hint = json.dumps(
        {
            "type": "object",
            "required": ["words"],
            "properties": {
                "words": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["word_index", "text"],
                        "properties": {
                            "word_index": {"type": "integer"},
                            "text": {"type": "string"},
                        },
                    },
                }
            },
        },
        ensure_ascii=False,
    )

    system_prompt = f"""你是中文口述实录编辑。输入为多行「词索引\\t词文本」，每行对应 ASR 的一个不可再分的词级锚点。
{bg_block}<INDUSTRY_HOT_WORDS>
{hot_line}
</INDUSTRY_HOT_WORDS>
<TASK>
1. 仅修正错别字、明显笔误与同音误识，并在**单个词文本允许范围内**补全或调整标点（不得把多个词合并成一个词）。
2. **严禁**增删词行：输出 words 数组长度必须等于输入行数，且 word_index 集合与输入完全一致。
3. **严禁**改动说话人、时间轴：不要输出 start_time/end_time；系统只读取你返回的 word_index 与 text。
4. 行业热词优先按专业写法修正。
</TASK>
仅输出一个 JSON 对象，形状如下：
{schema_hint}"""

    user_prompt = (
        "以下每行格式为 词索引\\t原始文本。请输出润色后的 JSON：\n\n" + user_body
    )

    client, model_name = _make_client("deepseek")
    max_tokens = MAX_COMPLETION_TOKENS_BY_MODEL.get(model_name, 8192)

    def _chat_once():
        return client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=max_tokens,
        )

    try:
        response = run_with_backoff(
            _chat_once,
            logger=logger,
            operation="polish_transcription_text (deepseek)",
        )
    except (APIError, RuntimeError, ValueError) as e:
        logger.warning("ASR 润色 LLM 调用失败，已跳过：%s", e)
        return list(words)

    choice = response.choices[0] if response.choices else None
    if choice is None or not choice.message or choice.message.content is None:
        logger.warning("ASR 润色返回空内容，已跳过")
        return list(words)

    raw = choice.message.content.strip()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("根须为对象")
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("ASR 润色 JSON 无法解析，已跳过：%s", e)
        return list(words)

    out = apply_asr_polish_payload_to_words(words, data)
    changed = any(o.text != w.text for o, w in zip(out, words))
    if changed:
        msg = "✅ 已对 ASR 实录做轻量润色（词级时间戳未改动）。"
        if callable(on_notice):
            try:
                on_notice(msg)
            except Exception:
                logger.exception("on_notice 回调失败")
    return out
