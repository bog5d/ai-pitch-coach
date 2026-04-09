# AI 路演教练 — 架构与数据流（V3.1 … V9.6.1）

本文档供后续开发者与 AI 接管时快速建立心智模型：**模块职责、数据流、人机协同与商业级防护**。

---

## 1. 总览

| 层级 | 组件 | 职责 |
|------|------|------|
| UI | `app.py`（Streamlit） | API、Workspace、**V6.2 音频网关**、触发 `job_pipeline`、**V3 审查台**、**V7.0 草稿**、**仅提取文字稿**（按说话人分段）；**V7.6** 狙击表 **双 Key** + **`asr_cache`**；**V8.0** **`disk_asr_cache`**、热词、`refine`/`polish`；**V8.4** 公司档案；**V8.6** **高管数字记忆库**、初稿快照、静默收割；锁定后 `generate_html_report` |
| 草稿 | `src/draft_manager.py` | **本地草稿静默持久化**：可写根下隐藏目录 `.drafts/`，`temp_*.json` → `os.replace` 原子落盘为 `draft_*.json`；`load_draft` / `list_available_drafts` 供断线恢复 |
| 网关 | `src/audio_preprocess.py` | ≥10MB：`ffmpeg` 抽视频轨 + 16k 单声道 MP3；失败回退原文件 |
| 编排 | `src/job_pipeline.py` | 转写（或 **`cached_words` 跳过 ASR**）→ 脱敏 → LLM（**V8.6** 可选 **`historical_memories` Top5**）→ 写 JSON；可选跳过 HTML |
| 转写 | `src/transcriber.py` | 硅基流动优先、阿里云 DashScope 兜底；**V4 请求指数退避**；**V7.5** 厂商/自动 **speaker_id**，`format_transcript_plain_by_speaker` 人类可读导出 |
| 评判 | `src/llm_judge.py` | DeepSeek/Kimi/Qwen **主评委**；**V8.6.1** 错题提炼 **仅 DeepSeek**（与主底座一致）；**`<HISTORICAL_PROFILE>`**；**V6.2 量化扣分**；**狙击清单**；**V7.0 QA 分池**；**V7.5** `max_tokens`、**JSON 抢救**；**退避重试** |
| 报告 | `src/report_builder.py` | **ffmpeg** 词级切片 → Base64 内嵌 HTML；**V7.2+** `apply_asr_original_text_override` **按索引物理覆写** `original_text`；**V7.1** 超长窗口 **保留末尾 180s** |
| 契约 | `src/schema.py` | `AnalysisReport`、`RiskPoint`；**V8.6** **`ExecutiveMemory`**（错题本） |
| 诊断 | `src/system_debug_log.py` | 统一 `debug.log`（可写根目录） |
| 退避 | `src/retry_policy.py` | 429 / 502–504 与网络类错误：**2s / 4s / 8s**，最多 4 次尝试 |
| 清理 | `src/garbage_collector.py` | 删除 **>7 天** 的 `*_transcription.json` / `*_analysis_report.json`；**永不删** `.html` 与音频 |

---

## 2. 核心数据流（单次录音）

1. 用户上传音频 → `app.py` 写入 **Workspace** 下 `业务大类/批次名/原文件名`。
2. **V6.2 智能音频网关**：`st.status` 汇报体积；≥10MB 时 `smart_compress_media` → 可选落地 `{stem}_v62_asr_gateway.mp3`，`run_pitch_file_job` 使用该路径；否则直通原文件。
3. **V6.2 量化扣分引擎**：LLM 为每个 `risk_points[]` 输出 `score_deduction`，`total_score` 须与「100 − Σ扣分」一致（由 Prompt + Schema 约束）。
4. `run_pitch_file_job(..., skip_html_export=True)`（审查台模式）：
   - 若传入 **`cached_words`**：`transcribe_audio` **跳过**，仍写入 `*_transcription.json`；否则 `transcribe_audio` → 词列表 + `*_transcription.json`
   - `mask_words_for_llm` → 送 LLM 的脱敏词列表
   - `evaluate_pitch` → `AnalysisReport` → **`apply_asr_original_text_override`** → `*_analysis_report.json`（**V7.5**：落盘即干净 `original_text`）
