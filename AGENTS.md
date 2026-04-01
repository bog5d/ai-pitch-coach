# 给 AI 助手（含 Claude Code）的接手说明

本文件是**开发者/自动化助手的入口**：先按顺序读文档，再改代码。人类同事也可当速查索引。

---

## 1. 必读顺序（默认）

1. **`ARCHITECTURE.md`** — 模块职责、数据流、审查台、`session_state` 键、**V7.5** 行为（草稿、QA 分池、狙击清单、`original_text` 覆写、切片 180s、JSON 抢救等）。**改业务逻辑前必须读过对应章节。**
2. **`PROJECT_PLAN.md`** — 产品原则（Pipeline、Schema 契约、词级锚定、非 Agent 化等）。**不要随意把流水线改成「多轮 Agent 对话」除非主理人明确要求。**
3. **具体任务涉及的 `src/*.py` / `app.py`** — 以代码与现有测试为准；文档若与代码冲突，**以代码为准**，并考虑是否顺手修正文档一句（小改即可）。

**界面与操作**（按钮文案、步骤、审查台怎么用）：以 **`小白保姆级操作手册.md`** 为准（人话、少术语）。

**发版与打包纪律**：根目录 **`.cursorrules`**（版本号、`V{x.x}_新功能与体验大升级.txt` 与白名单）。

---

## 2. 核心文件地图（改哪里）

| 区域 | 文件 | 注意点 |
|------|------|--------|
| UI / 审查台 / 草稿 / 网关触发 | `app.py` | Streamlit、`st.session_state`、**狙击清单 data_editor**、锁定前 `deepcopy`、V7.0 `.drafts/` |
| 草稿持久化 | `src/draft_manager.py` | 原子落盘；路径在可写根下 `.drafts/` |
| 单次任务编排 | `src/job_pipeline.py` | 转写 → 脱敏 → LLM → JSON；可 `skip_html_export` |
| 转写 | `src/transcriber.py` | 外网 API、`speaker_id`、**按说话人纯文本导出**、退避在 `retry_policy` |
| LLM 评判与 Prompt | `src/llm_judge.py` | QA/转写分池截断、**结构化狙击清单**、`max_tokens`、**截断 JSON 抢救**、场记式 `original_text` 约束 |
| HTML 报告与切片 | `src/report_builder.py` | **V7.2+**：`apply_asr_original_text_override` 在导出前覆写 `original_text`；超长窗口保留末尾 180s |
| 数据契约 | `src/schema.py` | `AnalysisReport` / `RiskPoint`；改字段要同步 Prompt、审查台、报告与测试 |
| 大文件音频预处理 | `src/audio_preprocess.py` | ≥10MB 网关；失败回退原文件 |
| 路径与可写根 | `src/runtime_paths.py` | `.env`、`debug.log`、Workspace |
| 文档与 QA 解析 | `src/document_reader.py` | 多格式合并与截断策略与 UI/LLM 侧一致 |
| 退避 / 日志 / GC | `src/retry_policy.py`、`src/system_debug_log.py`、`src/garbage_collector.py` | 勿删中间件保护逻辑 |

根目录还有 **`run_phase2.py`**、**`src/run_phase2.py`**（若存在脚本入口，以实际用途为准）。

---

## 3. 不变量与易错点（尽量别踩）

- **词级时间戳**是切片与 `original_text` 覆写的共同依据；动 `words` 结构或索引语义时要通盘考虑 `report_builder` 与 `llm_judge`。
- **审查台**：首轮只出初稿 JSON；**锁定**后才写最终 JSON 并调 `generate_html_report`。**V7.5** 起流水线落盘与锁定落盘均经 **`apply_asr_original_text_override`**，JSON 与 HTML 的「发言人口述实录」与 ASR 同源。
- **`*_analysis_report.json`**：与 HTML 内文已对齐（仍以代码为准）；勿只改前端展示而忽略词级覆写链。
- **改 `schema.py`**：同步 JSON Schema / Pydantic、LLM `response_format`、审查台字段、以及任何依赖 `model_dump` 的序列化。
- **发版**：`build_release.py` 的 `CURRENT_VERSION`、白名单里的版本说明 txt、`.cursorrules` 约定一致。

---

## 4. 测试与回归

- 目录测试：`pytest tests/`
- 根目录验收：`pytest test_v7_acceptance.py`（路径以仓库根为准）
- V7.2 覆写与边界：`tests/test_v72_backend_override.py`
- 极端窗口等：`tests/test_extreme_cases.py`

集成测试多依赖 mock；改流水线后至少跑与改动模块相关的测试子集。

---

## 5. 其他文档索引

| 文档 | 用途 |
|------|------|
| `README.md` | 功能总览、Quick Start、目录说明 |
| `PACKAGING_EXE.md` | EXE / 纯净包相关 |
| `写给同事的使用说明书.txt` | 同事向说明 |
| `V7.5_…` / `V7.2_…` / `V7.0_…` 等 txt | 大版本业务向大白话（随 `build_release.py` 打包） |

---

*若你新增重大行为（新版本号、新模块），请在本文件「核心文件地图」或「必读顺序」中补一行，方便下一个 AI 会话接上。*
