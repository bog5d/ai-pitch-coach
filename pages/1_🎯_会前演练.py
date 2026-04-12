"""
会前演练模式 — 独立 Streamlit 页面（V10.3 P2.3）

此页面可在多页模式下独立访问，无需通过主控台进入。
核心逻辑复用 practice_engine.py，UI 与 app.py 中的 _render_practice_mode 一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# 将 src/ 加入路径
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

st.set_page_config(
    page_title="会前演练 — AI 路演教练",
    page_icon="🎯",
    layout="wide",
)

st.title("🎯 会前演练模式")
st.caption("AI 扮演投资机构投资人，你作答，逐轮实时评分。")

try:
    from runtime_paths import get_writable_app_root
    from institution_registry import get_all as get_all_institutions
    from institution_registry import resolve as institution_resolve
    from practice_engine import (
        evaluate_answer_and_next,
        get_session_summary,
        start_practice_session,
    )
except ImportError as e:
    st.error(f"依赖加载失败：{e}")
    st.stop()

ws_path = Path(get_writable_app_root()).parent

# ── 选择公司 ──────────────────────────────────────────────────────────────────
company_id = st.text_input(
    "被访公司 ID",
    placeholder="如：泽天智航_1775917777",
    key="practice_page_company_id",
)

if not company_id:
    st.info("👆 请先输入被访公司 ID。")
    st.stop()

# ── 其余 UI（与 app.py _render_practice_mode 完全相同） ────────────────────────
all_institutions = get_all_institutions()
inst_name_list = [r["canonical_name"] for r in all_institutions] if all_institutions else []

col_inst, col_start = st.columns([3, 1])
with col_inst:
    practice_inst = st.selectbox(
        "选择扮演的投资机构",
        ["（手动输入）"] + inst_name_list,
        key="practice_page_inst_select",
    )
    if practice_inst == "（手动输入）":
        practice_inst = st.text_input("机构名称", key="practice_page_inst_manual", placeholder="如：迪策资本")

session_key = f"practice_session_{company_id}_page"

with col_start:
    st.write("")
    st.write("")
    if st.button("▶️ 开始新演练", key="practice_page_start_btn", type="primary"):
        _pi = (practice_inst or "").strip()
        if not _pi:
            st.error("请选择或填写投资机构名称。")
        else:
            _iid, _icanon = institution_resolve(_pi)
            with st.spinner("AI 投资人正在准备开场问题…"):
                try:
                    sess = start_practice_session(_iid, company_id, ws_path)
                    st.session_state[session_key] = sess
                    st.session_state[f"{session_key}_current_q"] = sess["opening_question"]
                    st.session_state[f"{session_key}_done"] = False
                except Exception as ex:
                    st.error(f"启动演练失败：{ex}")

if session_key not in st.session_state:
    st.info("👆 选择机构后点击「开始新演练」。")
    st.stop()

sess = st.session_state[session_key]
is_done = st.session_state.get(f"{session_key}_done", False)
current_q = st.session_state.get(f"{session_key}_current_q", "")
rounds = sess.get("rounds", [])

if rounds:
    with st.expander(f"📜 历史问答（{len(rounds)} 轮）", expanded=False):
        for i, r in enumerate(rounds, 1):
            sc = "🟢" if r["score"] >= 75 else ("🟡" if r["score"] >= 60 else "🔴")
            st.markdown(f"**第 {i} 轮** {sc} {r['score']}分")
            st.markdown(f"> **投资人**：{r['question']}")
            st.markdown(f"> **你**：{r['answer']}")
            st.caption(f"反馈：{r['feedback']}")
            st.divider()

if is_done:
    st.success("✅ 本轮演练结束！")
    summary = get_session_summary(sess)
    c1, c2, c3 = st.columns(3)
    c1.metric("总轮数", summary["total_rounds"])
    c2.metric("平均得分", f"{summary['avg_score']:.1f}")
    c3.metric("弱项数量", len(summary["weak_areas"]))
    if summary["weak_areas"]:
        st.warning("🎯 **需要重点准备的问题类型：**")
        for q in summary["weak_areas"]:
            st.markdown(f"- {q}")
    st.stop()

inst_name = sess.get("institution_profile", {}).get("canonical_name", "投资人")
st.markdown(f"### 💬 **{inst_name}** 提问：")
st.info(current_q)

ANSWER_KEY = f"{session_key}_answer_input"
st.text_area("你的回答", key=ANSWER_KEY, height=120, placeholder="输入你的回答…")

col_submit, col_end = st.columns([2, 1])
with col_submit:
    if st.button("✅ 提交回答", key=f"{session_key}_submit"):
        answer_text = (st.session_state.get(ANSWER_KEY) or "").strip()
        if not answer_text:
            st.warning("请先输入回答。")
        else:
            with st.spinner("AI 评分中…"):
                try:
                    result = evaluate_answer_and_next(sess, question=current_q, answer=answer_text)
                    score = result["score"]
                    feedback = result["feedback"]
                    next_q = result["next_question"]
                    st.session_state[session_key] = result["updated_session"]
                    st.session_state[f"{session_key}_current_q"] = next_q
                    sc = "🟢" if score >= 75 else ("🟡" if score >= 60 else "🔴")
                    st.markdown(f"**{sc} 得分：{score} / 100**")
                    st.markdown(f"**反馈**：{feedback}")
                    st.rerun()
                except Exception as ex:
                    st.error(f"评分失败：{ex}")

with col_end:
    if st.button("🏁 结束演练", key=f"{session_key}_end"):
        st.session_state[f"{session_key}_done"] = True
        st.rerun()
