# AI 路演教练与复盘系统 - 项目施工蓝图

## 一、 核心目标与产品形态
- **目标：** 打造一套全自动的复盘工具，替代合伙人人工听录音打分的时间。
- **形态：** 本地运行的 Python 脚本/Streamlit 应用。
- **最终输出：** 一份 Base64 编码的单文件 HTML 报告，内嵌"翻车片段"的文字点评与可直接点击播放的精准音频切片。

## 二、 核心架构原则（绝不可违背的铁律）
1. **摒弃全自主智能体 (No Agentic Workflow)：** 坚决采用高确定性的"流水线 (Pipeline)"架构，禁止让 LLM 自主决定下一步动作。
2. **规格驱动开发 (Spec-Driven)：** 所有的模块（转文字、打分、切片、HTML生成）必须高度解耦。模块之间通过 `src/schema.py` 中定义的 Pydantic v2 模型进行数据交互。
3. **词级索引锚定 (Word-Level Indexing)：** 彻底放弃"文本模糊匹配"。音频切割必须强依赖转写提供的词级时间戳（start_word_index, end_word_index），实现零误差切割。
4. **测试前置与固化：** 必须使用伪造的"黄金测试数据（JSON格式）"先跑通纯后端的物理逻辑（切音频、生网页），最后再接入大模型 API，避免测试成本爆炸。

## 三、 逐步施工路径 (开发进度记录)
- [x] **阶段 1：契约与测试桩搭建**
  - 创建 `src/schema.py` 锁定所有数据接口。
  - 黄金测试数据见 `tests/`（含极端用例脚本）。
- [x] **阶段 2：无脑物理切割机 (不依赖 AI)**
  - 词级锚定切割 + Base64 内嵌 MP3 单文件 HTML（`src/report_builder.py`）。
- [x] **阶段 3：注入灵魂 (接入大模型)**
  - 阿里云 DashScope 兼容链路转写（词级时间戳）+ DeepSeek 等评委（`src/transcriber.py`、`src/llm_judge.py`）。
- [x] **阶段 4：前端组装**
  - Streamlit 控制台（`app.py`）；**编排层**已抽至 `src/job_pipeline.py`（`run_pitch_file_job`），便于 CLI/自动化复用。
- [x] **阶段 4.1：交付与合规增强（v2.x）**
  - **主交付**：`build_release.py` BAT 纯净包（推荐）；目录名带 `CURRENT_VERSION`（如 `AI路演教练_纯净交付版_V7.0`）；随包 **`V7.0_新功能与体验大升级.txt`** / `V6.2_…` 等业务说明（见脚本白名单）；EXE 见 `PACKAGING_EXE.md`（实验性）。
  - **外发 HTML**：文件名脱敏 + 可选正文同规则替换 + 页脚水印（`HtmlExportOptions`，JSON 分析件保持原文）。
- [x] **阶段 4.2：V6.2 网关与打分（摘要）**
  - **智能音频网关**：`audio_preprocess` + `app.py` 大文件前置压缩后再 `transcribe_audio`。
  - **量化扣分**：`RiskPoint.score_deduction` + `llm_judge` Prompt；**定向核实**：`session_notes` / 🎯 输入框注入 CONTEXT。
- [x] **阶段 4.3：V7.0 审查台本地草稿（`draft_manager` + `.drafts/`）与 QA/转写分池截断（`llm_judge`）**

## 四、 给 AI 助手 (Cursor) 的行为规范
每次回答前，请仔细复习本文件。
1. **不要重构能用的代码：** 如果一个模块测试通过了，除非我明确要求，否则绝对不要去改动它。
2. **严格遵守接口：** 任何新功能的添加，都必须先检查是否符合 `src/schema.py` 的定义，如果不符合，先与我讨论修改 Schema。
3. **只做当前步：** 不要替我把后面几个阶段的代码一次性全写出来，我们要像搭积木一样，测试完一块再写下一块。

## 🚀 v3.0 架构演进路线图 (Roadmap)

**当前状态 (V7.0)**：`app.py` 侧多文件 QA 合并后 **`extract_text_from_files` 默认 `max_chars=30000`**；送入 `llm_judge.evaluate_pitch` 前对 **转写与 QA 分池限长**（`MAX_TRANSCRIPT_CHARS` / `MAX_QA_CHARS`），超长 QA **头尾截断**并 UI 提示。在典型几十页 QA 场景下，稳定性与速度可接受。

**产品提示（已实现/可配置）**：超大文档时应在业务侧拆分或提高截断阈值前评估 Token 与成本；侧边栏与 README 已强调「先 PDF/Word、非 PPT」等约束。

**未来触发条件**：当业务侧需要解析「数百页超大型文档（招股书、行业深度年报等）」，且硬截断导致对齐质量明显下降或成本不可接受时。

**终极升级方案**：启用 **Hierarchical RAG (分层检索认知架构)**。

- **升级思路**：不改动外层编排契约（`job_pipeline.run_pitch_file_job` + `schema`），主要替换 `src/document_reader.py` 引擎，必要时增加「检索上下文」字段进入 `evaluate_pitch`。
- **工作流**：AI 先看文档目录提取摘要 → 定位核心所在页码 → 仅精准抽取该页文本与录音对齐。
