# 依赖：pip install openai python-dotenv pydantic
"""
LLM 逻辑打分模块 2.0：三巨头模型路由（DeepSeek / Kimi / Qwen-Max）+ AnalysisReport 契约。
仓库发版 V7.0（与 build_release.CURRENT_VERSION 对齐；含量化扣分引擎与定向狙击 Prompt）。
V7.0：转写与 QA 分池限长，超长 QA 头尾智能截断 + on_notice / UI 提示。
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
from collections.abc import Callable
from typing import Any, List

from dotenv import load_dotenv
from openai import APIError, OpenAI
from pydantic import ValidationError

from retry_policy import run_with_backoff
from schema import AnalysisReport, TranscriptionWord
from runtime_paths import get_writable_app_root

# ---------------------------------------------------------------------------
load_dotenv(get_writable_app_root() / ".env")

logger = logging.getLogger(__name__)

# V7.0：录音转写与 QA 补充材料字数池物理隔离
MAX_TRANSCRIPT_CHARS = 80_000
MAX_QA_CHARS = 30_000

MIDDLE_OMIT_MARK = "\n...[内容过长，系统已智能省略中间部分]...\n"


def truncate_qa_text(qa: str, max_chars: int = MAX_QA_CHARS) -> tuple[str, bool]:
    """
    超长 QA 掐头去尾，中间用省略标记连接。
    返回 (处理后文本, 是否发生过截断)；结果长度保证不超过 max_chars。
    """
    q = (qa or "").strip()
    if len(q) <= max_chars:
        return q, False
    m = len(MIDDLE_OMIT_MARK)
    if max_chars <= m:
        return q[:max_chars], True
    inner = max_chars - m
    head_n = inner // 2
    tail_n = inner - head_n
    return q[:head_n] + MIDDLE_OMIT_MARK + q[-tail_n:], True

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

    return f"""你是一位拥有15年一线投行经验的「顶级金牌路演教练」。你的唯一服务对象是【被访谈对象/路演发言人】。你正在复盘带有词级索引 [index] 的录音逐字稿，目的是帮助发言人提升话术应对能力。
<CONTEXT>
当前业务场景：{ctx["biz_type"]}
双方角色设定公理：{ctx["exact_roles"]}
当前投资机构/项目名称：{ctx["project_name"]}
被访谈对象（标识）：{ctx["interviewee"]}
当前录音文件标识：{ctx["recording_label"]}
🎯 主理人重点关注/定向核实指令：{ctx["session_notes"]}
</CONTEXT>
<KNOWLEDGE_BASE>
{kb_block}
</KNOWLEDGE_BASE>
<TASK>
1【角色防错乱锚定（极度重要）】：你必须结合逐字稿中每个 `[index]` 词段的前后上下文，深度核对各段说话人（Speaker）身份与立场，再推断哪个是投资机构、哪个是被访谈方/发言人。
- **核心纪律**：投资人通常是「抛出压力、质疑、要求数据验证」的一方；发言人通常是「解释业务、回答追问、组织逻辑应对」的一方。
- **严禁将投资人的质询、措辞或口误，判定为发言人的问题来写 Tier 改进建议；板子绝对不能打错人！** 找茬与 improvement_suggestion 必须 100% 锚定在【发言人】的实际应答上。
2【实战复盘与话术重构】：找出被尽调方在回答中的避重就轻、逻辑漏洞或数据打架。指出问题后，你必须指导他们如何完美应对！
3【定向狙击指令】：如果 <CONTEXT> 中提供了「🎯 主理人重点关注指令」（且内容不是占位「无」），你必须将其作为最高优先级！在录音中像侦探一样精准定位与该指令相关的对话，单独提取为一个 RiskPoint，进行核实、对比并给出明确结论！
</TASK>
<SCORING_RULE>
【量化扣分引擎（极度重要）】：
你必须采用【自下而上的扣分法】。满分 100 分。对于你找出的每一个风险点，必须给出具体的扣分值（score_deduction）：轻微啰嗦/瑕疵扣 2-5 分；逻辑卡壳/答非所问扣 6-10 分；严重违背 QA 口径/红线翻车扣 11-20 分。最终的 total_score 必须等于 100 减去所有 risk_points 中 score_deduction 的总和！绝不允许凭感觉给出一个固定分数（如 68 分）！
（注：若未提供 QA，仍须按上述档位为每个风险点赋值 score_deduction，并保证 total_score 与扣分总和一致。）
</SCORING_RULE>
<CONSTRAINTS>
必须提供两层剖析：
- Tier 1: 商业逻辑致命伤。
- Tier 2: 如果 <KNOWLEDGE_BASE> 为空或未提供有效内部 QA，必须直接回答「未提供内部 QA，基于行业常识推断」，绝对禁止凭空捏造虚假规定！若有知识库，则对比是否违背标准。

【一、视角绝对锁定（强制红线）】：
- 你的屁股必须绝对坐在“发言人”这一边！
- 绝对禁止输出“建议投资机构接下来如何提问”的内容。
- 所有的改进建议（improvement_suggestion），必须直接针对发言人提供「标准话术示范」（例如：“针对这个问题，建议你下次这样回答：第一...第二...”）。

