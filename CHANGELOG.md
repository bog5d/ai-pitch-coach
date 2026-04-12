# CHANGELOG — AI 路演与访谈复盘系统

所有版本变更按时间倒序记录。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [V10.4] — 2026-04-13 · FOS 资产桥接 + 启动体验修复

会前简报接入仓颉资产库，**550 passed**。

### 新增（1 项）

| # | 描述 | 位置 |
|---|------|------|
| 新增-1 | **FOS 资产桥接**（`src/asset_bridge.py`）：读取仓颉 FSS 生成的 `asset_index.json`，在会前简报末尾自动列出「📁 库中相关资产」，按风险类型关键词匹配，零 API 成本，文件不存在时静默降级 | `src/asset_bridge.py`（新建）；`src/briefing_engine.py`：导入 `build_asset_section`，两条路径（有/无历史数据）均注入资产段落 |

### 修复（1 项）

| # | 问题描述 | 修复位置 |
|---|---------|---------|
| BUG-D | `.env` 已有 API Key 时，重启应用仍显示「请配置 Key」警告、生成报告按钮灰色，需手动再点一次「保存并测试」 | `app.py`：启动时自动检测两个 Key 均已配置则静默设 `env_all_ok=True` |

### 体验优化（2 项）

| # | 描述 |
|---|------|
| UX-1 | 会前简报输入框「被访公司 company_id」改为「被访公司名称」，对非技术用户更友好 |
| UX-2 | 会前简报 caption 加「首次见某机构将自动生成通用建议版简报」提示，避免用户困惑 |

### 测试

- `tests/test_asset_bridge.py` 新增 15 个用例（load/find/briefing 集成，全 Mock，零 API 费用）

---

## [V9.6.5] — 2026-04-11 · ASR 热词 UI 升级 + 被访谈人自动填充保护（BUG-C）

日常使用流畅度提升：热词输入框升级 + 自动填充不再覆盖手动修改，**271 passed**。

### 新增 / 改进（2 项）

| # | 描述 | 修复位置 |
|---|------|---------|
| 改进-1 | ASR 热词输入框从 `st.text_input` 升级为带折叠的 `st.text_area`（height=72），显示实时词数，移至批量分析区顶部独立折叠块，使用体验更清晰 | `app.py`：热词 expander 移至 `uploaded_list` 循环前 |
| BUG-C | 上传新文件时，自动猜测的「被访谈人」会静默覆盖用户已手动修改的内容 | `src/audio_filename_hints.py`：新增 `should_autofill_iv()` 纯函数；`app.py`：自动填充前调用保护逻辑，记录 `_batch_iv_autofilled_{idx}` 上次自动值 |

### 测试

- `tests/test_audio_filename_hints.py` 新增 5 个 `should_autofill_iv` 测试用例（共 11 个）

---

## [V9.6.4] — 2026-04-11 · ASR 落盘异常防护（稳定性红蓝对抗审计）

稳定性审计发现两处无异常保护的崩溃路径，**266 passed**。

### 修复（2 项）

| # | 问题描述 | 根因 | 修复位置 |
|---|---------|------|---------|
| BUG-A | 阿里云转写结果 URL 返回 HTML 错误页时抛裸 `JSONDecodeError`，整条 ASR 流程崩溃 | `_fetch_json_from_url` 无任何 try/except | `src/transcriber.py:_fetch_json_from_url`：网络错误 + JSON 解析异常统一包装为 `RuntimeError`，含中文可读提示 |
| BUG-B | 磁盘空间不足/权限不够时 `analysis_json` 写入失败，抛裸 `OSError`，可能留下损坏文件 | `job_pipeline.py:write_text` 无保护 | `src/job_pipeline.py`：写入包装 `try/except OSError → RuntimeError`，含路径和处理建议 |

### 测试

- `tests/test_bugfix_stability.py` 新增 `TestFetchJsonFromUrl`（3 cases）、`TestAnalysisJsonWriteProtection`（1 case）

