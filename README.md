<div align="center">

# 🎯 AI 路演与访谈复盘教练

**Pipeline · 词级锚定 · 单文件离线报告**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?style=flat-square&logo=streamlit&logoColor=white)](https://streamlit.io/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active-success?style=flat-square)]()

<br/>

> **一句话**：将冗长路演 / 访谈录音，自动转化为带 **毒舌找茬**、**QA 对齐** 与 **可点击音频切片** 的结构化复盘报告。

<br/>

</div>

---

## ✨ 核心功能特性

| 能力 | 说明 |
| :--- | :--- |
| **分层文档 & RAG 解析** | 多格式 QA（PDF / Word / Excel / txt / md）抽取与 **智能截断**（合并后默认 **30k** 字符入 UI，送 LLM 前 **V7.0 分池**：QA 独立上限 + 超长头尾截断）；架构预留 **Hierarchical RAG** 演进（见 `PROJECT_PLAN.md` v3.0） |
| **阿里云大模型极速转写** | 百炼 DashScope 兼容链路 + 多引擎兜底，输出 **词级时间戳** 转写 |
| **DeepSeek 毒舌对齐分析** | 结构化打分与「找茬」点评，显式业务上下文 + QA 注入 |
| **按录音 QA** | 每条录音在界面中 **单独上传参考 QA**（可选；多文件合并后截断 **30k**）；1 条或多条音频同一套流程 |
| **文件名预填** | 支持按 **`机构-姓名`** 与可选末尾 **`YYYYMMDD`** 从录音主文件名预填 **被访谈人** 与 **狙击表首行疑点**（可关） |
| **非对称音频切割** | 按词索引锚定切片，非对称缓冲（起止留白可配），报告内嵌 **Base64 音频** |
| **外发合规（HTML）** | 文件名脱敏；可选 **正文同规则替换**；**页脚水印**；`*_analysis_report.json` 默认保留完整原文供内部分析 |
| **V3.0 人机协同审查台** | 首轮流水线 **只出初稿 JSON**（不写最终 HTML）；主界面 **【报告审查与人工编辑台】** 从 `st.session_state` 编辑评语/扣分理由、删改翻车片段、人工增补复盘点；点 **【确认无误，锁定并生成最终版 HTML】** 后才覆盖写入 `*_analysis_report.json` 并调用 `report_builder.generate_html_report` |
| **V6.2 体验升级** | **大文件/视频**：≥10MB 可走智能音频网关（抽轨 + 降采样）再送转写；`.streamlit/config.toml` 将单文件上传上限提升至 **1GB**（随纯净包分发）。**打分**：逐项 `score_deduction` 自下而上扣分算总分。**定向核实**：结构化 **狙击清单**（原文引用 + **找茬疑点**）写入 Prompt。 |
| **V7.0 深度护航** | **审查台草稿**：`.drafts/` 原子落盘、侧栏 **恢复上次未完成草稿**、审查区静默自动保存。**QA**：独立字数池 + 超长资料头尾智能截取，**黄字**提示业务侧。 |
| **V7.1–V7.5 切片、实录与共驾** | **仅提取文字稿**（按说话人分段，复制到「原文引用」列）、**字面锚定**、超长切片 **保留末尾 180s**。**V7.2+**：流水线与锁定落盘的 `*_analysis_report.json` 均在写盘前 **`apply_asr_original_text_override`**，与 HTML 一致。**V7.5**：ASR **说话人 ID**、`max_tokens` 扩容、JSON **截断抢救**、**`st.data_editor` 狙击清单**（会话绑定）。说明见 **`V7.5_新功能与体验大升级.txt`**（及 `V7.2_…` / `V7.0_…` / `V6.2_…`，随 `build_release.py` 白名单）。 |
| **V9.6 两阶段深评 · 并发硬化** | **两阶段深评引擎**：阶段一轻量扫描出靶点，阶段二聚焦并发深评（`ThreadPoolExecutor`），10靶点耗时从 50s → ~10s。**ASR 轻量润色**：转写后 DeepSeek 修正错别字 + 行业术语（词级映射，时间戳严格不变）。**魔法对话框**：一句话指令局部重写改进建议。 |
| **V9.6.1 稳定性四连修** | ① 多音频批处理音频播放修复（存原始路径，不存临时 gateway）② 审查台翻车片段信息降载（4 字段默认 + 专家视图折叠）③ 风险点数量约束（≤10 + 质量门槛 Prompt）④ 空白卡片过滤（`_is_valid_risk_point` 守门）。 |
| **V9.6.2 工业级稳定性十修** | 阿里云任务轮询 **GET**、长录音时间戳单位、**缓存命中跳过二次润色**、超长文件名路径保护、阶段一截断写入报告说明、错题 **`raw_text` 幂等**、指挥中心 **单次聚合读盘**、审查台草稿 **KeyError 防护**、逻辑冲突告警降噪等；**262 passed**（全量 Mock 回归）。 |
| **V10.1 双态留痕 + 数据中台** | **凡运行必留痕**：AI 初稿就绪即写 `status=draft` analytics，锁定后覆写为 `status=locked`。Dashboard 重构为 4-Tab（📊会话总览 / 👤个人成长 / 🌐行业基准 / 🧠AI纠偏库）；session_id 基于 stem 确定性生成（uuid5），draft→locked 全程追踪。 |
| **V10.2 机构数据飞轮** | 机构注册表（`institution_registry.py`）短名称模糊匹配（SequenceMatcher + 拼音）、institutions.json 原子备份、GitHub 同步状态追踪、会前简报 `briefing_engine.py`。 |
| **V10.3 PM 红蓝对抗全项修复** | **P0** 短名称炸弹根治 + 备份机制 + 同步告警；**P1** 历史数据迁移 + 融资结果字段 + 记忆权重 90 天衰减；**P2** 会前演练模式（AI 扮投资人）+ 客户只读 Dashboard + 多页架构；**P3** 融资成功率预测 + Partner 投资人画像 + **多语言支持**（中英文自动检测，英文访谈全程英文输出）；**513 passed**。 |

---

## 🚀 Quick Start（极速上手）

### 环境要求

- **Python 3.10+**（推荐 3.11）
- 可访问互联网（调用云端转写与 LLM API）

### 1. 克隆仓库

```bash
git clone https://github.com/bog5d/ai-pitch-coach.git
cd ai-pitch-coach
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 `.env`（必填）

在**项目根目录**新建 `.env`（可复制 `.env.example`；**切勿将含真实 Key 的 `.env` 提交到 Git**），至少配置：

| 变量 | 用途 |
| :--- | :--- |
| `DASHSCOPE_API_KEY` | 阿里云百炼 DashScope（转写 / 兼容接口探活等） |
| `DEEPSEEK_API_KEY` | DeepSeek 官方 API（默认「毒舌」复盘评委） |

可选：`SILICONFLOW_API_KEY`、`KIMI_API_KEY` 等（见各模块说明）。

### 4. 启动控制台

```bash
streamlit run app.py
```

浏览器打开后，在侧边栏完成 **API 与 FFmpeg 全量环境测试（全绿）**，再上传音频、按条填写上下文与 QA，点击生成后先在 **审查台** 校对，再 **锁定** 导出最终 HTML（支持单次 1 条或多条录音）。

---

## 📦 主交付形态（推荐）

| 形态 | 适用 | 说明 |
| :--- | :--- | :--- |
| **`build_release.py` 纯净包 + BAT** | **生产与同事分发（首选）** | 与开发环境一致的 `streamlit run app.py`；体积小、可维护、易排障。 |
| **PyInstaller EXE** | 实验 / 必须「单 exe 目录」分发时 | 见 `PACKAGING_EXE.md`；Streamlit 冻结环境更脆弱，出问题请退回 BAT 包。 |

---

## 📦 小白专属 · Windows 无代码交付

仓库内置 **`build_release.py`**：一键生成面向 Windows 同事的 **纯净交付包**（含 **`一键启动系统.bat`**、`requirements.txt`、`app.py`、`src/` 等白名单资源，**不含** `.env` / 测试数据 / `output`）。

```bash
python build_release.py
```

生成目录：**`AI路演教练_纯净交付版_{版本}/`**（版本号由 `build_release.py` 的 `CURRENT_VERSION` 决定，当前为 **`AI路演教练_纯净交付版_V10.3/`**）— 可直接拷贝至 U 盘分发；同事双击 BAT 即可完成依赖安装与启动。

> 更细的操作说明见根目录 **`小白保姆级操作手册.md`**（若随仓分发）。

### Windows EXE 单体（开发者可选）

使用根目录 **`run_exe.py`** 作为 PyInstaller 入口，打包后进入 **`dist/AI路演复盘教练/`** 运行生成的 **`.exe`**（`--onedir` 目录分发）。完整命令、Python 3.13 / `setuptools` 注意事项见 **`PACKAGING_EXE.md`**。

---

## 🏗️ 架构速览

- **Pipeline + Pydantic 契约**（`src/schema.py`），拒绝不可控 Agent 编排  
- **词级索引** 驱动音频切割，避免全文模糊匹配  
- 详细设计见 **`PROJECT_PLAN.md`**；**V3.1–V7.5 数据流**（含草稿、QA 分池、狙击清单、**实录物理覆写**、日志/GC/退避）见 **`ARCHITECTURE.md`**

### V3.0 接手速查（Human-in-the-Loop）

| 主题 | 说明 |
| :--- | :--- |
| **数据契约** | `AnalysisReport` 含 `total_score_deduction_reason`；每条 `RiskPoint` 含 **`score_deduction`**（V6.2 量化扣分，延续至今）、`original_text`（**V7.2+** 流水线与锁定前由 `apply_asr_original_text_override` 按索引覆写）、`deduction_reason`、`is_manual_entry`。见 `src/schema.py`。 |
| **LLM** | `src/llm_judge.py` 要求模型输出扣分原因并与 QA 口径对齐说明。 |
| **流水线** | `run_pitch_file_job(..., skip_html_export=True)` 时仍写 `*_analysis_report.json` 初稿，但 **不** 生成 HTML；返回 `(words, report)` 供 UI 注入状态。见 `src/job_pipeline.py`。 |
| **Streamlit** | 每录音 `stem`：`report_draft_{stem}`、`words_{stem}`、`v3_ctx_{stem}`、`v3_review_stems`。**V7.0** 草稿静默保存与恢复；**V7.1**「仅提取文字稿」；**V7.5** 狙击清单 `batch_sniper_editor_{idx}`；未点「开始生成」时若仍有 `v3_review_stems`，会 **继续展示审查台**。见 `app.py`。 |
| **定稿** | 仅用户点击锁定后：`app.py` 将当前 `session_state` 写回 `*_analysis_report.json` 并调用 `src/report_builder.generate_html_report`。逐字稿与切片见 `format_transcript_snippet` / `snippet_audio_mp3_bytes`。 |

---

## 📁 目录结构（节选）

| 路径 | 说明 |
| :--- | :--- |
| `app.py` | Streamlit 企业控制台（V3.0 审查台状态 + 锁定导出；业务编排见 `src/job_pipeline.py`） |
| `src/` | 转写、打分、报告拼装（**V7.5 实录覆写 + 共驾加固**）、文档读取、`draft_manager`、`job_pipeline` 等 |
| `tests/` | 测试与黄金数据（大文件见 `.gitignore`） |
| `build_release.py` | 纯净交付包打包脚本 |
| `run_exe.py` / `PACKAGING_EXE.md` | EXE 启动器与 PyInstaller 说明 |

---

## 📄 许可

**MIT License** — 使用第三方 API 时请遵守各平台条款，并妥善保管密钥与访谈数据。

---

<div align="center">

**Built with ☕ and strict schemas**

</div>
