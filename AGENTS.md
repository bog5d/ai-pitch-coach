# 给 AI 助手（Cursor / Claude / 其它）的接手说明

本文件是**跨工具的统一入口**：新会话先读本节「握手区」，再按顺序读架构文档，最后动代码。目标：**减少重复勘探、降低 token、避免踩已修过的坑**。

---

## 0. AI 会话握手区（新对话必读，约 30 秒）

| 项目 | 当前事实（以仓库代码为准） |
|------|---------------------------|
| **发版号** | `build_release.py` → `CURRENT_VERSION`（现为 **V8.6.1**），纯净包目录名随其变化。 |
| **能力代际** | **V7.5–V8.4** 见下文文件地图与 ARCHITECTURE。**V8.6**：高管错题本、静默收割、`<HISTORICAL_PROFILE>` Top5、数字记忆库。**V8.6.1**：提炼 **仅 DeepSeek**；记忆字段 `risk_type` / `updated_at` / `hit_count`、Prompt **命中计数**、看板 **Top3 雷区**、锁定 **toast 收割反馈**。 |
| **回归测试** | `pytest tests/` → 当前全量 **133 passed**（含 `test_v86_*`、`test_v861_*`）。 |
| **Claude 专用** | 若使用 Claude Code，**额外**读根目录 **`CLAUDE.md`**（四大铁律：红蓝对抗、TDD、Streamlit 状态机、JSON 抢救）。其它模型也建议扫一眼铁律三、四。 |
| **人类操作** | **`小白保姆级操作手册.md`**（界面步骤）。 |

**改 `app.py` 狙击表前请先 grep**：禁止出现对 **`batch_sniper_editor_*`** 的 `st.session_state[...] =` 赋值（仅允许 `del` 以重置 widget）。初始表只允许写 **`batch_sniper_init_*`**。

**改流水线前必读**：`ARCHITECTURE.md` 第 8 节（V7.6 数据流 + 双 Key 协议 + 与磁盘缓存关系）。

---

## 1. 必读顺序（默认）

1. **`ARCHITECTURE.md`** — 模块职责、数据流、审查台、`session_state` 键、**V7.5–V8.0** 行为。**改业务逻辑前必须读过对应章节。**
2. **`PROJECT_PLAN.md`** — 产品原则（Pipeline、Schema、词级锚定、非 Agent 化）与阶段勾选记录。
3. **`CLAUDE.md`**（推荐）— Streamlit 死锁与 TDD 红线（与第 0 节互补）。
4. **任务涉及的 `src/*.py` / `app.py`** — 文档与代码冲突时 **以代码为准**，可顺手改文档一句。

**发版纪律**：根目录 **`.cursorrules`**（版本号、业务说明 txt 与白名单）。

---

## 2. 核心文件地图（改哪里）

| 区域 | 文件 | 注意点 |
|------|------|--------|
| UI / 审查台 / 草稿 / 网关 / **ASR 缓存键** | `app.py` | `asr_cache`（内存）、`_file_md5`；**`batch_sniper_init_{idx}`** 可写、**`batch_sniper_editor_{idx}`** 仅 widget；**禁止**对 `ed_key` 赋值；锁定前 `deepcopy`；**V8.6** `v3_initial_report_{stem}`、`v3_ctx.company_id`、`v86_dashboard_mode`；**高管数字记忆库** 仅用 `selectbox`+按钮写回 JSON，勿对 editor widget 反向赋值 |
| **磁盘 ASR 缓存** | `src/disk_asr_cache.py` | `{writable_root}/.asr_cache/{md5}.json`，原子写入；`app.py` 在命中内存后尝试磁盘、生成后回写 |
| **高管错题本 V8.6+** | `src/memory_engine.py` | `{writable_root}/.executive_memory/{company}/{tag}.json`；`capture_and_distill_diff`、`record_executive_memory_prompt_hits`、`top_risk_type_counts_for_company`、Dashboard |
| 草稿持久化 | `src/draft_manager.py` | 原子落盘；`.drafts/` |
| 单次任务编排 | `src/job_pipeline.py` | **`cached_words`** 非空则 **跳过** `transcribe_audio`；**`memory_company_id` + interviewee** 时加载 Top5 → **`record_executive_memory_prompt_hits`** → `evaluate_pitch` |
| 转写 | `src/transcriber.py` | 硅基优先、阿里兜底；**speaker_id**、`format_transcript_plain_by_speaker`；热词等与 V8.0 UI 联动 |
| LLM 评判与 Prompt | `src/llm_judge.py` | 分池截断、狙击清单、`max_tokens`、**salvage_***、**refine_risk_point** / **polish_manual_risk_point**；**V8.6** `<HISTORICAL_PROFILE>`；**V8.6.1** `distill_executive_memory_from_diff` **仅 DeepSeek** |
| HTML 报告与切片 | `src/report_builder.py` | `apply_asr_original_text_override`；180s 窗口等 |
| 数据契约 | `src/schema.py` | 含 **`needs_refinement`**、**`refinement_note`**、**`ExecutiveMemory`**（V8.6）；改字段需六向联动 |
| 大文件音频 | `src/audio_preprocess.py` | ≥10MB 网关 |
| 路径与可写根 | `src/runtime_paths.py` | `.env`、`debug.log`、Workspace、**.asr_cache** / **.executive_memory** 父目录 |

---

## 3. 不变量与易错点

- **词级时间戳**是切片与 `original_text` 覆写的共同依据。
- **审查台**：首轮初稿；**锁定**后写最终 JSON + HTML；落盘经 **`apply_asr_original_text_override`**。
- **`st.data_editor(key=ed_key)`**：`ed_key` 由 Streamlit 托管；**严禁** `st.session_state[ed_key] = ...`。重置用 **`del st.session_state[ed_key]`** + 更新 **`init_key`**。
- **改 `schema.py`**：同步 LLM、审查台、测试与 Prompt。
- **发版**：`CURRENT_VERSION`、白名单 txt、`.cursorrules` 一致。

---

## 4. 测试与回归

- `pytest tests/`（全量，当前 **74 passed**）
- 根目录：`pytest test_v7_acceptance.py`（若存在）
- V7.2 覆写：`tests/test_v72_backend_override.py`
- V7.5：`tests/test_v75_formatter.py`、`tests/test_v75_json_salvage.py`
- V7.6 缓存与流水线：`tests/test_v76_asr_cache.py`
- V8.0：`tests/test_v80_*`（磁盘缓存、热词、精炼等，以目录为准）
- V8.6：`tests/test_v86_memory_engine.py`、`test_v86_harvester.py`、`test_v86_injector.py`
- V8.6.1：`tests/test_v861_memory_evolution.py`

---

## 5. 其他文档索引

| 文档 | 用途 |
|------|------|
| `README.md` | 功能总览、Quick Start |
| `PACKAGING_EXE.md` | EXE / 纯净包 |
| `写给同事的使用说明书.txt` | 同事向说明 |
| `V*_新功能与体验大升级.txt` 等 | 业务大白话（`build_release.py` 白名单） |

---

*重大行为变更（新版本、新 session 键、新缓存层）请更新：本文件第 0 节表格、`ARCHITECTURE.md` 第 8 节、`PROJECT_PLAN.md` 阶段勾选 —— 方便下一任 AI **零重复考古**。*