---

## [V9.6.3] — 2026-04-11 · 审查台双缺陷修复（同事测试反馈）

针对 V9.6.2 同事测试发现的两个审查台体验问题精准修复，**262 passed**。

### 修复（2 项）

| # | 问题描述 | 根因 | 修复位置 |
|---|---------|------|---------|
| Fix-A | 问题背景只显示约 40 字，后半句被截断看不全 | `_extract_tier1_summary` 上限硬编码 40 字，tier1 首句多为 50-80 字 | `app.py:_extract_tier1_summary`：上限 40→100 字 |
| Fix-B | 专家视图打开后 Tier1/Tier2/扣分原因三框全空白 | 精炼操作或旧草稿将 session_state 中 `_t1/_t2/_ded` 写为 `""`；`_v3_init_risk_widgets` 检测到 key「已存在」便跳过初始化；widget 从 session_state 读到空字符串显示空白 | `app.py:_v3_init_risk_widgets`：新增空值安全回补逻辑（仅在 toggle 未激活时从 rp 回填） |

---

## [V9.6.2] — 2026-04-10 · 工业级稳定性十修（红蓝对抗 / TDD）

在 V9.6.1 之上，对 ASR 兜底、时间戳、缓存润色、路径长度、阶段一截断可感知性、Streamlit 竞态、错题本幂等、指挥中心 IO、冲突检测误报等 **10 项** 做加固；全量 **`pytest tests/` → 262 passed**（Mock 外部 API）。

### 修复摘要

| 领域 | 要点 |
|------|------|
| 转写 | DashScope 任务轮询 **POST→GET**；`_coerce_seconds_pair`：`begin_time`/`end_time` 固定按毫秒；`start_time` 启发式阈值 **86400s** 防长录音误判 |
| 流水线 | **`cached_words` 命中时跳过 `polish_transcription_text`**（防二次润色漂移）；**`safe_fs_segment` 200 字截断**（防 Windows `MAX_PATH`） |
| LLM | 阶段一 salvage 后 **`total_score_deduction_reason` 持久化截断提示**；**`detect_logical_conflict`** 5 字起窗 + 总告警 **≤3** |
| 记忆 / UI | **`append_executive_memory`** `raw_text` 幂等；**`get_company_dashboard_stats(..., pre_loaded_pairs=)`** 消双倍读盘；**`_v3_build_report_dict_from_widgets`** `.get` 防 KeyError |

### 测试

- 新增 / 扩展：**`tests/test_bugfix_stability.py`**（32 cases）、**`tests/test_v76_asr_cache.py`**（缓存跳过润色）等。

---

## [V9.6.1] — 2026-04-09 · 稳定性四连修（同事反馈专项）

V9.6 生产运行后同事反馈四类稳定性问题，本版本为外科手术修复，零新功能引入，228 passed（全绿）。

### 修复（4 项）

| # | 问题描述 | 根因 | 修复位置 |
|---|---------|------|---------|
| Fix 1 | 多音频批处理时，第二个及后续文件音频无声音、无法播放 | `app.py` 将 `work_audio`（网关压缩临时文件，ASR 后已 `unlink`）存入 session_state，而非原始 `audio_path` | `app.py`：`v3_ctx_{stem}["audio_path"]` 改为存原始 `audio_path` |
| Fix 2 | 审查台翻车片段信息过载，8 个字段一屏堆砌，视觉疲劳 | expander 内平铺所有字段，专业字段对非技术用户无价值 | `app.py`：重构为"4 字段默认展示 + 专家视图 `st.toggle` 折叠"；新增 `_extract_tier1_summary` 提取首句摘要；Prompt 强制 tier1 首句 ≤25 字动词开头 |
| Fix 3 | 偶发"0分崩溃"——LLM 输出 30+ 个风险点，Pydantic 校验失败/总分崩 | Prompt 无数量上限约束，LLM 自由发挥 | `src/llm_judge.py`：Prompt 追加"严重 ≤3 / 一般 ≤4 / 轻微 ≤3 / 总计 ≤10"硬约束 + 质量门槛 |
| Fix 4 | 审查台出现全空白翻车片段卡片 | LLM 输出空壳 `RiskPoint`（tier1/improvement 均为空字符串），未过滤直接落盘 | `src/llm_judge.py` + `src/job_pipeline.py`：新增 `_is_valid_risk_point` 守门函数，空壳条目在落盘前过滤 |

