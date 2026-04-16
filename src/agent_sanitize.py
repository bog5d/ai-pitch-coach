"""
Week 4：LangGraph 输入脱敏辅助模块。

目标：
1. 仅处理即将进入 LLM 的文本，不改动原始词级 words。
2. 优先尝试 Presidio；若运行环境缺少其完整 NLP 条件，则回退到稳定的正则脱敏。
3. 输出简单可观测统计，供 AgentState 记录。
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any

try:
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
    from presidio_anonymizer import AnonymizerEngine
    _PRESIDIO_AVAILABLE = True
except Exception:  # pragma: no cover - 依赖缺失时走正则回退
    AnalyzerEngine = None
    AnonymizerEngine = None
    Pattern = None
    PatternRecognizer = None
    RecognizerRegistry = None
    _PRESIDIO_AVAILABLE = False


@dataclass(frozen=True)
class SanitizationResult:
    text: str
    redaction_count: int
    redaction_summary: dict[str, int]
    engine: str


_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![A-Za-z0-9._%+-])")
_ID_RE = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")
_PERSON_RE = re.compile(r"([赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜谢邹喻柏水窦章云苏潘葛范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴鬱胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公][\u4e00-\u9fff]{1,2})")


def _regex_sanitize(text: str) -> SanitizationResult:
    summary = {"PERSON": 0, "PHONE_NUMBER": 0, "EMAIL_ADDRESS": 0, "ID_NUMBER": 0}

    def _sub(pattern: re.Pattern[str], label: str, source: str) -> str:
        def _repl(_: re.Match[str]) -> str:
            summary[label] += 1
            return f"[{label}]"

        return pattern.sub(_repl, source)

    out = text
    out = _sub(_EMAIL_RE, "EMAIL_ADDRESS", out)
    out = _sub(_PHONE_RE, "PHONE_NUMBER", out)
    out = _sub(_ID_RE, "ID_NUMBER", out)
    out = _sub(_PERSON_RE, "PERSON", out)

    return SanitizationResult(
        text=out,
        redaction_count=sum(summary.values()),
        redaction_summary={k: v for k, v in summary.items() if v > 0},
        engine="regex_fallback",
    )


def _build_presidio_result(text: str) -> SanitizationResult:
    if not _PRESIDIO_AVAILABLE:
        raise RuntimeError("presidio unavailable")

    registry = RecognizerRegistry()
    registry.add_recognizer(
        PatternRecognizer(
            supported_entity="PHONE_NUMBER",
            patterns=[Pattern(name="cn_phone", regex=_PHONE_RE.pattern, score=0.7)],
        )
    )
    registry.add_recognizer(
        PatternRecognizer(
            supported_entity="EMAIL_ADDRESS",
            patterns=[Pattern(name="email", regex=_EMAIL_RE.pattern, score=0.8)],
        )
    )
    registry.add_recognizer(
        PatternRecognizer(
            supported_entity="ID_NUMBER",
            patterns=[Pattern(name="cn_id", regex=_ID_RE.pattern, score=0.8)],
        )
    )
    registry.add_recognizer(
        PatternRecognizer(
            supported_entity="PERSON",
            patterns=[Pattern(name="zh_person", regex=_PERSON_RE.pattern, score=0.35)],
        )
    )

    analyzer = AnalyzerEngine(registry=registry, supported_languages=["en"])
    results = analyzer.analyze(
        text=text,
        language="en",
        entities=["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "ID_NUMBER"],
    )
    anonymized = AnonymizerEngine().anonymize(text=text, analyzer_results=results)

    summary: dict[str, int] = {}
    for item in results:
        summary[item.entity_type] = summary.get(item.entity_type, 0) + 1

    return SanitizationResult(
        text=anonymized.text,
        redaction_count=len(results),
        redaction_summary=summary,
        engine="presidio",
    )


def sanitize_llm_input_text(text: str) -> SanitizationResult:
    raw = (text or "").strip()
    if not raw:
        return SanitizationResult(text="", redaction_count=0, redaction_summary={}, engine="noop")

    # Presidio 在某些本地环境下初始化较重；默认走稳定的正则脱敏。
    # 仅在显式开启时再尝试 Presidio，以避免阻塞主链路与测试。
    use_presidio = os.environ.get("USE_PRESIDIO_SANITIZER", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if use_presidio:
        try:
            result = _build_presidio_result(raw)
            if result.redaction_count > 0:
                return result
        except Exception:
            pass

    return _regex_sanitize(raw)


def sanitize_text_meta(result: SanitizationResult) -> dict[str, Any]:
    return {
        "engine": result.engine,
        "redaction_count": result.redaction_count,
        "redaction_summary": dict(result.redaction_summary),
    }
