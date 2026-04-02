# CHANGELOG — AI 路演与访谈复盘系统

所有版本变更按时间倒序记录。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [V7.6] — 2026-04-02 · 状态机解耦 · ASR 缓存 · 收官加固版

### 新增
- **ASR 内存缓存机制**（`app.py` + `job_pipeline.py`）：
  - 新增 `_file_md5(bytes) -> str` 辅助函数，以文件内容 MD5 为缓存键。
  - `_v71_transcribe_upload_to_plain`（仅提取文字稿按钮）转写完成后将 `(words, plain)` 写入 `st.session_state["asr_cache"]`。
  - `run_pitch_file_job` 新增 `cached_words: list[TranscriptionWord] | None` 参数：命中时直接跳过云端 ASR 调用，节省费用；同时仍将词列表写盘保持归档完整性。
  - 主流程点击「生成报告」时优先检查缓存，命中即透传 `cached_words`，同一录音不再重复计费。
- **单元测试**（`tests/test_v76_asr_cache.py`，9 个 case）：覆盖缓存命中跳过 ASR、缓存未命中正常调用、命中时仍落盘 JSON、MD5 键等效性。
- **`CLAUDE.md` 最高行动宪法**：四大铁律（红蓝对抗 / TDD / Streamlit 状态机死锁红线 / JSON 截断抢救）固化为项目级约束，优先级高于任何对话指令。

### 修复
- **彻底消灭 `StreamlitValueAssignmentNotAllowedError`**（铁律三落地）：
  - 拔除所有对 widget 绑定 key 的反向赋值写操作。
  - 引入**双 Key 隔离法**：`batch_sniper_init_{idx}`（初始数据专用，写入唯一入口）与 `batch_sniper_editor_{idx}`（仅绑定 `st.data_editor` widget，严禁写入）严格分离。
  - 文件名自动填充时先更新 `init_key`，再 `del session_state[ed_key]` 强制 widget 以新数据重新初始化，用户编辑状态在重跑间可靠保留。
  - `_batch_sniper_targets_json` 读取逻辑更新：优先取 widget 托管的用户编辑结果（`ed_key`），兜底取初始数据（`init_key`）。

### 改动
- `src/job_pipeline.py`：`run_pitch_file_job` 函数签名新增 `cached_words` 关键字参数（向后兼容，默认 `None`）。
- `app.py`：引入 `_file_md5`；`_v71_transcribe_upload_to_plain` 新增缓存写入；主流程循环新增缓存检查与写入；`data_editor` 渲染块全面切换为 `init_key`/`ed_key` 双 Key 隔离。

---

## [V7.5] — 专家共驾 · 红蓝对抗加固版

### 新增
- 说话人区分（厂商 ID 优先，无则自动编号）；`format_transcript_plain_by_speaker` 按人分段导出，格式 `[发言人 N]: …`，无词序号污染。
- `run_pitch_file_job` / 锁定 JSON 落盘前 `apply_asr_original_text_override` 按词表索引物理覆写 `original_text`，根治模型洗稿。
- LLM 显式 `max_tokens`（8192）+ 截断 JSON 安全抢救（逆向寻末尾合法闭合）。
- `st.data_editor` 狙击清单：列「原文引用 / 找茬疑点」，按条 1v1 写入 LLM 上下文。
- 测试：`test_v75_formatter.py`、`test_v75_json_salvage.py`。

### 修复
- `original_text` 落盘前不再保留 LLM 生成的洗稿文本，统一由 ASR 索引切片覆写。

---

## [V7.2] — 实录保真 · `original_text` 物理覆写

### 新增
- `apply_asr_original_text_override`：报告落盘前按词级索引强制覆写 `original_text`，切断 QA 洗稿路径。
- 测试：`test_v72_backend_override.py`（含毒药数据 + 越界压测）。

---

## [V7.1] — 定向核实 · 音频切片掐头留尾

### 新增
- 定向核实字面锚定 + 约 60s 纪律。
- 超长窗口保留末尾 180s（`snippet_audio_mp3_bytes`）。
- 仅提取文字稿（`_v71_transcribe_upload_to_plain`）。

---

## [V7.0] — 草稿箱 · QA 分池截断

### 新增
- `draft_manager`：本地草稿静默持久化，原子落盘，冷启动断点续审。
- `llm_judge` QA 分池限长（`MAX_TRANSCRIPT_CHARS` / `MAX_QA_CHARS`）。

---

## [V6.2] — 智能音频网关 · 量化扣分

### 新增
- `audio_preprocess`：≥10MB 视频轨剥离 + 16k 单声道 MP3 压缩网关。
- `score_deduction`（RiskPoint 量化扣分）+ 结构化狙击清单（`sniper_targets_json`）。
- `garbage_collector`：>7 天中间 JSON 自动清理。

---

*最后更新：V7.6 发版，2026-04-02。*