### 新增测试（4 文件，20 cases）

- `tests/test_fix1_audio_path.py`（3 cases）：gateway 删除后原始音频仍存在，小文件路径正确，多文件互不干扰
- `tests/test_fix2_tier1_summary.py`（7 cases）：Prompt 格式约束 + `_extract_tier1_summary` 六边界
- `tests/test_fix3_risk_count_constraint.py`（4 cases）：Prompt 含数量上限 / 严重上限 / 质量门槛 / 禁止凑数
- `tests/test_fix4_empty_risk_filter.py`（6 cases）：空 tier1 / 空白 tier1 / 空 improvement / 有效通过 / 空壳 salvage / 有效 salvage 保留

---

## [V9.6] — 2026-04-03 · 两阶段深评 · 并发硬化版

V9.1 稳定后的全面进化：Cursor 搭建三大新架构（两阶段评估引擎、ASR 轻量润色管道、魔法对话框 API），总工程师完成深度审计并修复 4 处技术隐患，全量测试从 190 → 208 passed（零回归破坏）。

### 新增（Cursor 架构）

#### 两阶段深评引擎（`src/llm_judge.py`）
- **阶段一 `scan_risk_targets`**：轻量扫描全文，输出 `RiskScanResult`（`SceneAnalysis` + `RiskTargetCandidate[]`），不写 Tier/话术
- **阶段二 `deep_evaluate_single_risk`**：对每个靶点单独召唤 LLM，聚焦片段做完整 `RiskPoint` 深评
- 索引强制校验：深评结果的 `start/end_word_index` 由系统强制覆写，消除 LLM 偏移幻觉

#### ASR 轻量润色（`src/asr_polish.py`，新文件）
- `polish_transcription_text`：转写后调用 DeepSeek 做错别字 + 行业术语纠正
- `apply_asr_polish_payload_to_words`：按 `word_index` 字典映射，**只改 text，时间戳严格不变**
- 索引集合不一致时自动降级返回原列表（词级安全保证）
- `job_pipeline.py` 新增 `skip_asr_polish` 开关，默认开启

#### 魔法对话框（`refine_single_risk_point`）
- 按主理人一句话指令局部重写单条 `improvement_suggestion`
- 返回不可变 `MagicRefinementResult`，纯函数式，无状态污染

#### 新数据契约（`src/schema.py`）
- `RiskTargetCandidate`：阶段一靶点（位置 + 问题摘要 + 风险类型）
- `RiskScanResult`：阶段一出参（`SceneAnalysis` + `targets[]`）
- `MagicRefinementResult`：魔法对话框出参（`risk_point_id` + `improvement_suggestion`）

### 硬化修复（总工程师审计，4 处隐患）

| # | 位置 | 问题 | 修复 |
|---|------|------|------|
| Bug1 | `asr_polish.py` | LLM 返回重复 `word_index` 时静默覆盖（不降级） | 引入 `seen_indices` 集合，检测到重复立即降级 |
| Bug2 | `llm_judge.py` | 深评串行 `for` 循环（10靶点≈50秒） | `ThreadPoolExecutor(max_workers=6)` 并发，结果按原始顺序排列 |
| Bug3 | `llm_judge.py` | `refine_single_risk_point` 的 `context_text` 无长度上限 | 4000 字截断保护，防 token 超限 |
| Bug4 | `llm_judge.py` | 阶段一 `RiskScanResult` JSON 截断直接抛 `ValueError` | 新增 `_salvage_risk_scan_result`：降级返回空靶点列表而非崩溃 |

