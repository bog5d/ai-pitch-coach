# Skill: schema-change-protocol
# AI Pitch Coach · Schema 字段变更六联动协议

## 技能定位

**何时调用**：修改 `src/schema.py` 中任何字段（新增/删除/改名/改类型）之前，必须调用此技能。

## 六联动地图

```
schema.py 改动
  ├── 1. llm_judge.py     → Prompt 字段描述 & JSON 示例
  ├── 2. report_builder.py → HTML 渲染读取
  ├── 3. app.py            → 审查台 UI 控件绑定
  ├── 4. job_pipeline.py   → 流水线字段引用
  ├── 5. tests/            → Mock 数据硬编码字段
  └── 6. ARCHITECTURE.md  → 文档描述
```

## 快速搜索命令

```bash
# 把 OLD_FIELD 替换为实际字段名
grep -rn "OLD_FIELD" src/ app.py tests/ ARCHITECTURE.md
```

## 执行顺序

1. grep 六处 → 列出所有需修改的行
2. **先改 tests/**（铁律二：测试先行）
3. `pytest tests/ -q` → 此时应红（业务代码还没改）
4. 改 schema.py
5. 按联动点 1→2→3→4 改业务代码
6. `pytest tests/ -q` → 必须全绿
7. 改 ARCHITECTURE.md

## 高危字段操作

- **删除字段** → 必须同步从 Prompt 中删除要求 LLM 输出该字段
- **改字段名** → 用 `grep -rn` 全局替换，一个都不能漏
- **改 Literal 枚举** → Prompt 示例中的枚举值也要同步

详细清单见 `.claude/commands/schema-change-protocol.md`。

*本技能基于 V7.x 多次 schema 演进经验提炼。*
