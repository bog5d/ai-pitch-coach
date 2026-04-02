# /safe-tdd-dev — AI Pitch Coach 安全 TDD 开发技能

**触发时机**：每当被要求「开发新功能」或「修 Bug」时，必须在第一步调用此技能工作流，不得跳过。

---

## 强制工作流（顺序不可颠倒）

### Step 1 · 红蓝对抗防幻觉推演（≤5 分钟）

在动任何一行代码前，必须逐项回答：

| 维度 | 问题 | 预期答案 |
|------|------|----------|
| LLM 幻觉 | 这个函数的输入来自 LLM 吗？JSON 可能截断或字段缺失吗？ | 有→写降级策略；无→明确说明 |
| UI 状态 | 涉及 Streamlit 的 session_state 吗？rerun 后状态还在吗？ | 有→双 Key 隔离；无→明确说明 |
| 边界输入 | 空列表、None、超长字符串、文件不存在，各路径都有处理吗？ | 每条都需要明确分支 |
| API 成本 | 新逻辑会被测试调用吗？测试中是否需要 Mock 外部 API？ | 有外部调用→强制 Mock |

**未通过以上任意一项推演，禁止进入 Step 2。**

---

### Step 2 · 先写 Mock 测试（TDD，测试必须先于业务代码）

**规则**：
- 测试文件命名：`tests/test_{feature_name}.py`
- 所有 `transcribe_audio`、`evaluate_pitch`、HTTP 请求，必须用 `unittest.mock.patch` 拦截
- Mock namespace 必须与被测模块的 import 路径一致（见 CLAUDE.md 铁律五表格）
- 每个新函数至少覆盖：正常路径、边界 case（空/None/超长）、异常路径（Mock 抛出 Exception）

**测试模板**：

```python
"""
tests/test_{feature}.py — {功能名} 单元测试
TDD: 此文件必须先于业务代码存在。
Mock 策略：所有外部 API 调用必须拦截，零真实费用。
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ── 正常路径 ──────────────────────────────────────────────
class TestHappyPath:
    def test_normal_case(self, tmp_path):
        with (
            patch("module.external_api_func", return_value=MOCK_RESULT),
        ):
            result = function_under_test(...)
            assert result == EXPECTED

# ── 边界 case ─────────────────────────────────────────────
class TestEdgeCases:
    def test_empty_input(self): ...
    def test_none_input(self): ...
    def test_oversized_input(self): ...

# ── 异常路径 ──────────────────────────────────────────────
class TestErrorPaths:
    def test_api_timeout_raises_gracefully(self, tmp_path):
        with patch("module.external_api_func", side_effect=TimeoutError("timeout")):
            with pytest.raises(TimeoutError):
                function_under_test(...)
```

---

### Step 3 · 自主运行 pytest 直到全绿（授权自主执行）

```bash
python -m pytest tests/test_{feature}.py -v   # 新测试先跑
python -m pytest tests/ -q                    # 全量回归
```

- 若出现红色 FAILED：读取完整 stdout，定位根因，修改测试或补全 Mock，**不得跳过报错**。
- 循环直到终端显示 `N passed` 且无 FAILED。
- **禁止带红测试进入 Step 4**。

---

### Step 4 · 修改业务代码（测试绿灯后方可）

- 严格对照 Step 1 推演结论编写代码。
- 写完后再跑一次 `pytest tests/ -q` 确认回归不破。
- 确认绿灯后通知主理人，请求代码审查与合并授权。

---

## 快速召唤方式

在对话中输入 `/safe-tdd-dev` 即可加载此工作流。
也可在指令末尾追加 `use safe-tdd-dev workflow` 提醒 Claude 调用。
