# AI 路演教练 — 架构与数据流（V3.1 / V4.0 / V6.2 / V7.0）

本文档供后续开发者与 AI 接管时快速建立心智模型：**模块职责、数据流、人机协同与商业级防护**。

---

## 1. 总览

| 层级 | 组件 | 职责 |
|------|------|------|
| UI | `app.py`（Streamlit） | API 配置、按录音上下文、**V6.2 智能音频网关**（`st.status` + `smart_compress_media`）、触发 `job_pipeline`、**V3 审查台**（`session_state`）、**V7.0 草稿恢复条**与静默落盘、锁定后 `generate_html_report` |
| 草稿 | `src/draft_manager.py` | **本地草稿静默持久化**：可写根下隐藏目录 `.drafts/`，`temp_*.json` → `os.replace` 原子落盘为 `draft_*.json`；`load_draft` / `list_available_drafts` 供断线恢复 |
| 网关 | `src/audio_preprocess.py` | ≥10MB：`ffmpeg` 抽视频轨 + 16k 单声道 MP3；失败回退原文件 |
| 编排 | `src/job_pipeline.py` | 转写 → 脱敏 → LLM → 写 JSON；可选跳过 HTML（供审查后再导出） |
| 转写 | `src/transcriber.py` | 硅基流动优先、阿里云 DashScope 兜底；**V4 请求指数退避** |
| 评判 | `src/llm_judge.py` | DeepSeek/Kimi/Qwen 路由；**V6.2 量化扣分引擎**（`score_deduction` + Prompt 自下而上扣分）；**定向狙击**（`session_notes`→CONTEXT）；**V7.0 QA 动态字数隔离**（转写 `MAX_TRANSCRIPT_CHARS`、QA `MAX_QA_CHARS` 分池；超长 QA **头尾保留 + 中间省略标记**）；超限经 `on_notice` 与 UI **黄字提示**；**退避重试** |
| 报告 | `src/report_builder.py` | **ffmpeg 子进程**切片 → **Base64 MP4(AAC)** 内嵌单文件 HTML；无 pydub |
| 契约 | `src/schema.py` | `AnalysisReport`、`RiskPoint`（含 **`score_deduction`**、`deduction_reason`、`is_manual_entry` 等） |
| 诊断 | `src/system_debug_log.py` | 统一 `debug.log`（可写根目录） |
| 退避 | `src/retry_policy.py` | 429 / 502–504 与网络类错误：**2s / 4s / 8s**，最多 4 次尝试 |
| 清理 | `src/garbage_collector.py` | 删除 **>7 天** 的 `*_transcription.json` / `*_analysis_report.json`；**永不删** `.html` 与音频 |

---

## 2. 核心数据流（单次录音）

1. 用户上传音频 → `app.py` 写入 **Workspace** 下 `业务大类/批次名/原文件名`。
2. **V6.2 智能音频网关**：`st.status` 汇报体积；≥10MB 时 `smart_compress_media` → 可选落地 `{stem}_v62_asr_gateway.mp3`，`run_pitch_file_job` 使用该路径；否则直通原文件。
3. **V6.2 量化扣分引擎**：LLM 为每个 `risk_points[]` 输出 `score_deduction`，`total_score` 须与「100 − Σ扣分」一致（由 Prompt + Schema 约束）。
4. `run_pitch_file_job(..., skip_html_export=True)`（审查台模式）：
   - `transcribe_audio` → 词列表 + `*_transcription.json`
   - `mask_words_for_llm` → 送 LLM 的脱敏词列表
   - `evaluate_pitch` → `AnalysisReport` → `*_analysis_report.json`
5. **不生成最终 HTML**；`app.py` 将 `report.model_dump()`（含 UI 用 `_rid`）与 `words` 写入 `st.session_state`：
   - `report_draft_{stem}`、`words_{stem}`、`v3_ctx_{stem}`、`v3_review_stems`
6. **V7.0**：审查台每次渲染时由 `draft_manager.save_draft(session_id, …)` 将上述快照 **静默写入** `.drafts/`；冷启动且侧栏无在审任务时，可 **一键恢复** 最近草稿。
7. 用户在审查台编辑后点击 **锁定** → `_v3_finalize_stem`：`copy.deepcopy` 汇总 widget → 校验 → 覆盖 JSON → `generate_html_report`。