5. **不生成最终 HTML**；`app.py` 将 `report.model_dump()`（含 UI 用 `_rid`）与 `words` 写入 `st.session_state`：
   - `report_draft_{stem}`、**`v3_initial_report_{stem}`**（V8.6：AI 初稿快照，供锁定导出时 diff）、`words_{stem}`、`v3_ctx_{stem}`（含 **`company_id`**）、`v3_review_stems`
6. **V7.0**：审查台每次渲染时由 `draft_manager.save_draft(session_id, …)` 将上述快照 **静默写入** `.drafts/`；冷启动且侧栏无在审任务时，可 **一键恢复** 最近草稿。
7. 用户在审查台编辑后点击 **锁定** → `_v3_finalize_stem`：… **`generate_html_report`** … **V8.6**：成功后 **`_v86_harvest_finalize_if_needed`**：按 `_rid` 对齐初稿与终稿，**防噪门**通过后 **`distill_executive_memory_from_diff`（DeepSeek）** → **`memory_engine`** 追加落盘。**V8.6.1**：返回提炼条数；UI **`st.toast` / `st.success`** 反馈；记忆含 **`risk_type` / `updated_at` / `hit_count`**。

---

## 3. V3.x 人机协同（Session State）

- **草稿键**：`report_draft_{stem}`（dict，对齐 `AnalysisReport` 字段 + `_rid`）。
- **转写键**：`words_{stem}`（list of dict，词级时间戳）。
- **上下文键**：`v3_ctx_{stem}`（音频路径、JSON/HTML 路径、水印、脱敏选项、**`company_id`**）。
- **V8.6 初稿快照键**：`v3_initial_report_{stem}`（与 `report_draft_{stem}` 同形；恢复草稿时若缺失则用当前 draft 补一份，避免误把整个草稿当作「改动」收割）。
- **V8.6 看板模式**：`v86_dashboard_mode`（bool）；为真时主区仅渲染记忆库，`st.stop()` 截断后续批次 UI，**不改变**公司选择器与域字典协议。
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
- **V7.5**：`chat.completions.create` 显式 **`max_tokens`**（各模型 8192）；若整段 JSON 校验失败且疑似截断，对 **`risk_points` 数组** 做 **`JSONDecoder.raw_decode` 增量抢救**，保留已完整输出的条目。
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

- `pytest tests/`：含 `job_pipeline`、`extreme_cases`、`garbage_collector`、`test_v72_backend_override`（V7.2 覆写毒药/越界压测）、**`test_v75_formatter`**（按说话人分段 / 无 `[0]` 式导出）、**`test_v75_json_salvage`**（截断 JSON 抢救）、**`test_v76_asr_cache`**（V7.6 缓存命中 / 跳过 ASR / 落盘一致性，9 case）等。
- 转写/LLM 集成测试以 mock 为主，避免外网依赖。
- 全量回归：**74 passed**（截至 V8.0，以 `pytest tests/` 为准）。

---

*文档版本：V8.0 · 与 app.py / `job_pipeline` 当前行为对齐。*

---

## 8. V7.6 新增：ASR 缓存数据流与 UI 状态保护协议

### 8.1 ASR 内存缓存数据流

```text
[用户点击「仅提取文字稿」]
    → _file_md5(uf.getvalue())  →  asr_cache[hash] 命中？
          命中 → 直接返回 cached["plain"]                  (跳过 transcribe_audio)
          未中 → transcribe_audio(work) → 写 asr_cache[hash] = {words, plain}

[用户点击「生成报告」]
    → _file_md5(audio_path.read_bytes()) → asr_cache[hash] 命中？
          命中 → TranscriptionWord.model_validate(w) for w in cached["words"]
              → run_pitch_file_job(..., cached_words=cached_words_models)
                    ↳ 跳过 transcribe_audio；仍落盘 transcription.json（归档完整性）
          未中 → 正常 run_pitch_file_job（内部调 transcribe_audio）
              → 结束后写入 asr_cache[hash]（供下次复用）
```