### 测试

- **208 passed**（较 V9.1 的 190 新增 18 个用例）
- `tests/test_v96_asr_polish.py`（9 cases）：正常映射 + 5 个极端边界（合并词/删除词/重复索引/多余词/不连续索引）
- `tests/test_v96_two_stage.py`（6 cases）：两阶段链路 + 多靶点并发 + 单靶点失败跳过 + 阶段一截断降级
- `tests/test_v96_magic_refinement.py`（3 cases）：正常返回 + 空指令校验 + 超长 context 截断

---

## [V9.1] — 2026-04-03 · 三大顽疾根治版（P0/P1/P2 外科手术）

V9.0 由 AI 工具（Cursor Composer）快速搭建，功能完整但遗留三个底层 Bug。本版本为总工程师级审计后的外科手术修复，TDD 驱动，零回归破坏（190 passed）。

### 修复

#### P0（致命）— DataFrame `or` 运算符崩溃
- **文件**：`app.py`，`_batch_sniper_targets_json`
- **根因**：`df = state.get(result_key) or state.get(init_key)` 对 DataFrame 调用 `__bool__()` → `ValueError: The truth value of a DataFrame is ambiguous`
- **修复**：`or` → `is None` 判断，安全选取优先级更高的 result DataFrame
- **新增测试**：`tests/test_v90_sniper_dataframe_bug.py`（13 cases，含 DataFrame or 文档化 + 端到端序列化验证）

#### P1（性能）— 缓存命中时仍执行 FFmpeg 压缩 + 临时文件堆积
- **文件**：`app.py`，生成报告主循环
- **根因**：`_file_md5(raw_bytes)` 和缓存检查代码在 `smart_compress_media` 之后执行，导致缓存命中场景也白白跑 CPU 密集型压缩
- **修复 1**：重排执行顺序 → 算 hash → 查内存缓存 → 查磁盘缓存 → 仅未命中才启动 FFmpeg
- **修复 2**：**阅后即焚** — `gw_compressed` 在 `run_pitch_file_job` 入库完成后立即 `unlink`，防止批量分析时 `_v62_asr_gateway.mp3` 堆积撑爆磁盘
- **新增测试**：`tests/test_v90_pipeline_reorder.py`（17 cases，含 MD5 一致性、缓存命中不调 compress 的 mock 断言、阅后即焚顺序验证）

#### P2（体验）— 文字稿无标点无说话人分段（三联修复）
- **文件**：`src/transcriber.py`
- **根因 A**：SiliconFlow SenseVoiceSmall 词级 token 无标点（标点在 segment.text 中），旧代码仅取词级字段
- **根因 B**：阿里云 Paraformer API 缺少 `diarization_enabled: True` 参数，服务端不返回 `spk_id`
- **根因 C**：`format_transcript_plain_by_speaker` 仅在说话人切换时换行，同说话人一坨不拆
- **修复 1**（阿里云参数）：`_dashscope_submit_transcription_rest` 加 `"diarization_enabled": True`
- **修复 2**（阿里云标点）：`_map_aliyun_paraformer_to_schema` 将 `sentence.text` 末尾标点追加到句末词 `text` 字段（仅改 text，timestamps 严禁变动）
- **修复 3**（SiliconFlow 标点）：新函数 `_build_siliconflow_segment_punct_map(payload)` 构建 `{有效词全局索引: 标点}` map；`_map_siliconflow_to_schema` 新增 `punct_map` 可选参数，`transcribe_siliconflow` 调用时自动传入
- **修复 4**（显示格式）：`format_transcript_plain_by_speaker` 遇词末句终标点插 `\n`（段内句间）；说话人间保持 `\n\n`
- **对齐免疫**：`start_time` / `end_time` / `word_index` 在所有路径严禁变动（7 个专项测试覆盖）
- **新增测试**：`tests/test_v90_transcript_quality.py`（23 cases）

