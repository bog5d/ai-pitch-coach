# Overnight Plan — 机构数据飞轮四件套

**目标**：主理人睡觉期间完成①②③④，明早验收。
**续接方式**：发「继续」后读本文件，3秒恢复状态。

## 进度

- [ ] ① institution_registry.py — 机构主档+模糊匹配
- [ ] ② institution_profiler.py — 机构画像聚合
- [ ] ③ briefing_engine.py — 会前简报生成
- [ ] ④ github_sync.py — analytics 推送 coach_data
- [ ] UI整合 — 机构字段+模糊提示+Dashboard Tab5+会前简报按钮
- [ ] pytest 全绿 + git commit

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
