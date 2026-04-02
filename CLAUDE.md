# CLAUDE.md — AI Pitch Coach 最高行动宪法

本文件是 Claude Code 每次启动后**优先级最高**的约束文件，高于任何对话指令。
改业务代码前，必须确认以下四大铁律均未被违反。

---

## 铁律一：红蓝对抗防御意识（Defensive Programming）

在编写任何核心逻辑前，必须先进行红蓝对抗推演：

- **大模型幻觉预判**：LLM 可能返回洗稿文本（original_text 与 ASR 不符）、JSON 截断、字段缺失、嵌套错误。每个 LLM 调用点必须有明确的降级策略。
- **UI 状态丢失预判**：Streamlit rerun 机制会在任何交互后重新执行整个脚本。`session_state` 中的数据可能因 key 不一致、组件销毁、页面刷新而丢失。写 UI 逻辑前必须先问：「这个状态在 rerun 后还在吗？」
- **边界情况预判**：空音频、零风险点、超长转写、API 超时、文件不存在——每个输入路径都要有明确处理。

---

## 铁律二：严格的 TDD 测试驱动

**无测试，不写代码。**

- 任何后端逻辑改动（`src/*.py`、`job_pipeline.py` 等），必须**先**在 `tests/` 下补充测试用例，测试通过后方可修改 UI。
- 测试粒度：新增函数 → 新增对应单元测试；修改已有函数 → 确认已有测试仍覆盖，必要时补充边界 case。
- 回归保护：每次提交前至少运行 `pytest tests/` 确认全绿，不允许带红测试合入主干。

---

## 铁律三：Streamlit 状态机死锁红线

**绝对禁止**以下模式：

```python
# ❌ 禁止：将 UI 组件输出反向赋值给同名 session_state key
st.session_state[key] = st.data_editor(key=key)
```

此模式会导致：状态循环嵌套、组件失忆（每次 rerun 丢失用户编辑）、`StreamlitValueAssignmentNotAllowedError`。

**正确做法——双 Key 隔离法：**

```python
# ✅ 正确：初始数据 key 与组件绑定 key 严格分离
INIT_KEY = f"sniper_init_{stem}"   # 存放初始数据，不绑定组件
EDITOR_KEY = f"sniper_editor_{stem}"  # 仅绑定给 data_editor，后续只读取

if INIT_KEY not in st.session_state:
    st.session_state[INIT_KEY] = build_initial_df(...)

edited = st.data_editor(st.session_state[INIT_KEY], key=EDITOR_KEY)
# 读取用户编辑结果：st.session_state.get(EDITOR_KEY) 或直接用 edited 返回值
```

规则：**提供初始数据，key 绑定给组件，后续只读取，严禁反向赋值。**

---

## 铁律四：JSON 截断抢救红线

**绝对禁止**使用正则表达式尝试修复截断的嵌套 JSON：

```python
# ❌ 禁止：用正则拼凑闭合括号
import re
fixed = re.sub(r',\s*$', '', raw) + '}]}'  # 这是炸弹，不是修复
```

此方法在多层嵌套结构下必然产生语义错误，且错误无法被 Pydantic 捕获。

**正确做法——安全截断抛弃法：**

```python
# ✅ 正确：逆向寻找最后的合法闭合位置，宁可丢弃末尾不完整的风险点
import json

def salvage_json(raw: str) -> dict | None:
    """从截断的 JSON 字符串中抢救最大合法子集。"""
    for i in range(len(raw) - 1, -1, -1):
        if raw[i] in ('}', ']'):
            try:
                return json.loads(raw[:i+1])
            except json.JSONDecodeError:
                continue
    return None
```

哪怕丢弃末尾部分风险点，也要保全已解析部分的数据完整性。

---

## 铁律五：测试 API 成本护盾（Mock 强制拦截）

**绝对禁止**在自动化测试中消耗真实的 DeepSeek / ASR / DashScope API 额度：

```python
# ❌ 禁止：测试中调用真实外部 API（产生费用 + 测试不稳定）
def test_something():
    words = transcribe_audio(real_audio_file)   # 真实 ASR 调用，扣费！
    report = evaluate_pitch(words, ...)          # 真实 LLM 调用，扣费！
```

**正确做法——Mock 强制拦截：**

```python
# ✅ 正确：在测试入口处拦截所有外部 IO，零 API 费用
from unittest.mock import patch

def test_pipeline_with_cache(tmp_path):
    with (
        patch("job_pipeline.transcribe_audio", return_value=mock_words),
        patch("job_pipeline.evaluate_pitch", return_value=mock_report),
        patch("job_pipeline.apply_asr_original_text_override", return_value=mock_report),
    ):
        words_out, report_out = run_pitch_file_job(
            audio_path, params, skip_html_export=True, cached_words=None
        )
```

**Mock namespace 必须与被测模块的 import 路径完全一致：**

| 被测模块 | 正确 patch 路径 | 错误路径（勿用）|
|----------|-----------------|-----------------|
| `job_pipeline.py` 内的 `transcribe_audio` | `job_pipeline.transcribe_audio` | `transcriber.transcribe_audio` |
| `job_pipeline.py` 内的 `evaluate_pitch` | `job_pipeline.evaluate_pitch` | `llm_judge.evaluate_pitch` |

**集成测试规则**（需要真实 API 时）：
- 必须标注 `@pytest.mark.integration`。
- CI / 全量回归跑 `pytest -m "not integration"` 跳过。
- 本地手动触发时明确告知主理人将产生费用，获得授权后方可执行。

---

## 附：快速检查清单（改代码前过一遍）

- [ ] 我是否已读过 `ARCHITECTURE.md` 对应章节？
- [ ] 我是否先写了测试？（铁律二）
- [ ] 我的测试是否全部使用了 Mock，未消耗真实 API？（铁律五）
- [ ] 我的 UI 代码中是否存在反向赋值 session_state？（铁律三）
- [ ] 我的 JSON 解析是否用了安全截断而非正则修复？（铁律四）
- [ ] 改了 `schema.py` 字段后，是否同步了 Prompt、审查台、报告和测试？
- [ ] 发版前：`build_release.py::CURRENT_VERSION`、白名单 txt、`.cursorrules` 三处是否对齐？

---

*最后更新：V7.6 收官后，铁律五增补，由主理人授权写入。*
