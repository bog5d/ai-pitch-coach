# coach_data（GitHub）上传 / 下载清单与推荐操作流程

**文档用途**：给新 AI / 运维 / 多机协作同事，说明「哪些数据上云」「落在哪」「另一台电脑如何显式拉取」「如何按公司隔离」。  
**配套代码**：`src/github_sync.py`、`app.py`（数据中台 Tab「机构画像」内 expander）、`.env` 中 `COACH_DATA_*`。  
**原则**：**上传为增量推送；下载为显式动作**（避免静默全量同步带来的隐私与冲突风险）。

---

## 1. 环境配置（两台电脑必须一致）

在可写根目录下的 `.env`（与 `runtime_paths.get_writable_app_root()` 一致）配置：

| 变量 | 含义 |
|------|------|
| `COACH_DATA_GITHUB_PAT` | 对私有仓库 `coach_data` 有 `contents` 写权限的 PAT |
| `COACH_DATA_GITHUB_REPO` | 例如 `https://github.com/{owner}/coach_data.git` 或 `owner/coach_data` |

未配置时：锁定报告仍写本地；同步函数返回 false，UI 告警条会提示未配置。

---

## 2. 上传清单（锁定报告后自动尝试）

| 数据 | 仓库路径 | 本地来源 | 说明 |
|------|----------|----------|------|
| **会话分析摘要** | `analytics/{segment}/*.json` | `{归档目录}/*_analysis_report_analytics.json`（由 `analytics_exporter` 在锁定时写出） | `segment` = `analytics_repo_company_segment(company_id)`，URL-safe ASCII + 短哈希，与**侧栏项目 `company_id` 字符串**一一对应；文件名经 ASCII 安全化，**JSON 内容与本地一致** |
| **机构注册表** | `institutions/institutions.json` | 本地机构注册 JSON（`institution_registry`） | 名录 + 轻量元数据；**不是**完整画像本体 |

**注意**：画像聚合（`institution_profiler` 等）主要依赖 workspace 下递归扫描到的 `*_analytics.json` 内容字段；**机构表仅辅助解析机构 ID**。

---

## 3. 下载清单（显式拉取，不自动）

| 动作 | 入口 | 写入位置 | 范围 |
|------|------|----------|------|
| **拉取本公司 analytics** | Streamlit → **复盘数据中台** → Tab **机构画像** → expander **「coach_data：拉取本公司 analytics」** | `{工作区}/.coach_data_pull/analytics/{segment}/` | 仅当前侧栏项目的 `company_id` 对应的远端目录 |
| **可选日期过滤** | 同上 expander 内勾选 + `date_input` | 同上 | 仅保留 JSON 内 `locked_at` 或 `generated_at` 的**日期部分** ≥ 所选日 |
| **主理人全量拉取（代码 API）** | `github_sync.pull_all_analytics(dest_dir)` | 调用方指定 | 所有 `analytics/*/` 下 `.json`（**慎用**，体积与隐私需自担） |

**重要**：应用启动**不会**自动从 GitHub 拉取 analytics；B 电脑需由用户点击「拉取本公司 analytics」或自行调用 API / 定时任务。

---

## 4. 推荐操作流程（多机）

1. **A 电脑**：侧栏选对项目 → 批量分析 → 锁定报告 → 确认 `.env` 已配置 → 检查 `github_sync_status.json` 或远端 commit 是否出现 `sync analytics: ...`。
2. **B 电脑**：同一 `.env`（或等价 PAT/repo）→ 侧栏选**同一 `company_id` 的项目** → 打开数据中台 → 机构画像 → **拉取本公司 analytics**。
3. **B 电脑**：刷新机构画像 / 会前简报；`list_all_institution_profiles(workspace)` 会扫描整个工作区（含 `.coach_data_pull/...`），应能看到与 A 上传一致的 `company_id` / `institution_id` 记录。
4. **定时任务（可选）**：在 B 上 cron/计划任务调用 `pull_analytics_for_company(company_id, workspace_root)`，勿对全仓库无过滤拉取除非你是主理人聚合机。

---

## 5. 公司隔离与防串数据

- 远端目录 **`segment` 仅由 `company_id` 派生**；拉取 API **只请求该 segment**，不列举其他公司目录。
- 拉取文件落在本机 **`.coach_data_pull/analytics/{segment}/`**，与上传 segment 一致，便于对照。
- **私有化**：不同 `company_id` 应对应不同 segment；不在本功能中把多公司 raw 数据合并进同一文件。

---

## 6. 状态与排错

- 本地：`{writable_app_root}/github_sync_status.json`  
  - `channels.analytics`：analytics 通道最近成功/失败  
  - `channels.institutions`：institutions 通道  
- UI：数据中台「机构画像」顶部同步告警优先展示 **analytics** 失败原因。

---

## 7. 相关测试

- `tests/test_github_sync.py`：`analytics_repo_company_segment`、`pull_analytics_for_company`（含日期过滤 mock）
- 全量：`pytest tests/`

---

*维护：新增同步行为时同步更新本节与 `2026-04-16_FOS工程现状全景与下一步讨论底稿_给新AI.md` §17。*
