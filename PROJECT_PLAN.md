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
  - **主交付**：`build_release.py` BAT 纯净包（推荐）；目录名带 `CURRENT_VERSION`（如 `AI路演教练_纯净交付版_V7.5`）；随包 **`V7.5_新功能与体验大升级.txt`** / `V7.2_…` / `V7.0_…` / `V6.2_…` 等业务说明（见脚本白名单）；EXE 见 `PACKAGING_EXE.md`（实验性）。
  - **外发 HTML**：文件名脱敏 + 可选正文同规则替换 + 页脚水印（`HtmlExportOptions`，JSON 分析件保持原文）。
- [x] **阶段 4.2：V6.2 网关与打分（摘要）**
  - **智能音频网关**：`audio_preprocess` + `app.py` 大文件前置压缩后再 `transcribe_audio`。
  - **量化扣分**：`RiskPoint.score_deduction` + `llm_judge` Prompt；**定向核实**：结构化 **狙击清单**（`sniper_targets_json`）注入 CONTEXT。
- [x] **阶段 4.3：V7.0 审查台本地草稿（`draft_manager` + `.drafts/`）与 QA/转写分池截断（`llm_judge`）**
- [x] **阶段 4.4：V7.1 定向核实与切片掐头留尾；V7.2 `report_builder` 按索引物理覆写 `original_text`（防 QA 洗稿）+ `tests/test_v72_backend_override.py` 压测**
- [x] **阶段 4.5：V7.5 专家共驾** — 说话人 ID / 按 **[发言人 N]** 可读文字稿（`format_transcript_plain_by_speaker`）；流水线与锁定 JSON **覆写 `original_text`**；LLM **`max_tokens` + 截断 JSON 抢救**（`salvage_*`，`tests/test_v75_json_salvage.py`）；**`st.data_editor` 狙击清单**列 **原文引用 / 找茬疑点**、会话绑定（`tests/test_v75_formatter.py` 等）；发版 **`python build_release.py`** → **`AI路演教练_纯净交付版_V7.5`**
- [x] **阶段 4.6：V7.6 状态机解耦 · ASR 缓存 · 收官加固** 【COMPLETED 2026-04-02】
- [x] **阶段 4.7：V8.0 生产级协作与精准解析版** 【COMPLETED 2026-04-02】
  - **磁盘级 ASR 缓存**（`disk_asr_cache.py`）：MD5 哈希键 + 原子写入，三级缓存（内存→磁盘→云端），实现跨 session 永久免费秒开。
  - **项目专属热词库**：UI 文本框 → `transcribe_siliconflow initial_prompt` 注入，从源头提升专有名词识别率。
  - **三道防线审查台**：第一道（V7.6 狙击清单保留）→ 第二道（`refine_risk_point` 局部精炼 + 双 Key 安全注入）→ 第三道（`polish_manual_risk_point` AI 润色无中生有）。
  - **TDD**：新增 Mock 测试（含 disk_cache、hot_words、refinement 等）；全量 **`pytest tests/` → 74 passed**（以本机为准）。
  - **Schema 六联动**：`needs_refinement`、`refinement_note` 同步 Prompt、审查台、测试。
  - **V7.6 已并入本阶段交付**：双 Key 狙击表；会话 `asr_cache` + 流水线 `cached_words` 跳过 ASR（详见 `tests/test_v76_asr_cache.py`）。
  - **`CLAUDE.md`**：四大铁律（红蓝对抗 / TDD / Streamlit 状态机 / JSON 抢救）；**`AGENTS.md`**：全模型统一握手与文件地图。
  - 发版 **`python build_release.py`** → 目录名随 **`CURRENT_VERSION`** 变化。
- [x] **阶段 4.8：V8.3 生产级三大修复版** 【COMPLETED 2026-04-02】
  - **Bug 1 领域幻觉根治**：`llm_judge._build_system_prompt` 注入 `<DOMAIN_ANCHOR>` 块，硬科技/军工/低空经济领域铁律，7 个歧义词强制技术解释，3 条绝对红线（禁止捏造法律叙事/机构名/人名）。
  - **Bug 2 狙击清单静默失效修复**：`st.data_editor` 在 session_state 存 delta dict 非 DataFrame；新增 `batch_sniper_result_{idx}` 安全 key 存完整 DataFrame 返回值，`_batch_sniper_targets_json` 优先读 result_key。
  - **Bug 3 转写无标点修复**：Paraformer REST 参数补全 `enable_punctuation_prediction=True` + `disfluency_removal_enabled=True`。
  - 全量回归 **74 passed**。

## 四、 给 AI 助手 (Cursor) 的行为规范
每次回答前，请仔细复习本文件。
1. **不要重构能用的代码：** 如果一个模块测试通过了，除非我明确要求，否则绝对不要去改动它。
2. **严格遵守接口：** 任何新功能的添加，都必须先检查是否符合 `src/schema.py` 的定义，如果不符合，先与我讨论修改 Schema。
3. **只做当前步：** 不要替我把后面几个阶段的代码一次性全写出来，我们要像搭积木一样，测试完一块再写下一块。

## 🚀 v3.0 架构演进路线图 (Roadmap)

**当前状态 (V8.0)**：在 V7.6 全部能力之上，完成**磁盘级 ASR 缓存（三级架构）**、**项目专属热词库**、**三道防线审查台（局部精炼引擎 + AI 润色无中生有）**、**生产级状态机安全**，通过 **74 个全量回归测试**。

**产品提示（已实现/可配置）**：超大文档时应在业务侧拆分或提高截断阈值前评估 Token 与成本；侧边栏与 README 已强调「先 PDF/Word、非 PPT」等约束。

**未来触发条件**：当业务侧需要解析「数百页超大型文档（招股书、行业深度年报等）」，且硬截断导致对齐质量明显下降或成本不可接受时。

**终极升级方案**：启用 **Hierarchical RAG (分层检索认知架构)**。

- **升级思路**：不改动外层编排契约（`job_pipeline.run_pitch_file_job` + `schema`），主要替换 `src/document_reader.py` 引擎，必要时增加「检索上下文」字段进入 `evaluate_pitch`。
- **工作流**：AI 先看文档目录提取摘要 → 定位核心所在页码 → 仅精准抽取该页文本与录音对齐。

---

## 🔭 V8.0 研发重点展望（待规划）

**核心方向：深度 RAG 检索增强与多轮精炼引擎**

| 子课题 | 描述 |
|--------|------|
| **Hierarchical RAG** | `document_reader` 替换为分层检索引擎：目录摘要 → 页码定位 → 精准段落抽取，解决超大文档（招股书/行业年报）硬截断导致的对齐质量下降 |
| **多轮精炼对话** | 审查台从「一次生成+人工修改」升级为「AI 初稿 → 人工圈点 → AI 针对圈点处二次精炼」，缩短人工编辑时间 |
| **跨录音知识图谱** | 同一批次多条录音之间的矛盾点、重复风险点自动交叉比对，生成「批次级风险图谱」 |
| **持久化向量缓存** | 将 ASR 内存缓存（V7.6）升级为磁盘级向量索引，支持跨 session 的相似语义段复用 |

**触发条件**：当业务侧出现「同一项目多轮尽调、需要跨会议交叉核实」或「单次转写 >5 万字、QA 文档 >10 万字」时，启动 V8.0 预研。