### 全量回归

- **190 passed**（较 V9.0 的 137 passed 新增 53 个测试用例）

---

## [V9.0] — 2026-04-03 · 全景机构画像与数据指挥中心

本版本把 **V8.6.x 水下飞轮** 托出水面：**仅按当前选中 `company_id`** 聚合 `.executive_memory`，与 V8.4 公司域字典一致；指挥中心采用 **Plotly** 交互图（条形 + 环形 + 可选日分布折线）与 **四卡 KPI**，下钻表支持 **高管 / 风险类型** 筛选，并 **保留删除与调权重** 闭环。依赖新增 **`plotly`**。

### 新增

- **`get_company_dashboard_stats(company_id)`**（`memory_engine.py`）：`total_memories`、`active_executives`（去重 `tag`）、`risk_distribution`、`executive_hit_trends`（`by_executive`、`daily_activity`）、`total_hit_count`、`last_updated_at`。
- **`tests/test_v90_aggregator.py`**：空公司、`__new__`、双公司隔离、零指针安全结构。

### UI

- 看板标题 **全景机构画像 · 数据指挥中心**；`st.container(border=True)` 包裹 KPI（低版本自动降级）。
- **Plotly Express**：`template="plotly_white"`、企业蓝主色；环形图 `hole=0.4`，中心 **最高频雷区前两字** 缩写。

### 全量回归

- **137 passed**

---

## [V8.6.1] — 2026-04-03 · DeepSeek 统一提炼 + 记忆进化与收割反馈

### 修复（红蓝）

- **剔除 Anthropic / Haiku**：错题提炼 **仅走 DeepSeek**（`deepseek-chat`），与主评委同一底座；`ROUTER` 不再包含 `haiku`；`.env.example` 仅保留 DashScope + DeepSeek。

### 新增

- **`ExecutiveMemory`**：`risk_type`（严重/一般/轻微，来自锁定稿风险点）、`updated_at`（UTC Z）、`hit_count`（主评 Prompt 命中次数）。
- **`record_executive_memory_prompt_hits`**：注入 Top 记忆后同桶写回 `hit_count` + `updated_at`。
- **`top_risk_type_counts_for_company`**：看板 **Top3 高频雷区**（metric + 进度条；若 Streamlit 支持则追加 `st.pills`）。
- **锁定成功反馈**：`st.toast`（或降级 `st.success`）提示本轮提炼条数。

### 测试

- `tests/test_v861_memory_evolution.py`：DeepSeek 提炼路由、字段落盘、命中计数、雷区聚合。

### 全量回归

- **133 passed**

---

## [V8.6] — 2026-04-03 · 数据飞轮与高管全息记忆版

本版本实现了**业务经验的无感沉淀与可视化管理**：审查台锁定导出时，系统在后台静默对比「AI 初稿」与「终稿」，仅在改动足够显著时调用轻量模型提炼一条可复用记忆；下次同公司、同被访谈人标签复盘时，**按权重自动选取最多 5 条**注入评委 Prompt，像给团队配了一位「记得住往期翻车点」的专属教练。侧栏一键进入**高管数字记忆库**，主理人可清点沉淀条数、删除伪经验、调节权重——AI 不再只是单次工具，而是**可伴随组织进化的数字智库**。

### 新增