---

## 3. V3.x 人机协同（Session State）

- **草稿键**：`report_draft_{stem}`（dict，对齐 `AnalysisReport` 字段 + `_rid`）。
- **转写键**：`words_{stem}`（list of dict，词级时间戳）。
- **上下文键**：`v3_ctx_{stem}`（音频路径、JSON/HTML 路径、水印、脱敏选项）。
- **深拷贝**：锁定导出前必须使用 `copy.deepcopy`，避免 Streamlit 重跑引用污染。

---

## 4. V3.1 报告与音频（无 pydub）

- 切片：**`imageio_ffmpeg` 定位 ffmpeg**，**`subprocess`** 输出 MP4 片段（AAC + `frag_keyframe+empty_moov`）。
- Windows：**隐藏控制台**（`STARTUPINFO` + `CREATE_NO_WINDOW`），减轻「黑框」与干扰。
- HTML：`<audio src="data:audio/mp4;base64,...">`；失败时卡片级红字降级说明 + 逐字稿正文。
- 审查台试听：`snippet_audio_mp3_bytes` 名保留，实际为 **MP4 片段**；`st.audio(..., format="audio/mp4")`。

---

## 5. V4.0 四大护城河

### 5.1 细粒度进度（UI + 流水线）

- `app.py`：`st.status("🚀 正在执行 AI 深度复盘...", expanded=True)` + `status.write` 里程碑说明。
- `job_pipeline.py`：`on_status` 回调输出阶段文案（提取特征/转写字数/脱敏/多维度 QA 对齐等），并 **`logger.info` 落盘**。

### 5.2 诊断日志与一键下载

- `setup_file_logging()`：在 `main()` 首行调用（幂等），向 **`get_writable_app_root() / debug.log`** 追加。
- 挂载 logger：`llm_judge`、`transcriber`、`report_builder`、`job_pipeline`、`garbage_collector`、`retry_policy` 等。
- `app.py`：主业务 `try/except Exception` → `st.error` + **`st.download_button` 下载 `debug.log`**。

### 5.3 Token/字符防线与退避

- **V7.0**：`llm_judge.evaluate_pitch` 对 **转写** 与 **QA** **分池限长**（`MAX_TRANSCRIPT_CHARS` / `MAX_QA_CHARS`），不再使用「转写+QA 合计 6 万字」单桶截断。QA 超限时 **掐头去尾** 并插入省略说明，经 `on_notice`（pipeline `_line`）与 **`app.py` 黄字 `st.warning`** 提示业务侧；**warning 日志**同步落盘。
- **不使用** `stream=True`，保持 **单一 JSON 响应** + `response_format=json_object`。
- `retry_policy.run_with_backoff`：`llm_judge` 的 `chat.completions.create`；`transcriber` 的 `requests` GET/POST（对 429/502–504 先 `raise_for_status` 触发重试）。

### 5.4 幽灵清道夫（GC）

- `sweep_stale_intermediate_json(workspace_root)`：递归 `rglob`，仅匹配 `*_transcription.json` / `*_analysis_report.json`，**mtime > 7 天** 则删除。
- **保护**：任何以 `.html` / 常见音频后缀结尾的文件 **不删除**。
- **触发**：应用启动后台线程（基于当前侧边栏 Workspace 或默认可写根）；**每批处理结束后** 再扫一次 `root_path`。

---

## 6. 路径与环境

- **`runtime_paths.py`**：`get_project_root`（只读资源）、`get_writable_app_root`（`.env`、`debug.log`、默认归档）。
- **`.env`**：位于可写根；`DASHSCOPE_API_KEY`、`DEEPSEEK_API_KEY` 等。

---

## 7. 测试

- `pytest tests/`：含 `job_pipeline`、`extreme_cases`、`garbage_collector` 等。
- 转写/LLM 集成测试以 mock 为主，避免外网依赖。

---

*文档版本：V7.0 · 与 app.py 当前行为对齐。*

### 架构示意（V6.2 / V7.0 增补链路）

```text
[上传原始媒体] → (app.py 体积探针) → [智能音频网关 audio_preprocess] → [transcribe_audio]
                                                      ↓ 失败回退原文件
[显式上下文 session_notes] ─────────────────────→ [llm_judge 定向狙击 + 量化扣分引擎] → AnalysisReport
```