【商业与法律合规红线（致命底线）】：
- 你给出的标准话术示范，绝对不允许包含任何财务层面的过度承诺、绝对化用语或违反中国现行法律法规（尤其是私募/投融资监管）的内容。
- 严禁教唆发言人说出“绝对保本保息”、“业绩绝对翻倍”等违规话术。
- 如遇极端棘手问题，教发言人用“高情商的外交辞令”化解，或以“数据需会后核实”为由安全着陆，绝不能编造承诺！

【切片精准度与「黄金 60 秒」剪辑（强制）】：
- 不要盲目圈定超长对话。以「直击痛点」为原则，将 `start_word_index` 与 `end_word_index` 所覆盖的交锋时长（按词级起止时间理解）引导在 **45–60 秒**左右。
- **截取艺术**：建议包含【问题最尖锐的末尾约 10 秒】+【回答最核心的约 40–50 秒】。若 Q&A 确实漫长且精彩，允许适度放宽，但 **绝对禁止超过 180 秒的无信息量无效圈地**；系统侧会对物理音频硬截断至 180 秒，过度圈地只会丢失后半段听感。

【二、逐字稿公文级洗稿（防御级红线）】：
在输出 original_text（翻车片段原文）时，必须执行【轻度书面化润色】操作，并遵守以下铁律：
1. 去噪：剔除所有无意义语气词（如：嗯、啊、那个、对吧、然后、就是说），修复明显的结巴和重复。
2. 防篡改：绝对禁止篡改原意、删减核心数据或改变说话人的原始态度。
3. 防总结：必须保留原始的交锋对话格式。绝对禁止将对话改为第三人称转述或概括性总结！
4. 纯净输出：该字段内只允许包含洗稿后的原文，严禁包含任何“[评语]”等元数据注释。
5. **标点重建（强制）**：语音转写往往缺乏标点。你必须为原始转写补充正确且丰富的书面标点（逗号、句号、问号等），切断超长句，禁止整段无标点或一逗到底。
6. **分段强制换行（强制）**：`original_text` 输出时必须「每轮对话独占一行」并真实换行（\\n）：先写 `[投资人]：……` 后 **必须换行**，再写 `[发言人]：……` 后 **必须换行**；多轮交锋继续交替换行，禁止将多轮挤在同一段落，确保高管阅读与复制到公文环境均丝滑。

【三、扣分说明与索引边界（强制）】：
- 根级字段 total_score_deduction_reason：结合总分与 <KNOWLEDGE_BASE>，说明主要扣分维度与依据。
- 每个 risk_points[] 元素的 deduction_reason：结合参考QA具体指出偏离了哪条口径；若无有效QA可写「未提供可对齐的QA条款，扣分依据为行业尽调常识」。
- 字段 is_manual_entry 仅允许为 false。
- 【极度重要】：输出 start_word_index 和 end_word_index 时切忌只圈出错片段的几个词；必须向外扩展索引边界，包含投资人完整提问与创始人完整回答段落，使切割音频能呈现完整交锋语境。

必须严格按照 JSON Schema 输出，start/end index 必须精确。
</CONSTRAINTS>
<JSON_SCHEMA>
{schema_str}
</JSON_SCHEMA>
<FINAL_REMINDER>
【最后重申你的核心纪律（至关重要）】：
1. 必须绝对站在发言人视角给话术，严禁当投资人的军师！
2. original_text 必须用公文级标准进行去口语化洗稿，严禁带入分析点评！
3. 必须严格执行量化扣分引擎：每个 risk_points[] 填写 score_deduction，且 total_score = 100 - Σscore_deduction！
4. 话术建议坚守合规底线，严禁过度承诺！
</FINAL_REMINDER>
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
    on_notice: Callable[[str], None] | None = None,
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

    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:MAX_TRANSCRIPT_CHARS]
        logger.warning(
            "转写已超过 MAX_TRANSCRIPT_CHARS=%d，已截取前缀以稳定上下文",
            MAX_TRANSCRIPT_CHARS,
        )

    qa_use = (qa_text or "").strip()
    qa_use, qa_truncated = truncate_qa_text(qa_use, MAX_QA_CHARS)
    if qa_truncated:
        warn_msg = (
            "⚠️ QA 补充材料字数超载（超过3万字），为防止 AI 崩溃，已截取核心头尾条款"
        )
        logger.warning("%s", warn_msg)
        if callable(on_notice):
            try:
                on_notice(warn_msg)
            except Exception:
                logger.exception("on_notice 回调失败")

    schema_str = json.dumps(
        AnalysisReport.model_json_schema(),
        ensure_ascii=False,
    )
    system_prompt = _build_system_prompt(schema_str, explicit_context, qa_use)
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

    t_api0 = time.monotonic()

    def _chat_once():
        return client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )

    try:
        response = run_with_backoff(
            _chat_once,
            logger=logger,
            operation=f"LLM chat.completions ({model_choice})",
        )
    except APIError as e:
        logger.exception("LLM API 请求失败")
        raise RuntimeError(f"LLM API 请求失败: {e}") from e
    except Exception as e:
        logger.exception("LLM 调用异常")
        raise RuntimeError(f"LLM 调用异常: {e}") from e

    logger.info(
        "LLM chat.completions 成功 model=%s 耗时=%.2fs",
        model_name,
        time.monotonic() - t_api0,
    )

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