- **`ExecutiveMemory` 错题本契约**（`src/schema.py`）：`uuid`、`tag`、`raw_text`、`correction`、`weight`。
- **`src/memory_engine.py`**：按 `company_id` 子目录 + `tag` JSON 桶原子落盘；**防噪门**（相对编辑距离 >10% 或字数差 >10 才进入提炼）；`capture_and_distill_diff`、`append` / `delete` / `update_executive_memory_weight`、全公司枚举与计数；兼容早期 `_default` 扁平遗留文件。
- **`distill_executive_memory_from_diff`**（`src/llm_judge.py`）：**V8.6.1 起固定 DeepSeek**；初版曾误接 Haiku，已在 8.6.1 剔除。
- **`<HISTORICAL_PROFILE>` Prompt 块**：紧随 `<COMPANY_BACKGROUND>` 之后；`_format_historical_profile_block` 内二次按 **weight 降序截断 Top 5**，防止 Token 爆炸。
- **`PitchFileJobParams.memory_company_id`** + **`evaluate_pitch(..., historical_memories=)`**：流水线在「公司 ID + interviewee 均非空且非占位 **未指定**」时加载 Top 记忆并注入。
- **`app.py`**：`v3_initial_report_{stem}` 初稿快照；`v3_ctx_{stem}.company_id`；锁定成功 `_v86_harvest_finalize_if_needed`；侧栏 **📊 高管数字记忆库** + 独立 Dashboard（指标、清单、`st.dataframe`、删除/调权重）；**不**使用 `data_editor` 反向写 session，避免铁律三风险。
- **测试**：`tests/test_v86_memory_engine.py`、`test_v86_harvester.py`、`test_v86_injector.py`（含 pipeline 透传）；全量 **Mock**，零真实 LLM/ASR。

### 改动

- ~~`ROUTER` 增加 `haiku`~~（**V8.6.1 已移除**，统一 DeepSeek。）

### 全量回归

- **127 passed**

---

## [V8.4] — 2026-04-02 · 公司档案与上下文注入版

### 新增

#### 模块一：公司档案管理（`src/company_profile.py` 新模块）
- **`.company_profiles/` JSON 存储**：每家公司对应独立 `.json` 文件，肉眼可读，支持手动兜底修改。
- **原子写入**（`tmp + os.replace`）：防止崩溃产生损坏档案。
- **CRUD 全集**：`list_companies`、`load_company`、`save_company`、`delete_company`，均支持 `profiles_dir` 参数（测试友好）。
- **`uuid` 唯一标识符**：每个公司档案自动生成全局唯一 UUID（`uuid4`），用于跨系统追踪，可手动覆盖。

#### 模块二：LLM Prompt 公司背景注入（`src/llm_judge.py`）
- **`<COMPANY_BACKGROUND>` 注入块**：非空背景时自动插入 `</KNOWLEDGE_BASE>` 之后、`<TASK>` 之前，权重高于通用知识库、低于本次狙击指令。
- **独立 8k 字数池**：`truncate_company_background` 函数头部优先截断，超限时 UI 黄字警告。
- **冲突仲裁规则**：写入 `<CONSTRAINTS>` — `<SNIPER_TARGETS>` 与 `<COMPANY_BACKGROUND>` 矛盾时以前者为准，并在 `deduction_reason` 中注明差异。
- **`logical_conflict` 冲突报警机制**：`detect_logical_conflict(background, sniper_json)` 检测背景与狙击目标的关键词重叠，返回警告列表。

#### 模块三：状态隔离与 UI（`app.py`）
- **域字典隔离**：`st.session_state['current_company_cache']` 作为公司级命名空间，切换公司时 `= {}` 整体清空，严禁遍历删除 UI-bound key（铁律三）。
- **侧边栏公司选择器**：位于侧边栏最顶部，作为 session 最高作用域；支持从已有档案选择或新建公司。
- **实时冲突警告**：若当前公司背景与已填写的狙击清单存在关键词重叠，侧边栏弹出 `⚠️ 背景与狙击目标潜在冲突` 折叠面板。

#### 模块四：TDD 测试（`tests/test_v84_company_profile.py`）
- 28 个测试覆盖：CompanyProfile 模型（5）、CRUD（8）、llm_judge 注入与冲突检测（11）、pipeline 透传（3）、域字典切换（1）。

