# /streamlit-state-audit — Streamlit Session State 事前安全审计

**触发时机**：
- 每次新增或修改任何与 `st.session_state` / `st.data_editor` / `st.text_area` / `st.number_input` 相关的代码之前。
- 收到「Streamlit 红框报错」时，立即调用此技能定位根因，而不是凭感觉猜测。

---

## 核心心智模型：Streamlit 状态机的三条物理定律

```
定律一：每次用户交互 → 整个 Python 脚本从头重新执行（rerun）
定律二：widget 被渲染后，其 key 对应的 session_state 由 Streamlit 接管，禁止外部写入
定律三：组件被销毁（key 消失）→ 对应 session_state 数据丢失
```

违反任意一条，就会出现：状态丢失、`StreamlitValueAssignmentNotAllowedError`、组件失忆。

---

## 审计清单：新增 UI 代码时逐项过

### 检查项 A：是否存在"同 Key 反向赋值"死锁

```bash
# 在 app.py 中搜索可能的死锁模式
grep -n "session_state\[.*\] = st\." app.py
grep -n "session_state\[.*\] = .*editor\|session_state\[.*\] = .*input\|session_state\[.*\] = .*area" app.py
```

**危险模式（必须修复）**：
```python
# ❌ 死锁：先写 key → 再用同 key 绑 widget → 下次 rerun 写操作触发报错
st.session_state["my_key"] = some_df          # 写
st.data_editor(some_df, key="my_key")         # 同 key 绑 widget → 死锁

# ❌ 死锁变体：normalize 写回同 key
st.session_state[ed_key] = normalize(st.session_state[ed_key])  # 若 ed_key 已绑 widget
```

**安全模式（双 Key 隔离）**：
```python
# ✅ init_key 只用于写，ed_key 只用于绑 widget
INIT_KEY = f"sniper_init_{idx}"
ED_KEY   = f"sniper_editor_{idx}"

if INIT_KEY not in st.session_state:
    st.session_state[INIT_KEY] = build_df()
st.session_state[INIT_KEY] = normalize(st.session_state[INIT_KEY])  # 写 init_key：安全

st.data_editor(st.session_state[INIT_KEY], key=ED_KEY)  # 读 init_key 传数据，ED_KEY 只绑定
```

---

### 检查项 B：rerun 后状态是否还在？

对每个 `session_state` 写操作，问：

| 问题 | 正确做法 |
|------|----------|
| 这个 key 是在按钮回调里写的吗？ | ✅ 按钮回调内写：rerun 后保留 |
| 这个 key 是在脚本顶层写的吗（无条件写）？ | ⚠️ 每次 rerun 都会重写，慎用 |
| 用 `if key not in st.session_state:` 守卫了吗？ | ✅ 有守卫：只初始化一次 |
| 这个状态依赖 UploadedFile 对象吗？ | ⚠️ UploadedFile 可能在 rerun 后缓存失效，要提前落盘 |

---

### 检查项 C：组件 key 是否会"意外消失"

```python
# ⚠️ 危险：条件渲染导致 widget key 间歇性消失 → session_state 数据丢失
if some_condition:
    st.text_area("备注", key="batch_note_0")   # 条件为 False 时此 key 消失
```

对策：
- 关键业务数据不依赖条件渲染的 widget 保存；改用 `st.session_state` 显式存储。
- 若必须条件渲染，在 key 消失前将值手动复制到另一个持久 key。

---

### 检查项 D：`_v3_clear_review_session_state` 会清掉意外的 key 吗？

```bash
grep -A 10 "_v3_clear_review_session_state" app.py
```

确认清除范围（前缀）是否覆盖了新增的 key，或者是否误清了不该清的 key。

---

## 收到报错时：快速定位根因

### 错误：`StreamlitValueAssignmentNotAllowedError`

```
原因：试图对一个已被 widget 托管的 session_state key 执行赋值操作。

诊断步骤：
1. 找报错 key 名称（错误信息中有）
2. grep -n "session_state\['{key}'\] =" app.py → 找所有写操作行
3. grep -n "key='{key}'" app.py → 找 widget 绑定行
4. 确认是否同一个 key 既被写入又被 widget 绑定
5. 修复：拆成 init_key（写）+ ed_key（绑 widget）
```

### 错误：widget 内容每次 rerun 后清空

```
原因：每次 rerun 都无条件覆写了 session_state，或 init 守卫缺失。

诊断步骤：
1. 找该 widget 的 key
2. grep -n "session_state\['{key}'\] =" app.py
3. 检查是否在脚本顶层（非 if 守卫内）无条件赋值
4. 修复：加 `if key not in st.session_state:` 守卫
```

### 错误：用户编辑后点击按钮数据丢失

```
原因：UploadedFile 缓存失效 或 关键数据未在按钮点击时及时落盘。

诊断步骤：
1. 检查数据是否来自 UploadedFile（app.py 已用 write_bytes 提前落盘）
2. 检查数据是否在按钮 callback 执行完后仍在 session_state
3. 若依赖 widget 返回值（edited_df）：确认是用返回值还是用 session_state[key] 读
```

---

## 快速召唤

对话中输入 `/streamlit-state-audit` 即可加载此技能。
