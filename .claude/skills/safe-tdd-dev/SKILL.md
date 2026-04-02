# Skill: safe-tdd-dev
# AI Pitch Coach · 安全 TDD 开发技能（永久生效）

## 技能定位

**何时调用**：当被要求「开发新功能」、「修复 Bug」、「重构模块」时，在做任何改动前，强制激活本技能。

**核心价值**：
1. **零 API 费用测试**：通过 Mock 拦截所有外部 IO，确保自动化测试不消耗真实 DeepSeek / ASR 费用。
2. **不破坏主干**：全量回归绿灯后才允许提交，保护主线代码质量。
3. **UI 状态安全**：在涉及 Streamlit 的改动中，强制检查 session_state 双 Key 隔离。

---

## 技能工作流（强制顺序）

```
[接到需求]
    │
    ▼
Step 1: 红蓝对抗推演（≤5min）
    │  · LLM 幻觉风险？
    │  · UI 状态丢失风险？
    │  · 边界输入处理？
    │  · 测试需要 Mock 哪些外部 API？
    │
    ▼（推演通过）
Step 2: 先写 Mock 测试
    │  · 新建 tests/test_{feature}.py
    │  · patch 所有外部调用（transcribe_audio / evaluate_pitch / requests）
    │  · 覆盖：正常路径 + 边界 case + 异常路径
    │
    ▼
Step 3: 自主运行 pytest 直到全绿
    │  pytest tests/test_{feature}.py -v   ← 新测试
    │  pytest tests/ -q                    ← 全量回归
    │  红灯 → 读 stdout → 修复 → 重跑（循环）
    │
    ▼（全绿）
Step 4: 修改业务代码
    │  · 依据 Step 1 结论编写
    │  · 写完后再跑全量回归确认不破
    │
    ▼
Step 5: 汇报主理人，请求审查与合并授权
```

---

## Mock 速查表（AI Pitch Coach 项目专用）

| 外部依赖 | 被测模块中的 import | 正确 patch 路径 |
|----------|---------------------|-----------------|
| 阿里云 ASR 转写 | `from transcriber import transcribe_audio` | `job_pipeline.transcribe_audio` |
| DeepSeek 评判 | `from llm_judge import evaluate_pitch` | `job_pipeline.evaluate_pitch` |
| HTML 报告生成 | `from report_builder import generate_html_report` | `job_pipeline.generate_html_report` |
| ASR 实录覆写 | `from report_builder import apply_asr_original_text_override` | `job_pipeline.apply_asr_original_text_override` |
| HTTP 请求（转写器底层） | `requests.post` in transcriber | `transcriber.requests.post` |

> ⚠️ **Mock namespace 必须是「被测模块中的名字」，不是「原始定义模块的名字」**。

---

## 常用 Mock 代码片段

```python
# ── 完整 pipeline 测试（零 API 费用）────────────────────────
from unittest.mock import patch
from schema import AnalysisReport, SceneAnalysis, TranscriptionWord

MOCK_WORDS = [
    TranscriptionWord(word_index=0, text="测试", start_time=0.0, end_time=0.5, speaker_id="spk_a")
]
MOCK_REPORT = AnalysisReport(
    scene_analysis=SceneAnalysis(scene_type="测试", speaker_roles="A vs B"),
    total_score=85,
    total_score_deduction_reason="",
    risk_points=[],
)

def test_feature(tmp_path):
    with (
        patch("job_pipeline.transcribe_audio", return_value=MOCK_WORDS),
        patch("job_pipeline.evaluate_pitch", return_value=MOCK_REPORT),
        patch("job_pipeline.apply_asr_original_text_override", return_value=MOCK_REPORT),
    ):
        # ... 调用被测函数
        pass
```

```python
# ── 测试缓存命中（cached_words 路径）────────────────────────
def test_cache_hit_skips_asr(tmp_path):
    with patch("job_pipeline.transcribe_audio") as mock_asr:
        run_pitch_file_job(..., cached_words=MOCK_WORDS)
        mock_asr.assert_not_called()   # ← 核心断言
```

---

## 与 CLAUDE.md 铁律的映射关系

| 本技能 Step | 对应铁律 |
|-------------|----------|
| Step 1 红蓝对抗推演 | 铁律一 |
| Step 2 先写测试 | 铁律二 |
| Step 2 Mock 拦截 | 铁律五 |
| UI 检查（如涉及 Streamlit） | 铁律三 |
| JSON 解析检查（如涉及 LLM 输出） | 铁律四 |

---

## 调用方式

在 Claude Code 对话中：
- 输入 `/safe-tdd-dev` 加载完整工作流（slash command）
- 或在任意指令后追加 `use safe-tdd-dev skill`

*本文件由 Claude Code V7.6 收官时写入，永久生效。*
