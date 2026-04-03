"""
P0 回归：_batch_sniper_targets_json 的 DataFrame `or` 运算符崩溃。

根因：`df = st.session_state.get(result_key) or st.session_state.get(init_key)`
      当 result_key 存储的是 Pandas DataFrame 时，Python 调用 DataFrame.__bool__()，
      抛出 ValueError: The truth value of a DataFrame is ambiguous。

修复：将 `or` 改为 `is None` 判断。

测试策略：
- 提取纯函数 _pick_sniper_df(result_val, init_val) 验证修复逻辑
- 保留一条"文档测试"记录旧 or 的崩溃路径（红灯即正确）
- 零 Streamlit 依赖，零外部 API 消耗

运行：pytest tests/test_v90_sniper_dataframe_bug.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# ── 复现修复逻辑的纯函数（与 app.py 中提取版本一致）──────────────────────
def _pick_sniper_df(result_val, init_val):
    """
    从 session_state 的 result_key / init_key 中安全选取 DataFrame。
    使用 `is None` 而不是 `or`，避免 DataFrame.__bool__() 抛出 ValueError。
    """
    df = result_val
    if df is None:
        df = init_val
    return df


class TestDataFrameOrBugDocumentation:
    """记录旧 `or` 运算符在 DataFrame 上的崩溃路径——这些测试必须通过（崩溃是预期）。"""

    def test_or_on_nonempty_df_raises_value_error(self):
        """非空 DataFrame `or None` 触发 ValueError：根因文档化。"""
        df = pd.DataFrame([{"原文引用": "某上市公司年报", "找茬疑点": "利润增速矛盾"}])
        with pytest.raises(ValueError, match="ambiguous"):
            _ = df or None  # type: ignore[truthy-bool]

    def test_or_on_empty_df_also_raises(self):
        """空 DataFrame 同样触发 ValueError（不是 falsy）。"""
        df = pd.DataFrame()
        with pytest.raises(ValueError, match="ambiguous"):
            _ = df or pd.DataFrame([{"a": 1}])  # type: ignore[truthy-bool]

    def test_nonempty_df_is_not_none(self):
        """非空 DataFrame 的 `is None` 返回 False，是安全判断的前提。"""
        df = pd.DataFrame([{"x": 1}])
        assert (df is not None) is True

    def test_empty_df_is_not_none(self):
        """空 DataFrame 的 `is None` 同样返回 False。"""
        df = pd.DataFrame()
        assert (df is not None) is True


class TestPickSniperDfFix:
    """验证修复后的 _pick_sniper_df 在所有场景下的行为。"""

    def test_result_df_has_priority_over_init(self):
        """result_val 不为 None 时，优先返回 result_val（无论内容是否为空）。"""
        result = pd.DataFrame([{"原文引用": "已填原文", "找茬疑点": "已填疑点"}])
        init = pd.DataFrame([{"原文引用": "", "找茬疑点": ""}])
        out = _pick_sniper_df(result, init)
        assert list(out["原文引用"]) == ["已填原文"]

    def test_empty_df_result_still_has_priority(self):
        """result_val 是空 DataFrame（非 None），仍然优先返回 result_val。"""
        result = pd.DataFrame({"原文引用": [], "找茬疑点": []})
        init = pd.DataFrame([{"原文引用": "init_data", "找茬疑点": "hint"}])
        out = _pick_sniper_df(result, init)
        # 返回的是空 DataFrame，而非 init（防止新用户清空表后被 init 覆盖）
        assert len(out) == 0

    def test_none_result_falls_back_to_init(self):
        """result_val 为 None 时，正确回退到 init_val。"""
        init = pd.DataFrame([{"原文引用": "init_原文", "找茬疑点": "init_疑点"}])
        out = _pick_sniper_df(None, init)
        assert out.iloc[0]["原文引用"] == "init_原文"

    def test_both_none_returns_none(self):
        """result_val 和 init_val 均为 None 时，返回 None，由调用方处理。"""
        out = _pick_sniper_df(None, None)
        assert out is None

    def test_multirow_result_df_preserved(self):
        """多行 DataFrame（用户填了多条狙击目标）完整返回。"""
        result = pd.DataFrame([
            {"原文引用": "原文A", "找茬疑点": "疑点A"},
            {"原文引用": "原文B", "找茬疑点": "疑点B"},
            {"原文引用": "原文C", "找茬疑点": "疑点C"},
        ])
        out = _pick_sniper_df(result, None)
        assert len(out) == 3
        assert list(out["找茬疑点"]) == ["疑点A", "疑点B", "疑点C"]

    def test_no_bool_evaluation_on_result_df(self):
        """验证修复函数不会对 DataFrame 调用 bool()（即不触发 ValueError）。"""
        df_variants = [
            pd.DataFrame([{"a": 1}]),
            pd.DataFrame(),
            pd.DataFrame([{"a": 1}, {"a": 2}]),
        ]
        for df in df_variants:
            # 如果 _pick_sniper_df 内部用了 `or`，这里就会 raise
            out = _pick_sniper_df(df, pd.DataFrame([{"fallback": True}]))
            assert out is df  # 返回的必须是同一个对象，不是 fallback


class TestSniperJsonSerializationSafe:
    """验证狙击清单 JSON 序列化在修复后能正确完成（端到端逻辑链）。"""

    def _serialize_df_to_json(self, df) -> str:
        """仿照 _batch_sniper_targets_json 的序列化逻辑（剥离 st 依赖后的核心）。"""
        import json
        if df is None or not hasattr(df, "iterrows"):
            return "[]"
        rows_out = []
        for _, row in df.iterrows():
            q = str(row.get("原文引用") or "").strip()
            r = str(row.get("找茬疑点") or "").strip()
            if q or r:
                rows_out.append({"quote": q, "reason": r})
        return json.dumps(rows_out, ensure_ascii=False)

    def test_serialize_normal_df(self):
        """正常填写的 DataFrame 能序列化为合法 JSON。"""
        import json
        df = pd.DataFrame([{"原文引用": "我们明年一定上市", "找茬疑点": "确定性太强"}])
        result = _pick_sniper_df(df, None)
        out = self._serialize_df_to_json(result)
        parsed = json.loads(out)
        assert len(parsed) == 1
        assert parsed[0]["quote"] == "我们明年一定上市"
        assert parsed[0]["reason"] == "确定性太强"

    def test_serialize_none_df(self):
        """None 返回空数组 JSON，不崩溃。"""
        result = _pick_sniper_df(None, None)
        out = self._serialize_df_to_json(result)
        assert out == "[]"

    def test_serialize_empty_rows_filtered(self):
        """全空行不进入输出 JSON。"""
        import json
        df = pd.DataFrame([
            {"原文引用": "", "找茬疑点": ""},
            {"原文引用": "有内容", "找茬疑点": "有疑点"},
        ])
        result = _pick_sniper_df(df, None)
        out = self._serialize_df_to_json(result)
        parsed = json.loads(out)
        assert len(parsed) == 1
        assert parsed[0]["quote"] == "有内容"