### 改动
- `src/schema.py`：新增 `CompanyProfile` Pydantic 模型（含 `uuid` auto-factory）。
- `src/llm_judge.py`：新增 `MAX_COMPANY_BG_CHARS`、`truncate_company_background`、`detect_logical_conflict`；`_build_system_prompt` 和 `evaluate_pitch` 新增 `company_background` 参数。
- `src/job_pipeline.py`：`PitchFileJobParams` 新增 `company_background: str = ""`；`run_pitch_file_job` 截断 + 透传。
- `app.py`：导入 `company_profile`、`CompanyProfile`、`detect_logical_conflict`；域字典初始化；侧边栏选择器；`PitchFileJobParams` 注入 `company_background`。

### 全量回归
- **≥102 passed**（较 V8.0 新增 28 个测试）

---

## [V8.3] — 2026-04-02 · 生产级三大修复版

### 修复

#### Bug 1：领域幻觉根治 — `<DOMAIN_ANCHOR>` 行业铁律注入
- 在 `src/llm_judge.py::_build_system_prompt` 的 System Prompt 最顶层插入 `<DOMAIN_ANCHOR>` 块，优先级高于所有其他指令。
- 明确宣告系统专用领域：硬科技 / 深科技 / 军工国防 / 低空经济 / 先进制造 / 半导体。
- 列出 7 个高频歧义词的强制技术解释（指控→C2，火控→Fire Control System，靶场/制导/预警/攻击/载荷）。
- 设置 3 条绝对红线：禁止无根据引入法律叙事、禁止捏造机构/产品/人名、不确定时优先假设为技术术语。

#### Bug 2：结构化狙击清单静默失效 — `result_key` 修复
- **根因**：`st.data_editor(key=ed_key)` 在 `st.session_state[ed_key]` 存储的是 Streamlit delta dict（含 edited_rows/added_rows/deleted_rows），而非 DataFrame；`_batch_sniper_targets_json` 读取后因 `hasattr(delta_dict, "iterrows") == False` 直接返回 `"[]"`，狙击指令永远不进 LLM。
- **修复**：渲染层捕获 `st.data_editor` 返回值（完整 DataFrame）写入 `batch_sniper_result_{idx}`（非 widget key，铁律三安全）；`_batch_sniper_targets_json` 优先读 `result_key`，兜底读 `init_key`。

#### Bug 3：阿里云转写无标点 — Paraformer API 参数补全
- 在 `src/transcriber.py::_dashscope_submit_transcription_rest` 的 `parameters` 中新增：
  - `"enable_punctuation_prediction": True`：开启标点预测，文字稿自动断句。
  - `"disfluency_removal_enabled": True`：过滤"啊""那个"等语气词，提升阅读流畅度。
- 标点作为词级 token 由 Paraformer 生成，时间戳精确，音频切割对齐不受影响。

### 全量回归
- **74 passed**（与 V8.0 持平；本次修复无新增接口，无需新增测试）

---

## [V8.0] — 2026-04-02 · 生产级协作与精准解析版

### 新增

#### 模块一：转录精度与成本底座
- **磁盘级 ASR 缓存**（`src/disk_asr_cache.py` 新模块）：
  - 按文件内容 MD5 哈希存取，缓存存于 `{writable_root}/.asr_cache/{md5}.json`。
  - 原子写入（`tmp + os.replace`），防写入中途崩溃产生损坏文件。
  - 主流程升级为**三级缓存**：L1 内存缓存（本次 session）→ L2 磁盘缓存（跨 session 永久）→ L3 云端 ASR。
  - 实现「一次转写，永久免费秒开」：同一录音文件无论隔多久再次使用均命中磁盘缓存，零 API 费用。
