# Skill: streamlit-state-audit
# AI Pitch Coach · Streamlit Session State 事前安全审计技能

## 技能定位

**何时调用**：
1. 新增/修改任何 `st.session_state` / `st.data_editor` / `st.text_area` 相关代码之前
2. 收到 `StreamlitValueAssignmentNotAllowedError` 或 widget 内容莫名清空时

## 三条物理定律（违反即崩）

```
定律一：用户交互 → 整个脚本重新执行
定律二：widget key 被渲染后，对应 session_state 由 Streamlit 接管，外部写入报错
定律三：widget key 消失（组件被销毁）→ 数据丢失
```

## 事前审计四项

```bash
# A：搜索同 key 反向赋值死锁
grep -n "session_state\[.*\] = st\." app.py

# B：搜索无 if 守卫的无条件写操作
grep -n "session_state\[" app.py | grep -v "if.*not in"

# C：搜索条件渲染的 widget（可能导致 key 间歇消失）
grep -n "if.*:\s*$" app.py  # 手动检查其内部是否有 key 绑定

# D：确认 _v3_clear_review_session_state 清除范围
grep -A 10 "_v3_clear_review_session_state" app.py
```

## 常见错误与修复

| 错误 | 根因 | 修复 |
|------|------|------|
| `StreamlitValueAssignmentNotAllowedError` | 同 key 既写又绑 widget | 双 Key 隔离（init_key / ed_key） |
| Widget 每次 rerun 后清空 | 无条件覆写缺少 if 守卫 | 加 `if key not in st.session_state:` |
| 点按钮后数据丢失 | UploadedFile 缓存失效 | 按钮点击前已 write_bytes 落盘 |

## 双 Key 隔离标准模板

```python
INIT_KEY = f"feature_init_{idx}"   # 写操作唯一入口
ED_KEY   = f"feature_editor_{idx}" # 仅绑定 widget，严禁写入

if INIT_KEY not in st.session_state:
    st.session_state[INIT_KEY] = initial_data()

# 需要重置时：更新 init_key，删除 ed_key 强制重新初始化
if need_reset:
    st.session_state[INIT_KEY] = new_data()
    if ED_KEY in st.session_state:
        del st.session_state[ED_KEY]

st.data_editor(st.session_state[INIT_KEY], key=ED_KEY)
```

详细诊断流程见 `.claude/commands/streamlit-state-audit.md`。

*本技能从 V7.6 data_editor 修复经验提炼，防患于未然。*
