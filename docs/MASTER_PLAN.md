# MASTER PLAN — 数据飞轮全面加固

**续接口令**：发「继续」后，我读本文件，找到第一个 `[ ]` 任务，直接开始，不重复已做的事。

---

## P0 — 本周必修（正在损坏数据）

- [x] P0.1 短名称模糊匹配修复（去后缀标准化 + 常见简称词典）✅ 425 tests passed
- [x] P0.2 institutions.json 备份机制（写前备份，保留最近3版）✅
- [x] P0.3 GitHub 同步失效告警（sync_status.json + Dashboard 红色警告）✅

## P1 — 本月（补齐飞轮闭环）

- [x] P1.1 历史数据迁移脚本（从 recording_label 逆推机构名，补写 institution_id）✅
- [x] P1.2 融资结果字段（schema + UI + analytics 存储）✅
- [x] P1.3 记忆权重衰减（90天未被 recall 降权，写入 memory_engine）✅

## P2 — 下季度（产品升级）

- [x] P2.1 会前演练模式（AI扮投资人 × 机构画像 × 实时评分）✅
- [x] P2.2 客户公司只读 Dashboard（静态 HTML 导出，Chart.js 趋势图）✅
- [x] P2.3 多页架构初建（pages/1_🎯_会前演练.py + pages/2_📤_客户报告.py）✅
      ⚠️ app.py 全量拆分（batch/review/dashboard 三页）延至 V10.4 专项 Sprint

## P3 — 长期护城河

- [ ] P3.1 结果预测模型（得分+风险分布 → 融资成功率预估）
- [ ] P3.2 投资人个人画像（Partner 级别）
- [ ] P3.3 多语言支持

---

## 当前版本
- 代码版本：V10.3（477 tests passed，P0 ✅ P1 ✅ P2.1/2.2/2.3 ✅）
- 下一个发版：V10.4（P3 或 app.py 全量拆分 Sprint）

## 关键技术决策备忘
- 短名称匹配：先去掉常见后缀（资本/基金/投资/创投）再比较，无需额外依赖
- 备份策略：.bak1/.bak2/.bak3 三轮滚动，原子写入
- sync_status.json 存 {last_attempt, last_success, last_error, consecutive_failures}
- 历史迁移：扫描 recording_label 字段，用 institution_registry.resolve() 反推