缓存键：`hashlib.md5(文件内容字节).hexdigest()`（32 位十六进制）。

- **会话级**：`st.session_state["asr_cache"][hash]`，浏览器刷新或重启进程后清空。
- **磁盘级（V8.0）**：`src/disk_asr_cache.py` → 可写根下 **`.asr_cache/{hash}.json`**（原子写入）；`app.py` 在内存未命中时可加载磁盘，生成成功后回写磁盘，实现跨会话复用。

### 8.2 UI 状态保护协议（Streamlit 双 Key 隔离法）

**问题根因**：`st.data_editor(key=k)` 被渲染后，Streamlit 将 `session_state[k]` 的控制权接管（widget-managed）。
后续任何对 `session_state[k]` 的写入均触发 `StreamlitValueAssignmentNotAllowedError`。

**V7.6 解决方案——双 Key 隔离**：

| Key | 命名规则 | 用途 | 允许写入？ |
|-----|----------|------|-----------|
| `init_key` | `batch_sniper_init_{idx}` | 存放初始 DataFrame，每次 rerun 写入此处 | ✅ 可写 |
| `ed_key` | `batch_sniper_editor_{idx}` | 仅绑定 `st.data_editor(key=ed_key)` | ❌ 严禁写入 |

文件名变更触发自动填充时：
1. 更新 `init_key` 内容；
2. `del session_state[ed_key]`（若存在）→ 强制 widget 以新 `init_key` 数据重新初始化。

读取用户编辑结果：优先 `session_state.get(ed_key)`（widget 托管），兜底 `session_state.get(init_key)`。

### 8.3 V8.6 高管全息记忆（数据飞轮）

| 环节 | 行为 |
|------|------|
| 存储 | `get_writable_app_root()/.executive_memory/{safe_company_id}/{safe_tag}.json`，与 **公司档案 `company_id`**、**被访谈人 `interviewee`**（作 tag 桶）对齐 |
| 防噪 | `memory_diff_noise_gate_passes`：相对 Levenshtein **>10%** 或 **\|Δ字数\|>10** 才调用提炼 LLM |
| 提炼 | `llm_judge.distill_executive_memory_from_diff` → **DeepSeek**（`DEEPSEEK_API_KEY`）；失败捕获，静默跳过 |
| 注入 | `job_pipeline`：`load_top` → **`record_executive_memory_prompt_hits`**（磁盘 `hit_count`+`updated_at`）→ `evaluate_pitch`；**`_format_historical_profile_block`** 内再次 **weight 降序截断 5 条** |
| 看板 | **V9.0** **`get_company_dashboard_stats(company_id)`** 仅聚合该公司目录；UI **Plotly**（`plotly.express`）+ 下钻筛选；`delete` / `update_executive_memory_weight` 保留；**禁止** `data_editor` 反向写 `session_state`（铁律三） |

**接手调试清单**：收割未触发 → 查 `v3_ctx.company_id`、**非「未指定」** 访谈人、`v3_initial_report_{stem}`；提炼不落盘 → 查 **`DEEPSEEK_API_KEY`** 与 `debug.log`；命中不增 → 查 `record_executive_memory_prompt_hits` 与 tag 桶路径。

---

### 架构示意（V6.2 / V7.x 增补链路）

```text
[上传原始媒体] → (app.py 体积探针) → [智能音频网关 audio_preprocess] → [transcribe_audio]
                                                      ↓ 失败回退原文件
[显式上下文 狙击清单 JSON + 备注] ───────────────→ [llm_judge 结构化狙击 + 量化扣分引擎] → AnalysisReport

V7.6 缓存层：
[_file_md5] → asr_cache{hash: {words, plain}} → run_pitch_file_job(cached_words=...)
                                                        ↓ 命中时跳过 transcribe_audio
```
