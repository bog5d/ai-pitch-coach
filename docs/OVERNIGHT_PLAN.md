# Overnight Plan — 机构数据飞轮四件套

**目标**：主理人睡觉期间完成①②③④，明早验收。
**续接方式**：发「继续」后读本文件，3秒恢复状态。

## 进度

- [x] ① institution_registry.py — 机构主档+模糊匹配（15 tests）
- [x] ② institution_profiler.py — 机构画像聚合（10 tests）
- [x] ③ briefing_engine.py — 会前简报生成（8 tests）
- [x] ④ github_sync.py — analytics 推送 coach_data（11 tests）
- [x] UI整合 — 机构字段+模糊提示+Dashboard Tab5+会前简报按钮
- [x] pytest 全绿 406 passed + git commit (3 commits)

## ⚠️ 待主理人操作

GitHub PAT 需要开启 coach_data repo 的 **Contents: Read & Write** 权限：
GitHub → Settings → Developer settings → Personal access tokens
→ 找到该 token → Edit → Repository access → coach_data → Contents → Read and write → Save

开启后 GitHub 同步将在每次「锁定导出」时自动触发。

## 版本号
本次开发完成后发版 V10.2

## GitHub sync 配置
- repo: https://github.com/bog5d/coach_data.git
- PAT: 存 .env → COACH_DATA_GITHUB_PAT
- 目录结构: analytics/{company_id}/{stem}_analytics.json
- 注意: PAT 目前需要在 GitHub 开启 coach_data repo 的 Contents Write 权限

## 关键设计决策
1. 机构主档存 {MEMORY_ROOT}/institutions.json，格式:
   [{id: uuid, canonical_name, aliases: [], created_at, session_count}]
2. 模糊匹配阈值 0.8 (difflib.SequenceMatcher)
3. UI 拆字段: 投资机构名称(institution_name) + 项目批次(batch_label)
4. analytics 新增字段: institution_id, institution_canonical
5. 会前简报: 调用 DeepSeek，拉机构历史Top5问题+当前公司遗留坑
6. GitHub sync: 推失败静默跳过，不影响主流程