- **项目专属热词库**（`app.py` 上传区、`transcriber.py`、`job_pipeline.py`）：
  - 上传区新增文本框「项目专属专有名词」，支持中英文逗号/分号分隔多词。
  - `transcriber.transcribe_siliconflow` 将热词拼接为 `initial_prompt` 注入 multipart form（最优努力）。
  - `PitchFileJobParams` 新增 `hot_words` 字段；`run_pitch_file_job` 透传给 ASR 层。

#### 模块二：双剑合璧的三道防线审查台
- **第二道防线：局部精炼引擎**（`src/schema.py`、`src/llm_judge.py`、`app.py`）：
  - `RiskPoint` 新增 `needs_refinement: bool` 与 `refinement_note: str` 字段（默认 false/""，LLM 约束不输出）。
  - `llm_judge.refine_risk_point()`：对单个风险点调用专用精炼 Prompt，注入主理人批示意见；强制保留词索引。
  - 审查台每个风险点 expander 内新增「🔬 标记需精炼」复选框 + 「批示意见」输入框。
  - 新增**「🔬 局部重写全部选中项」**批量按钮：收集全部勾选条目，顺序调用 LLM，精炼结果通过 `refine_pending_{rid}` 中转 key 在下次 rerun 安全注入（双 Key 隔离模式推广）。
- **第三道防线：AI 润色遗漏痛点**（`src/llm_judge.py`、`app.py`）：
  - `llm_judge.polish_manual_risk_point()`：将主理人原始文字描述结构化为标准 `RiskPoint`，强制 `is_manual_entry=True`，`start/end_word_index=0`。
  - 审查台「➕ 新增遗漏痛点」升级：新增**「✨ AI 润色后插入」**按钮，一键将人工描述润色为专业风险点格式并无缝插入报告。

#### 模块三：透明厨房进度与生产级防御
- **透明厨房进度**：`st.status` 进度标签区分「内存缓存命中」/「磁盘缓存命中」/「云端转写」三态，主理人实时感知每条录音所走的路径。
- **状态机安全保障**：精炼结果注入通过 `v3rp_refine_pending_{stem}_{rid}` 中转 key + `_v3_init_risk_widgets` 处理的双 Key 安全模式，确保精炼按钮点击不丢失其他条目已有的手动编辑。

#### 模块四：TDD 测试
- `tests/test_v80_disk_cache.py`（8 case）：保存/读取、未命中、不同 hash 独立、自动创建目录、原子覆写。
- `tests/test_v80_hot_words.py`（7 case）：热词透传 SiliconFlow / Aliyun 降级 / pipeline 穿透 / initial_prompt 注入。
- `tests/test_v80_refinement.py`（11 case）：refine_risk_point 调用 LLM、返回 RiskPoint、保留词索引、批示注入 prompt、无效 JSON 异常；polish_manual 空描述 ValueError、is_manual_entry=True、索引为零。

### 改动
- `src/schema.py`：`RiskPoint` 新增 `needs_refinement`、`refinement_note` 两字段（均有默认值，向后兼容）。
- `src/llm_judge.py`：主 Prompt `<CONSTRAINTS>` 新增约束行：LLM 输出 `needs_refinement` 仅允许 false、`refinement_note` 仅允许 ""。
- `src/transcriber.py`：`transcribe_siliconflow`、`transcribe_aliyun`、`transcribe_audio` 均新增 `hot_words: list[str] | None = None` 关键字参数（向后兼容）。
- `src/job_pipeline.py`：`PitchFileJobParams` 新增 `hot_words` 字段；`run_pitch_file_job` 将其透传给 `transcribe_audio`。
- `app.py`：导入 `disk_asr_cache`、`refine_risk_point`、`polish_manual_risk_point`；上传区加热词输入框；主流程三级缓存重构；`_v3_init_risk_widgets` 加 pending refinement 安全注入；`_v3_build_report_dict_from_widgets` 加新字段；审查台 UI 全面升级。

### 全量回归
- **74 passed**（截至 V8.0，较 V7.6 新增 26 个测试）

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

*最后更新：V8.4 发版，2026-04-02。*
