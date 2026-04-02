# /schema-change-protocol — Schema 字段变更六联动协议

**触发时机**：每当需要修改 `src/schema.py` 中的任何字段（新增/删除/改名/改类型）时，
必须调用此技能，对照六个同步点逐一确认，否则必然出现字段不一致的运行时崩溃。

---

## 为什么 schema 改动高风险？

`src/schema.py` 是整个系统的**数据契约层**，被以下六个地方同时依赖：

```
schema.py
  ├── 1. src/llm_judge.py      ← Prompt 中要求 LLM 输出这些字段
  ├── 2. src/report_builder.py ← HTML 渲染读取这些字段
  ├── 3. app.py                ← 审查台 UI 控件与这些字段绑定
  ├── 4. src/job_pipeline.py   ← 流水线编排依赖字段名
  ├── 5. tests/                ← 测试构造 Mock 数据时硬编码字段
  └── 6. ARCHITECTURE.md       ← 文档描述字段含义
```

**漏掉任何一处 → 运行时 Pydantic ValidationError / KeyError / UI 控件失忆。**

---

## 六联动检查清单（按依赖深度排序）

### 联动点 1：`src/llm_judge.py` — Prompt 字段同步

搜索 Prompt 模板中所有字段引用：
```bash
grep -n "原字段名\|old_field_name" src/llm_judge.py
```

需要更新的位置：
- `system_prompt` 中对字段的文字说明（中文描述）
- `response_format` 约束（JSON Schema 示例）
- 字段解释与填写规范的说明段落

> ⚠️ 字段删除时尤其要检查：Prompt 里若仍要求 LLM 输出该字段，会造成幻觉堆积。

---

### 联动点 2：`src/report_builder.py` — HTML 渲染同步

```bash
grep -n "原字段名\|old_field_name" src/report_builder.py
```

需要更新：
- `generate_html_report` 中读取字段的代码
- HTML 模板字符串中对字段的引用
- `apply_asr_original_text_override` 中的字段访问

---

### 联动点 3：`app.py` — 审查台 UI 控件同步

```bash
grep -n "原字段名\|old_field_name" app.py
```

需要更新：
- `_v3_init_risk_widgets` / `_v3_init_header_widgets` 中的初始值读取
- `_v3_build_report_dict_from_widgets` 中的字段写入
- `_v3_snapshot_report_for_draft` 中的快照逻辑
- `_v3_render_single_stem_review` 中的 UI 控件绑定

---

### 联动点 4：`src/job_pipeline.py` — 流水线编排同步

```bash
grep -n "原字段名\|old_field_name" src/job_pipeline.py
```

一般较少直接引用字段，但要检查：
- `PitchFileJobParams` 是否涉及
- `run_pitch_file_job` 返回值处理

---

### 联动点 5：`tests/` — Mock 数据与断言同步

```bash
grep -rn "原字段名\|old_field_name" tests/
```

需要更新：
- 所有 `RiskPoint(...)` / `AnalysisReport(...)` 构造调用
- `model_validate({...})` 中的字典 key
- 断言语句 `assert rp.old_field == ...`

> ⚠️ 测试必须先于业务代码更新（铁律二），更新后立即 `pytest tests/ -q` 验证。

---

### 联动点 6：`ARCHITECTURE.md` — 文档同步

在 `## 1. 总览` 的契约层行追加新字段说明：
```markdown
| 契约 | `src/schema.py` | `AnalysisReport`、`RiskPoint`（含 **`新字段名`**、... |
```

---

## 执行流程

```
Step 1: grep 六处，逐一列出需修改的行号
Step 2: 更新 tests/（先于代码，铁律二）
Step 3: pytest tests/ -q → 必须全绿（此时测试应该红，因为业务代码还没改）
Step 4: 更新 schema.py
Step 5: 按联动点 1→2→3→4 顺序逐一更新业务代码
Step 6: pytest tests/ -q → 必须全绿
Step 7: 更新 ARCHITECTURE.md（联动点 6）
Step 8: 通知主理人，说明影响范围
```

---

## 高危操作清单（必须额外小心）

| 操作类型 | 风险 | 应对 |
|----------|------|------|
| 删除字段 | Pydantic 报错 + Prompt 继续要求 LLM 输出 | 联动点 1 必须同步删除字段要求 |
| 改字段名 | 所有 `rp.old_name` 引用崩溃 | 用 `grep -rn` 全局替换，不遗漏 |
| 改字段类型 | Pydantic 静默转换或验证失败 | 检查 `Literal` 枚举值在 Prompt 中是否同步 |
| 改 `default` | 旧草稿反序列化时可能失效 | 检查 `draft_manager` 落盘格式兼容性 |

---

## 快速召唤

对话中输入 `/schema-change-protocol` 即可加载此技能。
