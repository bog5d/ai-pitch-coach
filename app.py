"""
AI 路演与访谈复盘系统 — Streamlit 企业级控制台（按录音逐条归档 + 动态路径）。
发版主线 V9.0（与根目录 build_release.py → CURRENT_VERSION 对齐）。

支持单次 1 个或多个音频：每条录音单独填写被访谈人、备注与参考 QA。
运行：在项目根目录执行  streamlit run app.py
依赖：pip install streamlit（及项目既有 transcriber / llm_judge / report_builder 依赖）
"""
from __future__ import annotations

import copy
from collections.abc import Callable
import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import uuid

import pandas as pd
from pathlib import Path


def get_resource_path(relative_path):
    """获取资源的绝对路径，无缝兼容 Python 脚本开发环境与 PyInstaller 打包 EXE 环境"""
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


_SRC = Path(get_resource_path("src"))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from disk_asr_cache import get_default_cache_dir, load_asr_cache, save_asr_cache
from draft_manager import list_available_drafts, load_draft, save_draft
from garbage_collector import sweep_stale_intermediate_json
from runtime_paths import get_writable_app_root
from system_debug_log import read_debug_log_bytes, setup_file_logging

_ROOT = Path(get_resource_path(".")).resolve()
_ENV_PATH = get_writable_app_root() / ".env"

import streamlit as st
from dotenv import load_dotenv, set_key
from openai import APIError, OpenAI

load_dotenv(_ENV_PATH)

from audio_filename_hints import guess_batch_fields_from_stem, should_autofill_iv, stem_from_audio_filename
from audio_preprocess import smart_compress_media
from document_reader import extract_text_from_files
from job_pipeline import (
    DEFAULT_HTML_FILENAME_MASKS,
    OTHER_SCENE_KEY,
    PitchFileJobParams,
    SCENE_MAP,
    apply_html_filename_masks,
    build_explicit_context,
    run_pitch_file_job,
    safe_fs_segment,
)
from sensitive_words import parse_sensitive_words
import company_profile as cp
from schema import CompanyProfile
from llm_judge import detect_logical_conflict, polish_manual_risk_point, refine_risk_point
from transcriber import format_transcript_plain_by_speaker, transcribe_audio
from report_builder import (
    HtmlExportOptions,
    apply_asr_original_text_override,
    desensitize_text,
    generate_html_report,
    snippet_audio_mp3_bytes,
)
from schema import AnalysisReport, RiskPoint, TranscriptionWord
from analytics_exporter import export_analytics
from benchmark_engine import build_benchmark, scan_analytics_files
from institution_registry import fuzzy_match as institution_fuzzy_match, resolve as institution_resolve, get_all as get_all_institutions, increment_session_count as institution_inc_session
from institution_profiler import list_all_institution_profiles, build_institution_profile
from briefing_engine import generate_briefing_data, generate_briefing_text
from github_sync import sync_analytics as github_sync_analytics, sync_institutions as github_sync_institutions

_SCENE_SELECT_PLACEHOLDER = "—— 请先选择业务场景 ——"


def _extract_tier1_summary(tier1: str) -> str:
    """
    提取 tier1_general_critique 的首句作为「问题背景」摘要。
    优先找第一个中文句末标点（。；！？）且位置 ≤100，回退到前 100 字 + 省略号。
    """
    text = (tier1 or "").strip()
    if not text:
        return ""
    for sep in ["。", "；", "！", "？"]:
        idx = text.find(sep)
        if 0 < idx <= 100:
            return text[: idx + 1]
    if len(text) <= 100:
        return text
    return text[:100] + "…"


def _v86_risk_point_harvest_blob(rp: dict) -> str:
    """审查台单条风险点：拼接可比对文本，供静默收割防噪门使用。"""
    if not rp:
        return ""
    parts = [
        str(rp.get("tier1_general_critique") or ""),
        str(rp.get("tier2_qa_alignment") or ""),
        str(rp.get("improvement_suggestion") or ""),
        str(rp.get("original_text") or ""),
        str(rp.get("deduction_reason") or ""),
    ]
    return "\n".join(parts).strip()


def _v86_harvest_finalize_if_needed(stem: str, payload: dict) -> int:
    """锁定导出成功后：初稿 vs 终稿差异达标则静默提炼入库；返回新入库条数（失败不影响主流程）。"""
    ctx = st.session_state.get(f"v3_ctx_{stem}") or {}
    cid = (ctx.get("company_id") or "").strip()
    # 生成时未选项目，但锁定时用户已切换侧栏 → 实时补 company_id
    if not cid:
        _sidebar_cid = st.session_state.get("company_selector", "")
        if _sidebar_cid and _sidebar_cid != "__new__":
            cid = _sidebar_cid
            ctx["company_id"] = cid
            st.session_state[f"v3_ctx_{stem}"] = ctx
    if not cid:
        return 0
    tag = (ctx.get("interviewee") or "").strip() or "default"
    if tag in ("未指定",):
        return 0
    initial = st.session_state.get(f"v3_initial_report_{stem}")
    init_rps = (initial or {}).get("risk_points") or []
    init_map = {rp.get("_rid"): rp for rp in init_rps if isinstance(rp, dict) and rp.get("_rid")}
    fin_rps = (payload or {}).get("risk_points") or []
    harvested = 0
    try:
        from memory_engine import capture_and_distill_diff

        for frp in fin_rps:
            if not isinstance(frp, dict):
                continue
            rid = frp.get("_rid")
            old = init_map.get(rid, {}) if rid else {}
            ob = _v86_risk_point_harvest_blob(old if isinstance(old, dict) else {})
            nb = _v86_risk_point_harvest_blob(frp)
            risk_lv = str(frp.get("risk_level") or "").strip()
            if capture_and_distill_diff(ob, nb, cid, tag, risk_type=risk_lv):
                harvested += 1
    except Exception:
        logging.getLogger("ai_pitch_coach.ui").warning(
            "V8.6 静默收割异常（已忽略，不影响导出）", exc_info=True
        )
    return harvested


def _v86_render_executive_dashboard(company_id: str, workspace_root: str = "") -> None:
    """V10.1 复盘数据中台：4-Tab 架构（会话总览/个人成长/行业基准/AI纠偏库）。"""
    st.markdown("## 📂 复盘数据中台")
    if not company_id or company_id == "__new__":
        st.warning("请先在侧栏选择项目档案后再查看数据中台。")
        return

    ws_path = Path((workspace_root or "").strip() or str(get_writable_app_root()))

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📊 会话总览",
        "👤 个人成长",
        "🌐 行业基准",
        "🧠 AI纠偏库",
        "🏦 机构画像",
        "🎯 会前演练",
    ])

    # ════════════════════════════════════════════════
    # Tab 1：会话总览 — 每次运行都留痕（analytics JSON）
    # ════════════════════════════════════════════════
    with tab1:
        _render_session_overview(company_id, ws_path)

    # ════════════════════════════════════════════════
    # Tab 2：个人成长 — 成长曲线 + 雷达图 + 练习推荐
    # ════════════════════════════════════════════════
    with tab2:
        from memory_engine import list_all_executive_memories_for_company
        pairs = list_all_executive_memories_for_company(company_id)
        _render_personal_growth_section(company_id, workspace_root, pairs)

    # ════════════════════════════════════════════════
    # Tab 3：行业基准 — 匿名跨项目对比
    # ════════════════════════════════════════════════
    with tab3:
        _render_benchmark_section(workspace_root)

    # ════════════════════════════════════════════════
    # Tab 4：AI纠偏库 — 专属错题本（只在用户纠正AI后入库）
    # ════════════════════════════════════════════════
    with tab4:
        _render_ai_correction_library(company_id)

    # ════════════════════════════════════════════════
    # Tab 5：机构画像 — 跨公司投资机构分析（V10.2）
    # ════════════════════════════════════════════════
    with tab5:
        _render_institution_profiles(ws_path)

    # ════════════════════════════════════════════════
    # Tab 6：会前演练 — AI 扮投资人，实时问答评分（V10.3 P2.1）
    # ════════════════════════════════════════════════
    with tab6:
        _render_practice_mode(company_id, ws_path)


def _render_session_overview(company_id: str, ws_path: Path) -> None:
    """Tab 1：会话总览 — 从 analytics JSON 聚合，凡运行必有记录。"""
    import plotly.express as px
    from benchmark_engine import scan_analytics_files

    all_items = scan_analytics_files(ws_path)
    items = [s for s in all_items if s.get("company_id", "").strip() == company_id.strip()]

    # ── 全局 KPI ──────────────────────────────────────────────────────────
    total = len(items)
    people = sorted({s.get("interviewee", "").strip() for s in items if s.get("interviewee", "").strip()})
    locked = sum(1 for s in items if s.get("status") == "locked")
    avg_score = round(sum(s.get("total_score", 0) for s in items) / total, 1) if total else 0.0

    # V10.3 P3.1 融资成功率预测
    _pred_prob = None
    _pred_signal = "neutral"
    try:
        from outcome_predictor import predict_success_probability
        _pred = predict_success_probability(items)
        _pred_prob = _pred["probability"]
        _pred_signal = _pred["signal"]
    except Exception:
        pass

    with st.container(border=True):
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("总复盘场次", total, help="包含 AI 初稿（草稿）和已锁定导出的场次")
        c2.metric("已锁定场次", locked)
        c3.metric("覆盖人数", len(people))
        c4.metric("项目均分", f"{avg_score:.1f}" if total else "—")
        if _pred_prob is not None:
            _signal_icons = {
                "strong_positive": "🟢⬆️", "positive": "🟢",
                "neutral": "🟡", "negative": "🔴", "strong_negative": "🔴⬇️",
            }
            c5.metric(
                "融资成功预测",
                f"{_pred_prob*100:.0f}%",
                help=f"基于历史路演数据的启发式预测，仅供参考。信号：{_signal_icons.get(_pred_signal, '')}",
            )
        else:
            c5.metric("融资成功预测", "—", help="暂无足够数据")

    if total == 0:
        st.info(
            "📭 本项目暂无复盘记录。\n\n"
            "**下一步**：在主控制台选择本项目，上传录音并运行 AI 分析，"
            "系统将自动在此留下每一场记录（无需锁定）。"
        )
        return

    # ── 得分趋势折线图 ────────────────────────────────────────────────────
    import re as _re

    sorted_items = sorted(items, key=lambda x: x.get("generated_at", ""))
    if len(sorted_items) >= 2:
        st.caption("▸ 历次得分趋势（按生成时间排序）")
        _labels, _scores, _people, _statuses = [], [], [], []
        for s in sorted_items:
            m = _re.search(r"(\d{4})-(\d{2})-(\d{2})", s.get("generated_at", ""))
            _labels.append(f"{m.group(2)}/{m.group(3)}" if m else "—")
            _scores.append(s.get("total_score", 0))
            _people.append(s.get("interviewee", "—"))
            _statuses.append("✅已锁定" if s.get("status") == "locked" else "📝草稿")

        import plotly.graph_objects as go
        fig_trend = go.Figure()
        fig_trend.add_trace(go.Scatter(
            x=_labels, y=_scores,
            mode="lines+markers",
            marker=dict(
                size=10,
                color=["#22c55e" if st == "✅已锁定" else "#94a3b8" for st in _statuses],
                symbol=["circle" if st == "✅已锁定" else "circle-open" for st in _statuses],
            ),
            line=dict(color="#3b82f6", width=2),
            text=[f"{p}<br>{sc}分 {st}" for p, sc, st in zip(_people, _scores, _statuses)],
            hovertemplate="%{text}<extra></extra>",
        ))
        fig_trend.update_layout(
            height=260, margin=dict(t=10, b=10),
            yaxis=dict(range=[max(0, min(_scores) - 10), 105], title="综合得分"),
            xaxis_title="复盘场次",
        )
        st.plotly_chart(fig_trend, use_container_width=True)
        st.caption("🟢 实心绿点 = 已锁定导出　⚪ 空心灰点 = AI草稿未锁定")

    # ── 会话记录列表 ─────────────────────────────────────────────────────
    st.caption("▸ 全部会话记录（倒序，点击表头可排序）")
    rows = []
    for s in reversed(sorted_items):
        dt = s.get("generated_at", "")[:10]
        rb = s.get("risk_breakdown") or {}
        rows.append({
            "日期": dt,
            "被访谈人": s.get("interviewee", "—"),
            "得分": s.get("total_score", "—"),
            "状态": "✅锁定" if s.get("status") == "locked" else "📝草稿",
            "严重风险": rb.get("严重", {}).get("count", 0),
            "一般风险": rb.get("一般", {}).get("count", 0),
            "AI纠错数": s.get("refinement_count", 0),
            "人工补录": s.get("ai_miss_count", 0),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── V10.3 P2.2 导出客户只读报告 ──────────────────────────────────────────
    st.divider()
    with st.expander("📤 导出客户进度报告（只读 HTML，可安全分享）", expanded=False):
        st.caption("生成不含机密内容（无风险点详情）的静态 HTML 报告，可发送给客户公司。")
        if st.button("生成客户报告", key=f"gen_client_report_{company_id}"):
            try:
                from client_dashboard import collect_company_data, generate_client_dashboard_html
                with st.spinner("生成报告中…"):
                    report_data = collect_company_data(company_id, ws_path)
                    out_dir = ws_path / "client_reports"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    import re
                    safe_cid = re.sub(r"[^\w\-]", "_", company_id)
                    out_file = out_dir / f"{safe_cid}_client_report.html"
                    generate_client_dashboard_html(report_data, out_file)
                html_bytes = out_file.read_bytes()
                st.success(f"✅ 报告已生成：`{out_file.name}`")
                st.download_button(
                    "⬇️ 下载客户报告 (.html)",
                    data=html_bytes,
                    file_name=out_file.name,
                    mime="text/html",
                    key=f"dl_client_report_{company_id}",
                )
            except Exception as ex:
                st.error(f"生成失败：{ex}")


def _render_ai_correction_library(company_id: str) -> None:
    """Tab 4：AI纠偏库 — 专属错题本（原 全景机构画像 核心内容）。"""
    from memory_engine import (
        delete_executive_memory_by_uuid,
        get_company_dashboard_stats,
        list_all_executive_memories_for_company,
        update_executive_memory_weight,
    )

    _ks = "".join(c if c.isalnum() or c in "_-" else "_" for c in company_id)[:48]
    pairs = list_all_executive_memories_for_company(company_id)
    stats = get_company_dashboard_stats(company_id, pre_loaded_pairs=pairs)

    st.caption(
        "💡 AI纠偏库只收录**你主动修改过 AI 初稿**的条目——"
        "这是系统用来下次分析时更精准判断的私有语料，越改越准。"
    )

    # KPI
    with st.container(border=True):
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("纠偏条目总数", stats["total_memories"])
        k2.metric("涉及被访人数", stats["active_executives"])
        k3.metric("累计命中次数", stats["total_hit_count"])
        last_u = (stats["last_updated_at"] or "—")
        if "T" in last_u:
            last_u = last_u[:10]
        k4.metric("最近更新", last_u)

    if stats["total_memories"] == 0:
        st.info("暂无纠偏记录。在审查台修改 AI 初稿后锁定，系统将自动提炼并入库。")
        return

    # 飞轮速度指数
    fm = stats.get("flywheel_metrics", {})
    if fm:
        import plotly.express as px
        fa, fb, fc = st.columns(3)
        fa.metric("记忆命中率", f"{fm.get('hit_rate', 0)*100:.1f}%")
        fb.metric("本月新增", fm.get("monthly_new", 0))
        fc.metric("高权重条目", fm.get("weight_distribution", {}).get("high", 0))
        top = fm.get("top_memories") or []
        if top:
            df_top = pd.DataFrame(top, columns=["tag", "raw_text_snippet", "hit_count"])
            df_top.columns = ["高管", "易错要点", "命中次数"]
            fig_top = px.bar(
                df_top, x="命中次数", y="高管", orientation="h",
                template="plotly_white", color_discrete_sequence=["#16a34a"],
            )
            fig_top.update_layout(
                yaxis=dict(autorange="reversed", title=None),
                height=max(200, len(top) * 36),
                margin=dict(t=8, b=8, l=120, r=8),
            )
            st.plotly_chart(fig_top, use_container_width=True)

    # 明细表 + 删除/权重调整
    rows = []
    for stem_tag, mem in pairs:
        rows.append({
            "标签": mem.tag, "风险类型": (mem.risk_type or "—"),
            "易错要点": mem.raw_text, "标准口径": mem.correction,
            "权重": float(mem.weight), "命中次数": int(mem.hit_count),
            "最后触发": (mem.updated_at or "—")[:10], "uuid": mem.uuid,
        })
    if not rows:
        return

    all_tags = sorted({r["标签"] for r in rows})
    f1, f2 = st.columns(2)
    with f1:
        exec_pick = st.selectbox("按被访人筛选", ["全部"] + all_tags, key=f"v90_exec_{_ks}")
    with f2:
        all_risks = sorted({r["风险类型"] for r in rows})
        risk_pick = st.multiselect("按风险类型筛选", all_risks, key=f"v90_risk_{_ks}")

    filt = [r for r in rows
            if (exec_pick == "全部" or r["标签"] == exec_pick)
            and (not risk_pick or r["风险类型"] in risk_pick)]
    st.dataframe(pd.DataFrame(filt), use_container_width=True, hide_index=True)

    if not filt:
        return
    options = [(r["uuid"], f"{r['uuid'][:8]}… │ {str(r['易错要点'])[:32]}…") for r in filt]
    uuid_list = [u for u, _ in options]

    st.markdown("**外科手术 · 删除 / 调权重**")
    c1, c2 = st.columns(2)
    with c1:
        del_u = st.selectbox("删除条目", uuid_list,
                             format_func=lambda u: next(l for uid, l in options if uid == u),
                             key=f"v90_del_{_ks}")
        if st.button("🗑️ 删除所选", key=f"v90_del_btn_{_ks}", type="secondary"):
            if delete_executive_memory_by_uuid(company_id, del_u):
                st.success("已删除。"); st.rerun()
            else:
                st.error("未找到。")
    with c2:
        w_u = st.selectbox("调整权重", uuid_list,
                           format_func=lambda u: next(l for uid, l in options if uid == u),
                           key=f"v90_w_{_ks}")
        cur_w = next(float(r["权重"]) for r in filt if r["uuid"] == w_u)
        new_w = st.number_input("新权重", min_value=0.0, max_value=10.0,
                                value=float(cur_w), step=0.1, key=f"v90_w_num_{_ks}")
        if st.button("💾 应用权重", key=f"v90_w_btn_{_ks}"):
            if update_executive_memory_weight(company_id, w_u, new_w):
                st.success("已更新。"); st.rerun()
            else:
                st.error("更新失败。")


def _render_personal_growth_section(
    company_id: str,
    workspace_root: str,
    pairs: list,
) -> None:
    """V10.1：个人成长中心 = 成长曲线 + 弱点雷达图 + 今天练什么。"""
    import plotly.express as px
    import plotly.graph_objects as go
    from benchmark_engine import build_benchmark, scan_analytics_files
    from growth_engine import (
        build_growth_curve,
        build_weakness_radar,
        get_person_sessions,
        get_practice_recommendations,
    )

    st.divider()
    st.subheader("🧬 个人成长中心")

    # ── 被访谈人选择器 ──────────────────────────────────────────────────────
    all_tags = sorted({tag for tag, _ in pairs}) if pairs else []
    if not all_tags:
        st.info("暂无个人记录。完成至少一次「锁定导出」（被访谈人非空）后，个人成长数据将自动积累。")
        return

    selected_person = st.selectbox(
        "选择被访谈人",
        options=all_tags,
        key=f"growth_person_{company_id}",
    )

    ws_path = Path((workspace_root or "").strip() or str(get_writable_app_root()))

    with st.spinner("加载个人历史数据…"):
        person_sessions = get_person_sessions(ws_path, company_id, selected_person)
        curve = build_growth_curve(person_sessions)
        all_analytics = scan_analytics_files(ws_path)
        benchmark = build_benchmark(all_analytics)
        radar = build_weakness_radar(person_sessions, benchmark)
        recs = get_practice_recommendations(person_sessions, top_n=3)

    n_sessions = len(person_sessions)
    if n_sessions == 0:
        st.info(
            f"「{selected_person}」暂无 analytics 记录（需先锁定导出至少一场）。"
            "已有的记忆数据仍会在下方明细中显示。"
        )
        return

    # ── 成长曲线区块 ─────────────────────────────────────────────────────────
    st.markdown("#### 📈 得分成长曲线")

    # KPI 行
    c1, c2, c3, c4 = st.columns(4)
    trend_icon = {"上升": "⬆️", "下降": "⬇️", "平稳": "➡️", "首次": "🆕", "暂无数据": "—"}.get(
        curve["trend"], ""
    )
    c1.metric("复盘场次", n_sessions)
    c2.metric("最新得分", curve["scores"][-1] if curve["scores"] else "—")
    delta_val = curve["score_delta"]
    c3.metric(
        "累计进步",
        f"+{delta_val}" if delta_val > 0 else str(delta_val),
        delta=f"{'+' if delta_val > 0 else ''}{delta_val}分",
    )
    c4.metric(f"趋势 {trend_icon}", curve["trend"])

    # 折线图
    if len(curve["scores"]) >= 2:
        import re as _re

        labels = []
        for d in curve["dates"]:
            m = _re.search(r"(\d{4})-(\d{2})-(\d{2})", d)
            labels.append(f"{m.group(2)}/{m.group(3)}" if m else d[:10])

        fig_line = go.Figure()
        fig_line.add_trace(go.Scatter(
            x=labels, y=curve["scores"],
            mode="lines+markers+text",
            text=[str(s) for s in curve["scores"]],
            textposition="top center",
            line=dict(color="#3b82f6", width=3),
            marker=dict(size=10),
            name="得分",
        ))
        # 行业均分参考线
        bm_avg = benchmark.get("avg_score", 0)
        if bm_avg:
            fig_line.add_hline(
                y=bm_avg,
                line_dash="dash",
                line_color="#f59e0b",
                annotation_text=f"行业均分 {bm_avg:.1f}",
                annotation_position="bottom right",
            )
        fig_line.update_layout(
            height=280,
            margin=dict(t=20, b=10),
            yaxis=dict(range=[max(0, min(curve["scores"]) - 10), 105]),
            xaxis_title="复盘场次",
            yaxis_title="综合得分",
        )
        st.plotly_chart(fig_line, use_container_width=True)
    else:
        st.caption(f"当前仅有 {n_sessions} 场记录，2场以上才绘制曲线。")

    # ── 弱点雷达图 ───────────────────────────────────────────────────────────
    st.markdown("#### 🎯 能力雷达图 vs 行业基准")

    dims = radar["dimensions"]
    person_vals = radar["person_values"]
    bm_vals = radar["benchmark_values"]

    fig_radar = go.Figure()
    fig_radar.add_trace(go.Scatterpolar(
        r=person_vals + [person_vals[0]],
        theta=dims + [dims[0]],
        fill="toself",
        name=selected_person,
        line_color="#3b82f6",
        fillcolor="rgba(59,130,246,0.2)",
    ))
    fig_radar.add_trace(go.Scatterpolar(
        r=bm_vals + [bm_vals[0]],
        theta=dims + [dims[0]],
        fill="toself",
        name="行业基准",
        line_color="#f59e0b",
        fillcolor="rgba(245,158,11,0.1)",
        line_dash="dash",
    ))
    fig_radar.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 100], tickfont=dict(size=9)),
        ),
        showlegend=True,
        height=380,
        margin=dict(t=20, b=20),
    )
    st.plotly_chart(fig_radar, use_container_width=True)

    # 雷达图维度说明（第一次看不懂就直接告诉他）
    with st.expander("📐 维度说明（点开看怎么读这张图）"):
        st.markdown(
            "- **综合得分**：历次得分均值，越高越好\n"
            "- **严重风险率**：越接近100说明严重错误越少（100 − 严重占比%）\n"
            "- **一般风险率**：越接近100说明一般错误越少\n"
            "- **AI纠错力**：你发现了多少AI漏掉的风险点，体现主动意识\n"
            "- **精炼覆盖率**：你修改过多少AI初判（代表你在认真审查而非盲目接受）\n"
            "- 蓝色区域=你，黄色虚线=行业均值；蓝色面积越大越好"
        )

    if radar["top_weakness_types"]:
        st.caption(
            f"⚡ 你的主要弱点维度：**{'、'.join(radar['top_weakness_types'])}** "
            "（见下方「今天练什么」）"
        )

    # ── 今天要重点练什么 ─────────────────────────────────────────────────────
    st.markdown("#### 🏋️ 今天要重点练什么")
    st.caption(f"基于 {n_sessions} 场历史复盘 · 最近 3 场权重 ×4")

    if not recs:
        st.info("暂无足够数据生成练习建议，继续完成更多复盘后此处将自动更新。")
    else:
        for i, rec in enumerate(recs):
            medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"#{i+1}"
            with st.container():
                col_l, col_r = st.columns([1, 4])
                col_l.markdown(f"### {medal}")
                with col_r:
                    st.markdown(f"**{rec['risk_type']}**　*（历史加权出现 {rec['count']} 次）*")
                    st.info(rec["suggestion"])


def _render_benchmark_section(workspace_root: str) -> None:
    """V10.0：扫描 workspace 下所有 *_analytics.json，生成匿名行业基准对比看板。"""
    import plotly.express as px

    st.divider()
    st.subheader("📊 行业基准对比（全部公司匿名聚合）")

    ws_path = Path((workspace_root or "").strip() or str(get_writable_app_root()))
    with st.spinner("扫描历史 analytics 数据…"):
        analytics_list = scan_analytics_files(ws_path)
        bm = build_benchmark(analytics_list)

    n = bm["total_sessions"]
    if n == 0:
        st.info("暂无 analytics 数据。完成至少一次「锁定导出」后，行业基准数据将自动积累。")
        return

    # ── KPI 卡片 ────────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("总分析场次", n)
    k2.metric("覆盖公司数", bm["total_companies"])
    k3.metric("行业平均分", f"{bm['avg_score']:.1f}")
    k4.metric("精炼覆盖率", f"{bm['refinement_rate']*100:.1f}%")

    # ── 得分分布图 ───────────────────────────────────────────────────────────
    st.caption("▸ 得分档位分布")
    dist_df_data = {
        "档位": [d["range"] for d in bm["score_distribution"]],
        "场次数": [d["count"] for d in bm["score_distribution"]],
    }
    fig_dist = px.bar(
        dist_df_data,
        x="档位",
        y="场次数",
        color="档位",
        color_discrete_map={
            "0-59": "#ef4444",
            "60-74": "#f59e0b",
            "75-89": "#3b82f6",
            "90-100": "#22c55e",
        },
        title="📈 综合得分档位分布（全公司匿名）",
    )
    fig_dist.update_layout(showlegend=False, height=280, margin=dict(t=40, b=10))
    st.plotly_chart(fig_dist, use_container_width=True)

    # ── 风险类型频率 ─────────────────────────────────────────────────────────
    rf = bm["risk_type_frequency"]
    total_risks = sum(rf.values()) or 1
    st.caption("▸ 风险点级别分布（占所有风险点总数的百分比）")
    rc1, rc2, rc3 = st.columns(3)
    rc1.metric(
        "🔴 严重",
        rf["严重"],
        delta=f"{rf['严重']/total_risks*100:.1f}%",
        delta_color="inverse",
    )
    rc2.metric(
        "🟡 一般",
        rf["一般"],
        delta=f"{rf['一般']/total_risks*100:.1f}%",
        delta_color="off",
    )
    rc3.metric(
        "🟢 轻微",
        rf["轻微"],
        delta=f"{rf['轻微']/total_risks*100:.1f}%",
        delta_color="normal",
    )

    if bm["truncation_rate"] > 0:
        st.caption(
            f"⚠️ 截断率 {bm['truncation_rate']*100:.1f}%（阶段一分析被截断场次占比，超过 10% 建议检查 Prompt 长度）"
        )
    # ── END 行业基准对比 ─────────────────────────────────────────────────────


def _render_sync_status_alert() -> None:
    """P0.3：GitHub 同步状态告警条。连续失败 ≥ 3 次显示红色警告。"""
    try:
        sync_st = github_sync_analytics.__module__  # 确认模块已导入
        from github_sync import get_sync_status
        s = get_sync_status()
        if not s["configured"]:
            st.warning(
                "⚠️ **GitHub 数据同步未配置**：`.env` 中缺少 `COACH_DATA_GITHUB_PAT` 或"
                " `COACH_DATA_GITHUB_REPO`，数据仅保存在本地，无法跨设备汇聚。",
                icon="⚠️",
            )
        elif s["needs_alert"]:
            st.error(
                f"🔴 **GitHub 同步连续失败 {s['consecutive_failures']} 次**"
                f"（最后错误：{s.get('last_error', '未知')}）。"
                "数据未同步到 coach_data repo，请检查 PAT 是否过期或网络是否正常。"
                f"上次成功：{s.get('last_success') or '从未成功'}",
            )
        elif s.get("last_success"):
            st.caption(f"☁️ 上次同步成功：{s['last_success']}")
    except Exception:
        pass  # 告警本身不影响主流程


def _render_institution_profiles(ws_path: Path) -> None:
    """V10.3 Tab 5：机构画像全览 + 会前简报 + 同步状态告警。"""
    import plotly.express as px

    # P0.3：同步状态告警
    _render_sync_status_alert()

    st.divider()
    st.subheader("🏦 投资机构画像")

    with st.spinner("聚合机构数据…"):
        profiles = list_all_institution_profiles(ws_path)

    if not profiles:
        st.info(
            "暂无机构数据。在「批量分析」页填写「投资机构名称」并完成锁定导出后，"
            "机构画像将自动积累。"
        )
    else:
        # ── 机构总览列表 ────────────────────────────────────────────────
        st.caption(f"共 {len(profiles)} 家机构，按场次降序")
        rows = []
        for p in profiles:
            rows.append({
                "机构名称": p["canonical_name"] or p["institution_id"],
                "场次": p["total_sessions"],
                "涉及公司": p["total_companies"],
                "平均得分": p["avg_score"],
                "严重风险率": f"{p['severe_risk_ratio']*100:.0f}%",
                "最爱追问": "、".join(r["risk_type"] for r in p["top_risk_types"][:3]),
            })
        st.dataframe(rows, use_container_width=True, height=200)

        # ── 选择机构下钻 ────────────────────────────────────────────────
        inst_options = [p["canonical_name"] or p["institution_id"] for p in profiles]
        selected_inst_name = st.selectbox(
            "选择机构查看详情", inst_options, key="v102_inst_select"
        )
        selected_profile = next(
            (p for p in profiles if (p["canonical_name"] or p["institution_id"]) == selected_inst_name),
            None,
        )

        if selected_profile:
            p = selected_profile
            c1, c2, c3 = st.columns(3)
            c1.metric("总场次", p["total_sessions"])
            c2.metric("平均得分", p["avg_score"])
            c3.metric("严重风险率", f"{p['severe_risk_ratio']*100:.0f}%")

            if p["score_trend"]:
                fig = px.line(
                    y=p["score_trend"],
                    x=list(range(1, len(p["score_trend"]) + 1)),
                    labels={"x": "场次", "y": "得分"},
                    title=f"📈 {selected_inst_name} 历次访谈得分趋势",
                )
                fig.update_layout(height=220, margin=dict(t=40, b=10))
                st.plotly_chart(fig, use_container_width=True)

            if p["top_risk_types"]:
                st.caption("▸ 该机构最爱追问的问题类型")
                for r in p["top_risk_types"]:
                    st.markdown(f"- **{r['risk_type']}**（历史占比 {r['ratio']*100:.0f}%，共 {r['count']} 次）")

    # ── 会前简报生成器 ────────────────────────────────────────────────────────
    st.divider()
    st.subheader("📋 会前简报生成器")
    st.caption(
        "输入即将见面的机构和公司，AI 自动生成今天最该准备的事项。"
        "（首次见某机构或暂无历史数据时，将自动生成通用建议版简报。）"
    )

    all_institutions = get_all_institutions()
    inst_name_list = [r["canonical_name"] for r in all_institutions] if all_institutions else []

    col_a, col_b = st.columns(2)
    with col_a:
        briefing_inst = st.selectbox(
            "即将见面的投资机构",
            ["（手动输入）"] + inst_name_list,
            key="v102_briefing_inst",
        )
        if briefing_inst == "（手动输入）":
            briefing_inst = st.text_input("机构名称", key="v102_briefing_inst_manual")
    with col_b:
        briefing_company = st.text_input(
            "被访公司名称",
            placeholder="例如：泽天智航",
            key="v102_briefing_company",
        )

    if st.button("🚀 生成会前简报", key="v102_gen_briefing"):
        _bi = (briefing_inst or "").strip()
        _bc = (briefing_company or "").strip()
        if not _bi:
            st.error("请填写投资机构名称。")
        elif not _bc:
            st.error("请填写被访公司 ID。")
        else:
            with st.spinner("AI 正在生成会前简报…"):
                # 解析机构 ID
                _bid, _bcanon = institution_resolve(_bi)
                text = generate_briefing_text(
                    institution_id=_bid,
                    company_id=_bc,
                    workspace_root=ws_path,
                    company_name=_bc,
                    institution_name=_bcanon or _bi,
                )
            st.markdown(text)
            st.download_button(
                "⬇️ 下载简报 (.md)",
                data=text.encode("utf-8"),
                file_name=f"briefing_{_bi}_{_bc}.md",
                mime="text/markdown",
                key="v102_download_briefing",
            )


def _render_practice_mode(company_id: str, ws_path: Path) -> None:
    """V10.3 P2.1 Tab 6：会前演练 — AI 扮投资人，问答评分。"""
    try:
        from practice_engine import (
            evaluate_answer_and_next,
            get_session_summary,
            start_practice_session,
        )
        from institution_registry import get_all as get_all_institutions
        from institution_registry import resolve as institution_resolve
    except ImportError as e:
        st.error(f"演练模式依赖缺失：{e}")
        return

    st.subheader("🎯 会前演练模式")
    st.caption("AI 扮演投资机构投资人，你作答，逐轮实时评分。模拟越接近真实场景，准备越充分。")

    # ── 选择机构 ────────────────────────────────────────────────────────────────
    all_institutions = get_all_institutions()
    inst_name_list = [r["canonical_name"] for r in all_institutions] if all_institutions else []

    col_inst, col_start = st.columns([3, 1])
    with col_inst:
        practice_inst = st.selectbox(
            "选择扮演的投资机构",
            ["（手动输入）"] + inst_name_list,
            key="practice_inst_select",
        )
        if practice_inst == "（手动输入）":
            practice_inst = st.text_input("机构名称", key="practice_inst_manual", placeholder="如：迪策资本")

    session_key = f"practice_session_{company_id}"

    with col_start:
        st.write("")  # 对齐间距
        st.write("")
        if st.button("▶️ 开始新演练", key="practice_start_btn", type="primary"):
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

    # ── 演练进行中 ──────────────────────────────────────────────────────────────
    if session_key not in st.session_state:
        st.info("👆 选择机构后点击「开始新演练」。")
        return

    sess = st.session_state[session_key]
    is_done = st.session_state.get(f"{session_key}_done", False)
    current_q = st.session_state.get(f"{session_key}_current_q", "")
    rounds = sess.get("rounds", [])

    # ── 历史轮次回顾 ──────────────────────────────────────────────────────────
    if rounds:
        with st.expander(f"📜 历史问答（{len(rounds)} 轮）", expanded=False):
            for i, r in enumerate(rounds, 1):
                score_color = "🟢" if r["score"] >= 75 else ("🟡" if r["score"] >= 60 else "🔴")
                st.markdown(f"**第 {i} 轮** {score_color} {r['score']}分")
                st.markdown(f"> **投资人**：{r['question']}")
                st.markdown(f"> **你**：{r['answer']}")
                st.caption(f"反馈：{r['feedback']}")
                st.divider()

    if is_done:
        # ── 会话结束：显示总结 ────────────────────────────────────────────────
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
        if summary["strong_areas"]:
            st.success("💪 **表现较好的问题：**")
            for q in summary["strong_areas"]:
                st.markdown(f"- {q}")
        return

    # ── 当前问题 + 输入框 ──────────────────────────────────────────────────────
    inst_name = sess.get("institution_profile", {}).get("canonical_name", "投资人")
    st.markdown(f"### 💬 **{inst_name}** 提问：")
    st.info(current_q)

    ANSWER_KEY = f"{session_key}_answer_input"
    st.text_area(
        "你的回答",
        key=ANSWER_KEY,
        height=120,
        placeholder="输入你的回答，尽量清晰、有数据支撑…",
    )

    col_submit, col_end = st.columns([2, 1])
    with col_submit:
        if st.button("✅ 提交回答", key=f"{session_key}_submit"):
            answer_text = (st.session_state.get(ANSWER_KEY) or "").strip()
            if not answer_text:
                st.warning("请先输入回答。")
            else:
                with st.spinner("AI 评分中…"):
                    try:
                        result = evaluate_answer_and_next(
                            sess, question=current_q, answer=answer_text
                        )
                        score = result["score"]
                        feedback = result["feedback"]
                        next_q = result["next_question"]
                        st.session_state[session_key] = result["updated_session"]
                        st.session_state[f"{session_key}_current_q"] = next_q

                        sc_label = "🟢" if score >= 75 else ("🟡" if score >= 60 else "🔴")
                        st.markdown(f"**{sc_label} 得分：{score} / 100**")
                        st.markdown(f"**反馈**：{feedback}")
                        st.rerun()
                    except Exception as ex:
                        st.error(f"评分失败：{ex}")

    with col_end:
        if st.button("🏁 结束演练", key=f"{session_key}_end"):
            st.session_state[f"{session_key}_done"] = True
            st.rerun()


def _preflight_subprocess_kwargs() -> dict:
    """Windows 下隐藏 FFmpeg 自检子进程控制台。"""
    kw: dict = {}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kw["startupinfo"] = si
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def _probe_ffmpeg_for_ui() -> tuple[bool, str]:
    """V4.8 / V6.2 侧边栏体检：FFmpeg 路径与 -version 探针。返回 (是否就绪, 失败简述)。"""
    try:
        import imageio_ffmpeg
    except ImportError as e:
        return False, f"缺少依赖 imageio-ffmpeg：{e}"

    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        return False, f"get_ffmpeg_exe 失败：{e}"

    if not ffmpeg_exe or not Path(ffmpeg_exe).is_file():
        return False, "未获得有效 FFmpeg 可执行路径"

    try:
        r = subprocess.run(
            [ffmpeg_exe, "-version"],
            capture_output=True,
            timeout=2,
            check=False,
            **_preflight_subprocess_kwargs(),
        )
    except FileNotFoundError:
        return False, "无法启动 FFmpeg 进程（FileNotFoundError）"
    except subprocess.TimeoutExpired:
        return False, "FFmpeg -version 超时（>2s）"

    if r.returncode != 0:
        tail = (r.stderr or b"")[:200].decode("utf-8", errors="replace")
        return False, tail or f"返回码 {r.returncode}"
    return True, ""


def _v3_clear_review_session_state() -> None:
    """新一轮生成前清空审查台相关状态，避免旧 widget 与草稿串台。"""
    for _k in list(st.session_state.keys()):
        if not isinstance(_k, str):
            continue
        if _k.startswith(
            ("v3", "report_draft_", "words_", "v3_ctx_", "v46_")
        ):
            del st.session_state[_k]
    st.session_state["v3_review_stems"] = []


def _v3_ensure_rid(rp: dict) -> str:
    rid = rp.get("_rid")
    if not rid:
        rid = uuid.uuid4().hex[:16]
        rp["_rid"] = rid
    return str(rid)


def _v3_init_header_widgets(stem: str, draft: dict) -> None:
    sa = draft.get("scene_analysis") or {}
    if f"v3_{stem}_scene_type" not in st.session_state:
        st.session_state[f"v3_{stem}_scene_type"] = sa.get("scene_type", "")
    if f"v3_{stem}_speaker_roles" not in st.session_state:
        st.session_state[f"v3_{stem}_speaker_roles"] = sa.get("speaker_roles", "")
    if f"v3_{stem}_total_score" not in st.session_state:
        st.session_state[f"v3_{stem}_total_score"] = int(draft.get("total_score", 0))
    if f"v3_{stem}_total_ded" not in st.session_state:
        st.session_state[f"v3_{stem}_total_ded"] = draft.get(
            "total_score_deduction_reason", ""
        )
    if f"v3_{stem}_highlights" not in st.session_state:
        _hl = draft.get("positive_highlights") or []
        st.session_state[f"v3_{stem}_highlights"] = "\n".join(_hl) if _hl else ""


def _v3_init_risk_widgets(stem: str, draft: dict) -> None:
    for rp in draft.get("risk_points") or []:
        rid = _v3_ensure_rid(rp)
        base = f"v3rp_{stem}_{rid}"
        refine_pending_key = f"v3rp_refine_pending_{stem}_{rid}"

        # ── 第二道防线：处理待注入的精炼结果（双 Key 安全注入模式）──
        if refine_pending_key in st.session_state:
            refined: dict = st.session_state.pop(refine_pending_key)
            # 删除旧 widget-managed keys，强制下次渲染以新值重新初始化
            for suffix in ("_lvl", "_ps", "_t1", "_t2", "_im", "_ded", "_ort",
                           "_needs_refine", "_refine_note"):
                st.session_state.pop(f"{base}{suffix}", None)
            # 注入精炼后的值
            st.session_state[f"{base}_lvl"] = refined.get("risk_level", rp.get("risk_level", "一般"))
            st.session_state[f"{base}_ps"] = refined.get("problem_summary", rp.get("problem_summary", ""))
            st.session_state[f"{base}_t1"] = refined.get("tier1_general_critique", "")
            st.session_state[f"{base}_t2"] = refined.get("tier2_qa_alignment", "")
            st.session_state[f"{base}_im"] = refined.get("improvement_suggestion", "")
            st.session_state[f"{base}_ded"] = refined.get("deduction_reason", "")
            st.session_state[f"{base}_ort"] = refined.get("original_text", "")
            st.session_state[f"{base}_needs_refine"] = False
            st.session_state[f"{base}_refine_note"] = ""
            # 同步回 draft（供后续 _v3_build_report_dict_from_widgets 读取）
            for field in ("risk_level", "tier1_general_critique", "tier2_qa_alignment",
                          "improvement_suggestion", "deduction_reason", "original_text",
                          "score_deduction", "needs_refinement", "refinement_note"):
                if field in refined:
                    rp[field] = refined[field]
            continue  # 已处理，跳过正常 if-not-in-state 初始化

        # ── 正常初始化（仅当 key 尚未被 widget 托管时赋初值）──
        if f"{base}_lvl" not in st.session_state:
            st.session_state[f"{base}_lvl"] = rp.get("risk_level", "一般")
        if f"{base}_ps" not in st.session_state:
            st.session_state[f"{base}_ps"] = rp.get("problem_summary", "")
        if f"{base}_im" not in st.session_state:
            st.session_state[f"{base}_im"] = rp.get("improvement_suggestion", "")
        if f"{base}_ort" not in st.session_state:
            st.session_state[f"{base}_ort"] = rp.get("original_text", "")

        # V8.0 新增：精炼标记与批示
        if f"{base}_needs_refine" not in st.session_state:
            st.session_state[f"{base}_needs_refine"] = bool(rp.get("needs_refinement", False))
        if f"{base}_refine_note" not in st.session_state:
            st.session_state[f"{base}_refine_note"] = rp.get("refinement_note", "")


def _v3_snapshot_report_for_draft(stem: str) -> dict:
    """将审查台控件值合并为可恢复的 report 字典，并保留各 risk_point 的 _rid。"""
    draft = st.session_state.get(f"report_draft_{stem}")
    if not draft:
        return {}
    built = _v3_build_report_dict_from_widgets(stem)
    old_rps = draft.get("risk_points") or []
    new_rps = built.get("risk_points") or []
    for i, nr in enumerate(new_rps):
        rid = old_rps[i].get("_rid") if i < len(old_rps) else None
        if rid:
            nr["_rid"] = rid
        else:
            nr.setdefault("_rid", uuid.uuid4().hex[:16])
    built["risk_points"] = new_rps
    return built


def _v3_build_report_dict_from_widgets(stem: str) -> dict:
    draft = st.session_state.get(f"report_draft_{stem}")
    if not draft:
        return {}
    rps_out: list[dict] = []
    for rp in draft.get("risk_points") or []:
        rid = rp.get("_rid")
        if not rid:
            continue
        base = f"v3rp_{stem}_{rid}"
        rps_out.append(
            {
                "risk_level": st.session_state.get(f"{base}_lvl", rp.get("risk_level", "一般")),
                # problem_summary 可编辑，从 widget session_state 读取
                "problem_summary": st.session_state.get(
                    f"{base}_ps", rp.get("problem_summary", "")
                ),
                # tier1/tier2/deduction 只读，直接从 draft 数据读取（不经 widget session_state）
                "tier1_general_critique": rp.get("tier1_general_critique", ""),
                "tier2_qa_alignment": rp.get("tier2_qa_alignment", ""),
                "improvement_suggestion": st.session_state.get(
                    f"{base}_im", rp.get("improvement_suggestion", "")
                ),
                "start_word_index": int(rp.get("start_word_index", 0)),
                "end_word_index": int(rp.get("end_word_index", 0)),
                "deduction_reason": rp.get("deduction_reason", ""),
                "original_text": st.session_state.get(
                    f"{base}_ort", rp.get("original_text", "")
                ),
                "score_deduction": int(rp.get("score_deduction", 0) or 0),
                "is_manual_entry": bool(rp.get("is_manual_entry", False)),
                "needs_refinement": bool(
                    st.session_state.get(f"{base}_needs_refine", rp.get("needs_refinement", False))
                ),
                "refinement_note": st.session_state.get(
                    f"{base}_refine_note", rp.get("refinement_note", "")
                ),
            }
        )
    ts = st.session_state.get(f"v3_{stem}_total_score", draft.get("total_score", 0))
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        ts_int = int(draft.get("total_score", 0))
    ts_int = max(0, min(100, ts_int))
    # positive_highlights: 编辑区存为换行分隔文本，输出为字符串列表
    _hl_raw = st.session_state.get(f"v3_{stem}_highlights", "")
    _hl_list = [line.strip() for line in (_hl_raw or "").splitlines() if line.strip()]
    return {
        "scene_analysis": {
            "scene_type": st.session_state.get(
                f"v3_{stem}_scene_type",
                (draft.get("scene_analysis") or {}).get("scene_type", ""),
            ),
            "speaker_roles": st.session_state.get(
                f"v3_{stem}_speaker_roles",
                (draft.get("scene_analysis") or {}).get("speaker_roles", ""),
            ),
        },
        "total_score": ts_int,
        "total_score_deduction_reason": st.session_state.get(
            f"v3_{stem}_total_ded", draft.get("total_score_deduction_reason", "")
        ),
        "positive_highlights": _hl_list,
        "risk_points": rps_out,
    }


def _v3_finalize_stem(stem: str) -> tuple[Path, int]:
    ctx = st.session_state[f"v3_ctx_{stem}"]
    # 锁定时若生成时未选项目，用当前侧栏的选择实时补全 company_id
    if not (ctx.get("company_id") or "").strip():
        _sidebar_cid = st.session_state.get("company_selector", "")
        if _sidebar_cid and _sidebar_cid != "__new__":
            ctx["company_id"] = _sidebar_cid
            st.session_state[f"v3_ctx_{stem}"] = ctx
    # 深拷贝后再校验；JSON 正文保持审查台明文，仅外发 HTML 文件名做 DLP 脱敏
    payload = copy.deepcopy(_v3_build_report_dict_from_widgets(stem))
    report = AnalysisReport.model_validate(payload)
    words = [
        TranscriptionWord.model_validate(x) for x in st.session_state[f"words_{stem}"]
    ]
    report_for_disk = apply_asr_original_text_override(report, words)
    Path(ctx["analysis_json"]).write_text(
        json.dumps(report_for_disk.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    proj = (ctx.get("project_name") or "").strip() or "项目"
    iv = (ctx.get("interviewee") or "").strip() or "发言人"
    proj_d = desensitize_text(proj, is_person=False)
    iv_d = desensitize_text(iv, is_person=True)
    stem_part = safe_fs_segment(Path(ctx["audio_path"]).stem)
    html_name = (
        f"{safe_fs_segment(proj_d)}-{safe_fs_segment(iv_d)}_{stem_part}_复盘报告.html"
    )
    html_path = Path(ctx["audio_path"]).parent / html_name

    hopt = HtmlExportOptions(
        footer_watermark=ctx.get("watermark") or "",
        content_replace_map=ctx.get("html_mask_map") if ctx.get("mask_html_body") else None,
        show_generated_timestamp=True,
    )
    generate_html_report(
        Path(ctx["audio_path"]),
        words,
        report_for_disk,
        html_path,
        export_options=hopt,
    )
    final = html_path.resolve()
    st.session_state[f"v46_preview_html_{stem}"] = str(final)
    harvest_n = _v86_harvest_finalize_if_needed(stem, payload)
    # V10.3 P1.2：从 session_state 读取融资结果写入 ctx
    _fo = st.session_state.get(f"fundraising_outcome_{stem}", "（未记录）")
    ctx["fundraising_outcome"] = "" if _fo == "（未记录）" else _fo
    ctx["fundraising_amount"] = st.session_state.get(f"fundraising_amount_{stem}", "").strip()
    ctx["fundraising_valuation"] = st.session_state.get(f"fundraising_valuation_{stem}", "").strip()
    # V10.3 P3.2：投资人姓名（Partner 画像）
    ctx["investor_name"] = (st.session_state.get("v103_investor_name") or "").strip()
    # V10.1：locked 覆写 analytics JSON（覆盖 draft，status="locked"）
    analytics_path = export_analytics(report_for_disk, ctx, status="locked")

    # V10.2：机构会话计数 + GitHub 异步推送
    _iid = (ctx.get("institution_id") or "").strip()
    _cid = (ctx.get("company_id") or "").strip()
    if _iid:
        try:
            institution_inc_session(_iid)
        except Exception:
            pass
        if analytics_path:
            try:
                github_sync_analytics(analytics_path, _cid)
                github_sync_institutions()
            except Exception:
                pass

    return final, harvest_n


def _v3_render_single_stem_review(stem: str) -> None:
    draft = st.session_state.get(f"report_draft_{stem}")
    if not draft:
        st.warning("未找到该条的审查草稿。")
        return
    _v3_init_header_widgets(stem, draft)
    _v3_init_risk_widgets(stem, draft)

    st.markdown(f"**录音主文件名：** `{stem}`")

    st.text_area(
        "场景推断（可编辑）",
        key=f"v3_{stem}_scene_type",
        height=68,
    )
    st.text_area(
        "身份与氛围（可编辑）",
        key=f"v3_{stem}_speaker_roles",
        height=68,
    )
    c1, c2 = st.columns(2)
    with c1:
        st.number_input(
            "综合得分（0–100）",
            min_value=0,
            max_value=100,
            key=f"v3_{stem}_total_score",
        )
    with c2:
        st.caption("总分扣分说明见下方文本框。")
    st.text_area(
        "总分扣分说明 / 与 QA 对照",
        key=f"v3_{stem}_total_ded",
        height=100,
    )

    # ── V10.5 亮点展示区（平衡评估）──────────────────────────────────────────
    _hl_draft = (draft.get("positive_highlights") or [])
    if _hl_draft:
        with st.expander("✅ 表现亮点（AI 发现的正面表现）", expanded=True):
            st.caption("以下为 AI 识别出的发言亮点，可手动编辑（一行一条）后锁定写入报告。")
            st.text_area(
                "亮点列表（一行一条）",
                key=f"v3_{stem}_highlights",
                height=120,
                label_visibility="collapsed",
            )
    else:
        with st.expander("✅ 表现亮点（可手动填写）", expanded=False):
            st.caption("AI 未识别到明显亮点，或本次分析早于 V10.5。可手动填写（一行一条）。")
            st.text_area(
                "亮点列表（一行一条）",
                key=f"v3_{stem}_highlights",
                height=80,
                label_visibility="collapsed",
            )

    words_raw = st.session_state.get(f"words_{stem}") or []
    words_models = [TranscriptionWord.model_validate(x) for x in words_raw]
    audio_fs_path = Path(st.session_state[f"v3_ctx_{stem}"]["audio_path"])

    st.subheader("翻车片段与逐字稿")
    rps = st.session_state[f"report_draft_{stem}"].get("risk_points") or []

    for idx, rp in enumerate(list(rps)):
        rid = _v3_ensure_rid(rp)
        is_manual = bool(rp.get("is_manual_entry", False))
        with st.expander(
            f"片段 #{idx + 1} · {'人工增补' if is_manual else 'AI 提取'} · {rid}",
            expanded=False,
        ):
            # ── 默认展示：4 个核心字段 ──
            st.selectbox(
                "严重程度",
                options=["严重", "一般", "轻微"],
                key=f"v3rp_{stem}_{rid}_lvl",
            )

            # 问题背景：可编辑文本（problem_summary 字段，事实导向 30 字内）
            st.text_area(
                "问题背景（可编辑）",
                key=f"v3rp_{stem}_{rid}_ps",
                height=68,
                help="事实导向：发言人说了什么 + 矛盾点在哪，30字内。由 AI 自动填写，可手动修正。",
            )

            st.text_area(
                "改进建议",
                key=f"v3rp_{stem}_{rid}_im",
                height=80,
            )
            st.text_area(
                "🎙️ 发言人口述实录",
                key=f"v3rp_{stem}_{rid}_ort",
                height=100,
                help="模型洗稿后的口述实录，可编辑；将写入 HTML「发言人口述实录」区块。",
            )

            # ── 音频试听 ──
            if not is_manual and audio_fs_path.is_file():
                sw, ew = int(rp.get("start_word_index", 0)), int(rp.get("end_word_index", 0))
                blob = snippet_audio_mp3_bytes(audio_fs_path, words_models, sw, ew)
                if blob:
                    st.audio(io.BytesIO(blob), format="audio/mpeg")
                else:
                    st.caption("（无法生成该片段试听，请检查词索引）")
            elif is_manual:
                st.caption("人工条目无词级切片与自动试听。")

            # ── AI 推理链（只读，不可编辑）──────────────────────────────────
            if st.toggle("🔍 AI 推理（只读）", key=f"v3rp_{stem}_{rid}_expert_view"):
                _t1_txt = rp.get("tier1_general_critique", "")
                _t2_txt = rp.get("tier2_qa_alignment", "")
                _ded_txt = rp.get("deduction_reason", "")
                st.caption("**Tier 1 · 商业逻辑顶尖视角**")
                st.info(_t1_txt or "—")
                if _t2_txt:
                    st.caption("**Tier 2 · QA 口径对齐**")
                    st.info(_t2_txt)
                if _ded_txt:
                    st.caption("**扣分依据**")
                    st.info(_ded_txt)

            # ── V8.0 第二道防线：精炼标记与批示 ──
            st.divider()
            col_chk, col_note = st.columns([1, 4])
            with col_chk:
                st.checkbox(
                    "🔬 标记需精炼",
                    key=f"v3rp_{stem}_{rid}_needs_refine",
                    help="勾选后点击下方「局部重写全部选中项」按钮，AI 将深度重写该条目。",
                )
            with col_note:
                st.text_input(
                    "批示意见（给精炼 AI 的指令）",
                    key=f"v3rp_{stem}_{rid}_refine_note",
                    placeholder="例如：重点检验财务数据一致性，改进建议要更具体",
                    help="精炼时注入 LLM 的方向指令，留空则 AI 自主深化。",
                )

            if st.button(
                "🗑️ 删除此片段",
                key=f"v3del_{stem}_{rid}",
            ):
                st.session_state[f"report_draft_{stem}"]["risk_points"] = [
                    x
                    for x in st.session_state[f"report_draft_{stem}"]["risk_points"]
                    if x.get("_rid") != rid
                ]
                st.rerun()

    # ── V8.0 第二道防线：批量精炼按钮 ──
    checked_rids = [
        rp.get("_rid") for rp in (st.session_state.get(f"report_draft_{stem}", {}).get("risk_points") or [])
        if st.session_state.get(f"v3rp_{stem}_{rp.get('_rid')}_needs_refine", False)
        and rp.get("_rid")
    ]
    if checked_rids:
        st.info(f"🔬 已勾选 **{len(checked_rids)}** 个条目待精炼")
    if st.button(
        "🔬 局部重写全部选中项",
        key=f"v3_batch_refine_{stem}",
        disabled=not checked_rids,
        help="对所有勾选「标记需精炼」的条目依次调用 LLM 深度重写，不影响未勾选条目。",
    ):
        ctx = st.session_state.get(f"v3_ctx_{stem}") or {}
        words_raw = st.session_state.get(f"words_{stem}") or []
        words_models = [TranscriptionWord.model_validate(x) for x in words_raw]
        draft = st.session_state.get(f"report_draft_{stem}") or {}
        rp_by_rid = {rp.get("_rid"): rp for rp in draft.get("risk_points") or []}
        explicit_ctx = {
            "biz_type": ctx.get("biz_type", ""),
            "exact_roles": ctx.get("exact_roles", ""),
            "project_name": ctx.get("project_name", ""),
            "interviewee": ctx.get("interviewee", ""),
        }
        errors_refine = []
        with st.status(f"🔬 正在精炼 {len(checked_rids)} 个选中条目...", expanded=True) as refine_status:
            for rid in checked_rids:
                rp = rp_by_rid.get(rid)
                if not rp:
                    continue
                note = st.session_state.get(f"v3rp_{stem}_{rid}_refine_note", "")
                refine_status.update(label=f"⏱️ 正在精炼：{rid[:8]}…", state="running")
                refine_status.write(f"批示意见：{note or '（无，AI 自主深化）'}")
                try:
                    refined_rp = refine_risk_point(
                        rp, words_models,
                        model_choice="deepseek",
                        explicit_context=explicit_ctx,
                        refinement_note=note,
                    )
                    st.session_state[f"v3rp_refine_pending_{stem}_{rid}"] = refined_rp.model_dump()
                    refine_status.write(f"✅ {rid[:8]} 精炼完成")
                except Exception as ex:
                    errors_refine.append(f"{rid[:8]}: {ex!s}")
                    refine_status.write(f"❌ {rid[:8]} 精炼失败: {ex!s}")
            if errors_refine:
                refine_status.update(label="⚠️ 部分精炼失败，其余已完成", state="error")
            else:
                refine_status.update(label="✅ 全部选中条目精炼完成", state="complete")
        st.rerun()

    # ── V8.0 第三道防线：新增遗漏痛点（含 LLM 润色）──
    with st.expander("➕ 新增遗漏痛点", expanded=False):
        st.caption(
            "**第三道防线**：若 AI 初稿彻底漏掉了某个重点，"
            "可在此手动输入业务逻辑，选择「仅保存」或「AI 润色后插入」。"
        )
        st.text_input("标题 / Tier1 要点", key=f"v3man_{stem}_t1")
        st.text_area("问题描述 / Tier2", key=f"v3man_{stem}_t2", height=80)
        st.text_area("改进建议（可选，AI 润色时会自动补全）", key=f"v3man_{stem}_im", height=80)
        col_save, col_polish = st.columns(2)
        with col_save:
            if st.button("💾 仅保存（不调用 AI）", key=f"v3man_{stem}_save"):
                nt = (st.session_state.get(f"v3man_{stem}_t1") or "").strip()
                if not nt:
                    st.error("请至少填写标题/Tier1 要点。")
                else:
                    entry = {
                        "risk_level": "轻微",
                        "tier1_general_critique": nt,
                        "tier2_qa_alignment": st.session_state.get(f"v3man_{stem}_t2", ""),
                        "improvement_suggestion": st.session_state.get(f"v3man_{stem}_im", ""),
                        "original_text": "",
                        "start_word_index": 0,
                        "end_word_index": 0,
                        "score_deduction": 0,
                        "deduction_reason": "人工录入",
                        "is_manual_entry": True,
                        "needs_refinement": False,
                        "refinement_note": "",
                        "_rid": uuid.uuid4().hex[:16],
                    }
                    st.session_state[f"report_draft_{stem}"]["risk_points"].append(entry)
                    st.rerun()
        with col_polish:
            if st.button("✨ AI 润色后插入", key=f"v3man_{stem}_polish", type="primary"):
                nt = (st.session_state.get(f"v3man_{stem}_t1") or "").strip()
                t2 = (st.session_state.get(f"v3man_{stem}_t2") or "").strip()
                raw_desc = "\n".join(filter(None, [nt, t2]))
                if not raw_desc:
                    st.error("请至少填写标题/Tier1 要点后再调用 AI 润色。")
                else:
                    ctx = st.session_state.get(f"v3_ctx_{stem}") or {}
                    explicit_ctx = {
                        "biz_type": ctx.get("biz_type", ""),
                        "exact_roles": ctx.get("exact_roles", ""),
                        "project_name": ctx.get("project_name", ""),
                        "interviewee": ctx.get("interviewee", ""),
                    }
                    with st.spinner("AI 正在润色并结构化该遗漏点…"):
                        try:
                            polished_rp = polish_manual_risk_point(
                                raw_desc,
                                model_choice="deepseek",
                                explicit_context=explicit_ctx,
                            )
                            entry = polished_rp.model_dump()
                            entry["_rid"] = uuid.uuid4().hex[:16]
                            st.session_state[f"report_draft_{stem}"]["risk_points"].append(entry)
                            st.success("✅ AI 润色完成，已插入审查台！")
                            st.rerun()
                        except Exception as ex:
                            st.error(f"AI 润色失败：{ex!s}")

    # ── V10.3 P1.2 融资结果记录（锁定前可选填） ─────────────────────────────────
    with st.expander("📊 本次会谈融资进展（可选，锁定后写入数据飞轮）", expanded=False):
        _outcome_key = f"fundraising_outcome_{stem}"
        _amount_key  = f"fundraising_amount_{stem}"
        _val_key     = f"fundraising_valuation_{stem}"
        outcome_opt = st.radio(
            "本次会谈后融资状态",
            options=["（未记录）", "进行中", "已成功", "未推进"],
            index=0,
            key=_outcome_key,
            horizontal=True,
        )
        if outcome_opt == "已成功":
            c_a, c_v = st.columns(2)
            c_a.text_input(
                "融资金额（万元，选填）",
                key=_amount_key,
                placeholder="如：5000",
            )
            c_v.text_input(
                "融资后估值（万元，选填）",
                key=_val_key,
                placeholder="如：80000",
            )

    if st.button(
        "✅ 确认无误，锁定并生成最终版 HTML 报告",
        type="primary",
        key=f"v3finalize_{stem}",
    ):
        _toast = getattr(st, "toast", None)
        try:
            with st.spinner("正在生成 HTML 报告，请稍候…"):
                final_html, harvest_n = _v3_finalize_stem(stem)
            st.success(
                f"✅ 已锁定：**{stem}**\n"
                f"JSON 与 HTML 已写入归档目录。\n"
                f"HTML（脱敏文件名）：`{final_html.name}`"
            )
            if callable(_toast):
                _toast(f"✅ HTML 报告已生成：{final_html.name}", icon="✅")
            ctx = st.session_state.get(f"v3_ctx_{stem}") or {}
            iv = (ctx.get("interviewee") or "").strip() or ""
            cid = (ctx.get("company_id") or "").strip()
            # 若生成时未选项目，但锁定时用户已切换侧栏，实时补 company_id
            if not cid:
                _sidebar_cid = st.session_state.get("company_selector", "")
                if _sidebar_cid and _sidebar_cid != "__new__":
                    cid = _sidebar_cid
                    ctx["company_id"] = cid
                    st.session_state[f"v3_ctx_{stem}"] = ctx
            if harvest_n > 0:
                msg = f"🌱 已为「{iv or '该高管'}」自动提炼 {harvest_n} 条新经验入库，飞轮运转中！"
                if callable(_toast):
                    _toast(msg, icon="🌱")
                else:
                    st.success(msg)
            else:
                if not cid:
                    st.warning(
                        "⚠️ **记忆未入库**：侧边栏未选择项目（当前为「新建项目」状态）。"
                        "请在侧边栏选择或创建一个项目后重新生成，记忆将自动归入该项目。"
                    )
                elif not iv or iv in ("未指定", "default"):
                    st.warning(
                        "⚠️ **记忆未入库**：被访谈人字段为空或「未指定」。"
                        "请在上方录音配置中填写姓名后重新运行。"
                    )
                else:
                    st.caption(
                        f"💡 本次锁定未产生新记忆（项目：{cid} · 被访谈人：{iv}）。"
                        "记忆仅在**你修改了 AI 初稿内容**时才会自动提炼——"
                        "如改了「改进建议」或「原文引用」，下次锁定即可沉淀。"
                    )
        except Exception as ex:
            _err_msg = f"导出失败：{ex!s}"
            st.error(_err_msg)
            if callable(_toast):
                _toast(f"❌ {_err_msg}", icon="❌")
            logging.getLogger("ai_pitch_coach.ui").exception("锁定导出异常")

    ph = st.session_state.get(f"v46_preview_html_{stem}")
    if ph and os.name == "nt" and Path(ph).is_file():
        c_open1, c_open2 = st.columns(2)
        with c_open1:
            if st.button("📂 打开报告所在文件夹", key=f"v46dir_{stem}"):
                os.startfile(str(Path(ph).parent))
        with c_open2:
            if st.button("🌐 立即预览报告", key=f"v46open_{stem}"):
                os.startfile(ph)


def _v7_collect_draft_payload() -> dict:
    """从当前 session_state 收集审查台快照，供本地草稿箱落盘。"""
    stems: list[str] = st.session_state.get("v3_review_stems") or []
    blob: dict = {
        "version": 7,
        "session_id": str(st.session_state.get("session_id") or ""),
        "v3_review_stems": list(stems),
        "reports": {},
        "words": {},
        "ctx": {},
    }
    for stem in stems:
        try:
            blob["reports"][stem] = _v3_snapshot_report_for_draft(stem)
        except Exception:
            blob["reports"][stem] = copy.deepcopy(
                st.session_state.get(f"report_draft_{stem}") or {}
            )
        blob["words"][stem] = copy.deepcopy(st.session_state.get(f"words_{stem}") or [])
        blob["ctx"][stem] = copy.deepcopy(st.session_state.get(f"v3_ctx_{stem}") or {})
    return blob


def _v7_apply_draft_payload(data: dict) -> None:
    stems = data.get("v3_review_stems") or []
    reports = data.get("reports") or {}
    words = data.get("words") or {}
    ctx = data.get("ctx") or {}
    for stem in stems:
        if stem in reports:
            st.session_state[f"report_draft_{stem}"] = copy.deepcopy(reports[stem])
            if f"v3_initial_report_{stem}" not in st.session_state:
                st.session_state[f"v3_initial_report_{stem}"] = copy.deepcopy(reports[stem])
        if stem in words:
            st.session_state[f"words_{stem}"] = copy.deepcopy(words[stem])
        if stem in ctx:
            st.session_state[f"v3_ctx_{stem}"] = copy.deepcopy(ctx[stem])
    st.session_state["v3_review_stems"] = list(stems)
    sid = data.get("session_id")
    if sid:
        st.session_state["session_id"] = str(sid)


def _v7_latest_draft_session_id() -> str | None:
    """在可用草稿中选最近修改的一个 session_id。"""
    ids = list_available_drafts()
    best: str | None = None
    best_t = -1.0
    root = get_writable_app_root() / ".drafts"
    for sid in ids:
        p = root / f"draft_{sid}.json"
        try:
            t = p.stat().st_mtime
        except OSError:
            continue
        if t > best_t:
            best_t = t
            best = sid
    return best


def _v3_render_review_workbench() -> None:
    stems: list[str] = st.session_state.get("v3_review_stems") or []
    if not stems:
        return
    sid = str(st.session_state.get("session_id") or "").strip()
    if sid:
        try:
            save_draft(sid, _v7_collect_draft_payload())
        except Exception:
            logging.getLogger("ai_pitch_coach.ui").exception("静默保存草稿失败")
    st.divider()
    st.subheader("🔍 报告审查与人工编辑台（V3.0）")
    st.caption("✅ 数据已自动静默保存至本地草稿箱")
    st.caption(
        "以下为 AI 初稿；请逐条核对、编辑或删除片段，并可人工增补。"
        "仅当点击「锁定并生成最终版 HTML」后才会写入最终 HTML。"
    )
    # ── 记忆归属预告 ──────────────────────────────────────────────────────────
    # 在审查开始前告知用户，修改内容后锁定，记忆将归入哪个项目/被访谈人
    _sidebar_cid_now = st.session_state.get("company_selector", "")
    _sidebar_cid_now = "" if _sidebar_cid_now == "__new__" else _sidebar_cid_now
    _mem_notices = []
    for _stem in stems:
        _ctx = st.session_state.get(f"v3_ctx_{_stem}") or {}
        _cid = (_ctx.get("company_id") or "").strip()
        # 若生成时未选项目，实时回填当前侧栏选择（预告准确）
        if not _cid and _sidebar_cid_now:
            _cid = _sidebar_cid_now
        _iv = (_ctx.get("interviewee") or "").strip()
        if _cid and _iv and _iv not in ("未指定", "default"):
            _mem_notices.append(f"**{_iv}** → 项目「{_cid}」")
        elif not _cid:
            _mem_notices.append("❌ 未选项目（请在侧栏选择项目后再锁定）")
        elif not _iv or _iv in ("未指定", "default"):
            _mem_notices.append("❌ 被访谈人未填写（记忆将无法入库）")
    if _mem_notices:
        _notice_text = "　｜　".join(_mem_notices)
        st.info(f"🧠 **锁定后记忆将归入**：{_notice_text}"
                "　*（修改 AI 初稿内容后锁定才会产生新记忆）*")
    if len(stems) == 1:
        _v3_render_single_stem_review(stems[0])
    else:
        tabs = st.tabs(stems)
        for tab, stem in zip(tabs, stems):
            with tab:
                _v3_render_single_stem_review(stem)


def _normalize_sniper_editor_df(df):
    """兼容 V7.5 前列名「人工疑点」→「找茬疑点」，避免会话里旧表丢列。"""
    if df is None or not hasattr(df, "columns"):
        return df
    if "人工疑点" in df.columns and "找茬疑点" not in df.columns:
        return df.rename(columns={"人工疑点": "找茬疑点"})
    return df


def _batch_sniper_targets_json(idx: int) -> str:
    """从狙击表读取数据，序列化为 JSON（quote/reason）。
    优先读 data_editor 返回值缓存（result_key，完整 DataFrame），兜底读初始数据（init_key）。
    注：ed_key 存的是 Streamlit delta dict，不是 DataFrame，不再从中读取。
    ⚠️ 严禁用 `or` 运算符判断 DataFrame：DataFrame.__bool__() 会抛 ValueError。
       必须用 `is None` 判断。
    """
    result_key = f"batch_sniper_result_{idx}"
    init_key = f"batch_sniper_init_{idx}"
    # P0 修复：`or` 运算符对 DataFrame 调用 __bool__() → ValueError "ambiguous"
    # 改为 is None 判断，安全选取优先级更高的 result DataFrame
    df = st.session_state.get(result_key)
    if df is None:
        df = st.session_state.get(init_key)
    df = _normalize_sniper_editor_df(df)
    if df is None:
        return "[]"
    if not hasattr(df, "iterrows"):
        return "[]"
    rows_out: list[dict[str, str]] = []
    for _, row in df.iterrows():
        q = str(row.get("原文引用", "") or "").strip()
        r = str(row.get("找茬疑点", row.get("人工疑点", "")) or "").strip()
        if q or r:
            rows_out.append({"quote": q, "reason": r})
    return json.dumps(rows_out, ensure_ascii=False)


def _v71_transcribe_upload_to_plain(
    uf,
    *,
    on_line: Callable[[str], None] | None = None,
) -> str:
    """仅转写上传文件为可读纯文本，并将结果存入 ASR 内存缓存。
    缓存命中时直接返回，跳过云端调用；点击「生成报告」时主流程可复用同一缓存。

    大文件（≥10MB）与「生成报告」一致：先经 ``smart_compress_media`` 再送转写。
    ``on_line`` 可选，用于在 UI 中展示原始大小 / 压缩后大小（与批量流水线口径一致）。
    """
    def _ln(msg: str) -> None:
        if on_line:
            on_line(msg)

    raw = uf.getvalue()
    orig_len = len(raw)
    orig_mb = orig_len / (1024 * 1024)
    file_hash = _file_md5(raw)
    asr_cache: dict = st.session_state.setdefault("asr_cache", {})
    if file_hash in asr_cache:
        _ln(
            f"✅ 命中本页会话内的转写缓存（原文件约 **{orig_mb:.2f} MB**），"
            "跳过压缩与云端转写，直接展示已缓存文字稿。"
        )
        return asr_cache[file_hash]["plain"]

    _ln(f"📥 原始上传：**{orig_mb:.2f} MB** · `{uf.name}`")

    suffix = Path(uf.name).suffix or ".wav"
    f1 = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f1.write(raw)
    f1.close()
    paths: list[Path] = [Path(f1.name)]
    work = paths[0]
    try:
        if orig_len < 10 * 1024 * 1024:
            _ln(
                "✅ 体积 **小于 10 MB**，免压缩直通转写（与「开始生成复盘报告」一致）。"
            )
        else:
            _ln("⚙️ 已启动 **智能音频网关**（抽离视频轨 + 语音降采样），与生成报告流程一致…")
            cres = smart_compress_media(raw, filename_hint=uf.name)
            if cres.did_compress:
                new_mb = len(cres.data) / (1024 * 1024)
                ratio = (1.0 - len(cres.data) / max(1, orig_len)) * 100.0
                _ln(
                    f"✅ **压缩完成**：**{orig_mb:.2f} MB → {new_mb:.2f} MB** "
                    f"（体积约缩减 **{ratio:.1f}%**），随后用压缩稿调用云端转写。"
                )
                f2 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
                f2.write(cres.data)
                f2.close()
                p2 = Path(f2.name)
                paths.append(p2)
                try:
                    work.unlink(missing_ok=True)
                except OSError:
                    pass
                paths.remove(work)
                work = p2
            else:
                _ln(
                    "⚠️ 压缩未生效或已安全回退，将使用 **原文件** 调用转写（与批量流程回退策略一致）。"
                )
        _ln("⏱️ 正在调用云端转写，请稍候…")
        words = transcribe_audio(work, out_json_path=None)
        plain = format_transcript_plain_by_speaker(words)
        asr_cache[file_hash] = {
            "words": [w.model_dump() for w in words],
            "plain": plain,
        }
        return plain
    finally:
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def _parse_filename_mask_lines(raw: str) -> dict[str, str]:
    """解析侧边栏「每行：原名⇒代号」或 原名=>代号 或 原名=代号。"""
    out: dict[str, str] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        for sep in ("⇒", "=>", "->", "="):
            if sep in s:
                a, b = s.split(sep, 1)
                k, v = a.strip(), b.strip()
                if k and v:
                    out[k] = v
                break
    return out


def _merge_html_filename_masks(sidebar_text: str) -> dict[str, str]:
    merged = dict(DEFAULT_HTML_FILENAME_MASKS)
    merged.update(_parse_filename_mask_lines(sidebar_text))
    return merged


def _env_configured(key: str) -> bool:
    v = os.getenv(key)
    return bool(v and str(v).strip())


def _qa_uploader_key_suffix(audio_name: str) -> str:
    """稳定短后缀，避免特殊字符进入 Streamlit widget key。"""
    return hashlib.sha256((audio_name or "").encode("utf-8")).hexdigest()[:12]


def _file_md5(data: bytes) -> str:
    """计算文件内容 MD5，用作 ASR 内存缓存键（非安全场景，仅做内容去重）。"""
    return hashlib.md5(data).hexdigest()  # noqa: S324


def _as_upload_list(x: object) -> list:
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


def _ping_dashscope_compatible(api_key: str) -> tuple[bool, str]:
    """阿里云百炼 DashScope OpenAI 兼容接口极简问候（max_tokens=5）。"""
    key = (api_key or "").strip()
    if not key:
        return False, "Key 为空"
    try:
        client = OpenAI(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=key,
        )
        r = client.chat.completions.create(
            model="qwen-turbo",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
            temperature=0,
        )
        if r.choices:
            return True, ""
        return False, "响应无 choices"
    except APIError as e:
        return False, f"APIError: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _ping_deepseek(api_key: str) -> tuple[bool, str]:
    """DeepSeek 官方 OpenAI 兼容接口极简问候（max_tokens=5）。"""
    key = (api_key or "").strip()
    if not key:
        return False, "Key 为空"
    try:
        client = OpenAI(
            base_url="https://api.deepseek.com",
            api_key=key,
        )
        r = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
            temperature=0,
        )
        if r.choices:
            return True, ""
        return False, "响应无 choices"
    except APIError as e:
        return False, f"APIError: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ── FOS Sprint 6：投资人匹配页面 ──────────────────────────────────────────────
def _render_investor_matcher_page(company_id: str, workspace: str) -> None:
    """FOS Sprint 6 — 投资人匹配引擎页面。"""
    try:
        from investor_matcher import (
            CompanySnapshot, match_institutions, format_match_report
        )
    except ImportError:
        st.error("investor_matcher 模块未加载，请确认 src/investor_matcher.py 存在。")
        return

    st.header("🎯 投资人匹配")
    st.caption("基于已积累的机构画像，为目标公司推荐最匹配的投资机构。数据越积累越准。")

    col1, col2 = st.columns([1, 1])
    with col1:
        company_name = st.text_input("被服务公司名称", value=company_id or "", key="matcher_company_name")
        stage = st.selectbox("当前融资阶段", ["", "天使轮", "Pre-A", "A轮", "A+轮", "B轮", "B+轮", "C轮", "D轮", "上市前", "战略轮"], key="matcher_stage")
        revenue = st.number_input("营收（万元，0=未知）", min_value=0, value=0, key="matcher_revenue")
    with col2:
        industry_tags_raw = st.text_area("行业标签（逗号分隔）", placeholder="如：军工电子, AI, RTOS, 低空经济", key="matcher_industry", height=80)
        model_tags_raw = st.text_area("商业模式标签（逗号分隔）", placeholder="如：ToB, 硬科技, 嵌入式", key="matcher_model", height=80)

    top_n = st.slider("最多展示几家机构", 3, 20, 10, key="matcher_top_n")

    if st.button("🔍 开始匹配", type="primary", key="btn_run_matcher"):
        industry_tags = [t.strip() for t in industry_tags_raw.replace("，", ",").split(",") if t.strip()]
        model_tags = [t.strip() for t in model_tags_raw.replace("，", ",").split(",") if t.strip()]

        if not company_name:
            st.warning("请填写公司名称。")
            return

        snapshot = CompanySnapshot(
            company_name=company_name,
            industry_tags=industry_tags,
            stage=stage,
            revenue_rmb_wan=int(revenue),
            model_tags=model_tags,
        )

        with st.spinner("正在检索机构画像数据…"):
            results = match_institutions(snapshot, workspace_root=workspace, top_n=top_n)

        if not results:
            st.info("暂无匹配数据。请先完成至少一次机构访谈以积累画像（系统会自动从历史 analytics 文件中提取）。")
            return

        st.success(f"找到 {len(results)} 家潜在匹配机构")
        for i, r in enumerate(results, 1):
            with st.expander(f"**{i}. {r.institution_name}** — {r.score}分", expanded=(i <= 3)):
                col_s, col_d = st.columns([1, 3])
                with col_s:
                    st.metric("匹配分", f"{r.score}/100")
                    if r.stage_match:
                        st.success("✅ 阶段吻合")
                    st.caption(f"访谈记录：{r.session_count} 次")
                with col_d:
                    st.write(f"**匹配理由**：{r.match_reason}")
                    if r.matched_keywords:
                        st.write(f"**命中关键词**：{', '.join(r.matched_keywords[:8])}")


# ── FOS Sprint 6：融资 Pipeline CRM 页面 ─────────────────────────────────────
def _render_pipeline_crm_page(company_id: str, workspace: str) -> None:
    """FOS Sprint 6 — 融资过程 CRM 页面。"""
    try:
        from pipeline_tracker import (
            PipelineStore, PipelineRecord, PipelineStatus,
            get_default_store, format_pipeline_overview,
        )
    except ImportError:
        st.error("pipeline_tracker 模块未加载，请确认 src/pipeline_tracker.py 存在。")
        return

    store = get_default_store(workspace)

    st.header("📋 融资 Pipeline")
    st.caption("追踪每家机构的接触进度，管理完整融资漏斗。")

    # 筛选器
    filter_company = st.text_input("按项目名称筛选（留空显示全部）", value=company_id or "", key="pipeline_filter_company")
    records = store.list_records(company_id=filter_company.strip() or None)

    # 统计看板
    if records:
        summary = store.get_summary(company_id=filter_company.strip() or None)
        active_statuses = [PipelineStatus.INITIAL_CONTACT, PipelineStatus.NDA_SIGNED,
                           PipelineStatus.MATERIALS_SENT, PipelineStatus.DD_IN_PROGRESS,
                           PipelineStatus.INTERVIEW_STAGE, PipelineStatus.TS_NEGOTIATION]
        cols = st.columns(len(active_statuses))
        for col, status in zip(cols, active_statuses):
            col.metric(status.value, summary.get(status, 0))

        won = summary.get(PipelineStatus.CLOSED_WON, 0)
        lost = summary.get(PipelineStatus.CLOSED_LOST, 0)
        if won or lost:
            st.caption(f"🏆 关单成功：{won}  ❌ 放弃：{lost}")

    st.divider()

    # 新建记录
    with st.expander("➕ 新建 Pipeline 记录", expanded=False):
        c1, c2, c3 = st.columns(3)
        new_inst_name = c1.text_input("机构名称", key="new_pipe_inst")
        new_inst_id = c2.text_input("机构ID（英文）", key="new_pipe_inst_id")
        new_company = c3.text_input("项目名称", value=company_id or "", key="new_pipe_company")
        new_status = st.selectbox("初始状态", [s.value for s in PipelineStatus], key="new_pipe_status")
        new_note = st.text_input("初始备注（可选）", key="new_pipe_note")
        if st.button("💾 创建记录", key="btn_create_pipeline"):
            if not new_inst_name or not new_company:
                st.warning("请填写机构名称和项目名称。")
            else:
                import time as _time
                rid = f"{new_inst_id or new_inst_name}_{new_company}_{int(_time.time())}"
                rec = PipelineRecord(
                    record_id=rid,
                    institution_id=new_inst_id or new_inst_name,
                    institution_name=new_inst_name,
                    company_id=new_company,
                    company_name=new_company,
                    status=PipelineStatus(new_status),
                )
                if new_note:
                    rec.add_event(new_note, "创建记录")
                store.save(rec)
                st.toast(f"✅ 已创建：{new_inst_name} × {new_company}", icon="✅")
                st.rerun()

    # 记录列表
    if not records:
        st.info("暂无 Pipeline 记录。点击上方「新建」开始追踪。")
        return

    status_emoji = {
        PipelineStatus.INITIAL_CONTACT: "📞", PipelineStatus.NDA_SIGNED: "📝",
        PipelineStatus.MATERIALS_SENT: "📦", PipelineStatus.DD_IN_PROGRESS: "🔍",
        PipelineStatus.INTERVIEW_STAGE: "🎙️", PipelineStatus.TS_NEGOTIATION: "💼",
        PipelineStatus.CLOSED_WON: "🏆", PipelineStatus.CLOSED_LOST: "❌",
    }

    for rec in records:
        emoji = status_emoji.get(rec.status, "•")
        last_date = rec.timeline[-1].date if rec.timeline else "—"
        with st.expander(
            f"{emoji} **{rec.institution_name}** × {rec.company_name}  |  {rec.status.value}  |  {last_date}",
            expanded=False
        ):
            c_l, c_r = st.columns([2, 1])
            with c_l:
                # 状态更新
                new_s = st.selectbox(
                    "更新状态",
                    [s.value for s in PipelineStatus],
                    index=[s.value for s in PipelineStatus].index(rec.status.value),
                    key=f"pipe_status_{rec.record_id}",
                )
                note_input = st.text_input("备注（更新状态时写入时间线）", key=f"pipe_note_{rec.record_id}")
                if st.button("💾 保存", key=f"pipe_save_{rec.record_id}"):
                    new_status_enum = PipelineStatus(new_s)
                    if new_status_enum != rec.status:
                        rec.update_status(new_status_enum, note=note_input)
                    elif note_input:
                        rec.add_event(note_input)
                    store.save(rec)
                    st.toast("✅ 已保存", icon="✅")
                    st.rerun()

                next_act = st.text_input("下一步行动", value=rec.next_action, key=f"pipe_next_{rec.record_id}")
                if st.button("📌 更新下一步", key=f"pipe_next_save_{rec.record_id}"):
                    rec.next_action = next_act
                    store.save(rec)
                    st.rerun()

            with c_r:
                st.caption("📅 时间线")
                for entry in reversed(rec.timeline[-8:]):
                    st.caption(f"`{entry.date}` {entry.action} — {entry.note}")

                if rec.linked_interviews:
                    st.caption("🎙️ 关联访谈")
                    for iv in rec.linked_interviews:
                        st.caption(f"  • {iv}")


# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    st.set_page_config(
        page_title="AI 路演与访谈复盘系统",
        page_icon="🚀",
        layout="wide",
    )
    setup_file_logging()
    logging.getLogger("ai_pitch_coach").debug("Streamlit main() rerun")

    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "v86_dashboard_mode" not in st.session_state:
        st.session_state["v86_dashboard_mode"] = False

    # V8.4 域字典初始化
    if "current_company_cache" not in st.session_state:
        st.session_state["current_company_cache"] = {}
    if "_active_company_id" not in st.session_state:
        st.session_state["_active_company_id"] = None

    with st.sidebar:
        # ── V8.4 公司档案选择器 ─────────────────────────────────────────────────
        st.markdown("### 🏢 项目档案")

        companies = cp.list_companies()
        # 以 display_name 去重：同名公司只保留第一条，避免重复条目堆积
        _seen_names: set[str] = set()
        unique_companies = []
        for _c in companies:
            if _c.display_name not in _seen_names:
                _seen_names.add(_c.display_name)
                unique_companies.append(_c)
        companies = unique_companies

        company_options: dict[str, str] = {c.company_id: c.display_name for c in companies}
        company_options["__new__"] = "➕ 新建项目"

        # 默认选中上次使用的公司；若无记录则选第一个已有公司（而非"新建"）
        _all_opts = list(company_options.keys())
        _saved_id = st.session_state.get("_active_company_id")
        if _saved_id and _saved_id in _all_opts:
            _default_idx = _all_opts.index(_saved_id)
        elif len(companies) > 0:
            _default_idx = 0  # 第一个已有项目
        else:
            _default_idx = 0  # 只有"新建"

        selected_company_id = st.selectbox(
            "选择项目",
            options=_all_opts,
            format_func=lambda k: company_options[k],
            index=_default_idx,
            key="company_selector",
            label_visibility="collapsed",
        )

        # 公司切换：整体清空域字典（铁律三：严禁遍历删除 UI-bound key）
        if st.session_state["_active_company_id"] != selected_company_id:
            st.session_state["current_company_cache"] = {}
            st.session_state["_active_company_id"] = selected_company_id

        # 新建项目
        if selected_company_id == "__new__":
            with st.expander("📝 填写项目信息", expanded=True):
                st.caption(
                    "💡 **建议用项目简称**（如「泽天智航」），同项目下不同子公司/被访人"
                    "只需切换「被访谈人」字段，无需分别建档。"
                )
                new_display = st.text_input("项目名称（简称）", key="new_co_display")
                new_bg = st.text_area("项目背景（可选）", key="new_co_bg", height=100)
                if st.button("💾 创建并选中", key="btn_create_co"):
                    if new_display.strip():
                        import re as _re
                        # 同名复用：避免重复创建相同项目
                        _existing = next(
                            (c for c in cp.list_companies() if c.display_name == new_display.strip()),
                            None,
                        )
                        if _existing:
                            st.session_state["_active_company_id"] = _existing.company_id
                            st.info(f"「{new_display.strip()}」已存在，已自动切换到该项目。")
                            st.rerun()
                        else:
                            import time as _time
                            new_id = _re.sub(r"[^\w]", "_", new_display.strip()) + f"_{int(_time.time())}"
                            cp.save_company(CompanyProfile(
                                company_id=new_id,
                                display_name=new_display.strip(),
                                background=new_bg.strip(),
                            ))
                            st.session_state["_active_company_id"] = new_id
                            st.rerun()
                    else:
                        st.warning("请输入项目名称")
            current_company_bg = ""
            current_sniper_json = "[]"
        else:
            # 从域字典缓存加载公司档案（避免重复磁盘 IO）
            cache = st.session_state["current_company_cache"]
            if "company_profile" not in cache:
                cache["company_profile"] = cp.load_company(selected_company_id)
            profile = cache.get("company_profile")

            with st.expander("📋 公司背景", expanded=False):
                if profile:
                    edited_bg = st.text_area(
                        "背景内容",
                        value=profile.background,
                        height=120,
                        key=f"bg_editor_{selected_company_id}",
                        label_visibility="collapsed",
                    )
                    if st.button("💾 保存背景", key=f"btn_save_bg_{selected_company_id}"):
                        cp.save_company(profile.model_copy(update={"background": edited_bg}))
                        cache["company_profile"] = cp.load_company(selected_company_id)
                        st.success("已保存")
                else:
                    st.caption("档案不存在或已损坏")

            current_company_bg = profile.background if profile else ""
            # 获取当前 session 的狙击清单 JSON（用于 logical_conflict 检测）
            # 取第一个 batch 的狙击清单（如有），供冲突检测使用
            current_sniper_json = st.session_state.get("batch_sniper_editor_0") or "[]"
            if not isinstance(current_sniper_json, str):
                import json as _json
                try:
                    current_sniper_json = _json.dumps(
                        current_sniper_json.to_dict(orient="records") if hasattr(current_sniper_json, "to_dict") else [],
                        ensure_ascii=False
                    )
                except Exception:
                    current_sniper_json = "[]"

            # logical_conflict 警告
            if current_company_bg:
                conflicts = detect_logical_conflict(current_company_bg, current_sniper_json)
                if conflicts:
                    with st.expander("⚠️ 背景与狙击目标潜在冲突", expanded=False):
                        for w in conflicts:
                            st.warning(w)

        st.divider()
        if st.button("📊 高管数字记忆库", key="btn_v86_open_dash", use_container_width=True):
            st.session_state["v86_dashboard_mode"] = True
            st.rerun()
        if st.button("🎯 投资人匹配", key="btn_investor_matcher", use_container_width=True):
            st.session_state["fos_page"] = "investor_matcher"
            st.rerun()
        if st.button("📋 融资Pipeline", key="btn_pipeline_crm", use_container_width=True):
            st.session_state["fos_page"] = "pipeline_crm"
            st.rerun()
        # ── 公司档案选择器结束 ────────────────────────────────────────────────────

        latest_draft_sid = _v7_latest_draft_session_id()
        if latest_draft_sid and not st.session_state.get("v3_review_stems"):
            st.info("检测到本地有未完成的审查草稿，可从下方恢复。")
            if st.button("恢复上次未完成的审查草稿", key="v7_restore_draft_btn"):
                loaded = load_draft(latest_draft_sid)
                if loaded:
                    _v7_apply_draft_payload(loaded)
                    st.rerun()
                else:
                    st.error("草稿已损坏或不存在，无法恢复。")
        st.header("⚙️ 系统配置")
        workspace_default = str(get_writable_app_root())
        workspace = st.text_input(
            "数据归档根目录 (Workspace Root)",
            value=workspace_default,
            help="可填企业共享盘路径；留空则使用当前项目根目录。",
        ).strip()

        if "dash_key_field" not in st.session_state:
            st.session_state.dash_key_field = os.getenv("DASHSCOPE_API_KEY") or ""
        if "deep_key_field" not in st.session_state:
            st.session_state.deep_key_field = os.getenv("DEEPSEEK_API_KEY") or ""

        with st.expander("🔑 首次使用请配置 API 密钥", expanded=True):
            st.text_input(
                "阿里云 API Key (用于转写)",
                type="password",
                key="dash_key_field",
                help="写入环境变量 DASHSCOPE_API_KEY（百炼 DashScope，录音转写等）。",
            )
            st.text_input(
                "DeepSeek API Key (用于毒舌分析)",
                type="password",
                key="deep_key_field",
                help="写入环境变量 DEEPSEEK_API_KEY（默认打分分析）。",
            )
            if st.button("💾 保存并测试连接", key="save_test_api_keys"):
                ds = (st.session_state.dash_key_field or "").strip()
                dk = (st.session_state.deep_key_field or "").strip()
                if not ds or not dk:
                    st.session_state.env_all_ok = False
                    st.error("❌ 请先完整填写两个 API Key。")
                else:
                    try:
                        set_key(str(_ENV_PATH), "DASHSCOPE_API_KEY", ds)
                        set_key(str(_ENV_PATH), "DEEPSEEK_API_KEY", dk)
                        load_dotenv(_ENV_PATH, override=True)
                        os.environ["DASHSCOPE_API_KEY"] = ds
                        os.environ["DEEPSEEK_API_KEY"] = dk
                        with st.status("🔌 正在全量环境自检...", expanded=True) as status:
                            status.update(
                                label="正在测试阿里云 DashScope（大模型兼容接口）...",
                                state="running",
                            )
                            ok_a, err_a = _ping_dashscope_compatible(ds)
                            if ok_a:
                                status.write("✅ 阿里云 DashScope：绿灯")
                            else:
                                status.write(f"❌ 阿里云 DashScope：{err_a}")
                            status.update(
                                label="正在测试 DeepSeek...",
                                state="running",
                            )
                            ok_b, err_b = _ping_deepseek(dk)
                            if ok_b:
                                status.write("✅ DeepSeek：绿灯")
                            else:
                                status.write(f"❌ DeepSeek：{err_b}")
                            status.update(
                                label="正在检测本地视听引擎 (FFmpeg)...",
                                state="running",
                            )
                            ok_ff, err_ff = _probe_ffmpeg_for_ui()
                            if ok_ff:
                                status.write("✅ 视听引擎 (FFmpeg)：已就绪")
                            else:
                                status.markdown(
                                    ":red[❌ 视听引擎 (FFmpeg)：未找到或被拦截]"
                                )
                            status.update(label="自检完成", state="complete")
                        if ok_a and ok_b and ok_ff:
                            st.session_state.env_all_ok = True
                            st.success(
                                "✅ 环境全绿！API与本地视听引擎均已完美就绪。"
                            )
                        else:
                            st.session_state.env_all_ok = False
                            parts: list[str] = []
                            if not ok_a:
                                parts.append(f"阿里云：{err_a}")
                            if not ok_b:
                                parts.append(f"DeepSeek：{err_b}")
                            if not ok_ff:
                                parts.append(f"FFmpeg：{err_ff}")
                            st.error("❌ 环境自检未通过：" + "；".join(parts))
                    except Exception as e:
                        st.session_state.env_all_ok = False
                        st.error(f"❌ 保存或测试失败：{e!s}")

        if st.session_state.get("env_all_ok"):
            st.success("✅ 环境：API + FFmpeg 已验证")
        else:
            st.caption(
                "⚠️ 须点击「保存并测试连接」直至阿里云、DeepSeek、FFmpeg 全部通过后方可生成报告。"
            )

        if "sensitive_words_raw" not in st.session_state:
            st.session_state.sensitive_words_raw = "福创投, 迪策, 净利润"
        st.text_area(
            "🔒 保密词汇黑名单（支持换行、空格、中英文逗号/分号混用）",
            key="sensitive_words_raw",
            height=88,
            help="在调用大模型前，对转写词文本替换为 ***。可点击「识别保密词汇」预览系统解析结果。",
        )
        if st.button("🔍 识别保密词汇", key="btn_parse_sensitive_words"):
            parsed = parse_sensitive_words(
                str(st.session_state.get("sensitive_words_raw") or "")
            )
            st.session_state.sensitive_words_last_parsed = parsed
        last_p = st.session_state.get("sensitive_words_last_parsed")
        if last_p is not None:
            if last_p:
                show_n = 24
                if len(last_p) <= show_n:
                    joined = "，".join(last_p)
                    st.caption(
                        f"已成功识别并提取 {len(last_p)} 个保密词汇：{joined}"
                    )
                else:
                    joined = "，".join(last_p[:show_n])
                    st.caption(
                        f"已成功识别并提取 {len(last_p)} 个保密词汇：{joined}…"
                        f"（此处仅展示前 {show_n} 个）"
                    )
            else:
                st.caption("已成功识别：当前列表为空（未提取到任何非空词汇）。")

        filename_mask_input = st.text_area(
            "📤 外发 HTML 文件名脱敏（每行：原名⇒代号）",
            value="",
            height=100,
            help=(
                "仅改变「复盘报告.html」的文件名，便于外发；内置已含 迪策资本⇒DC资本、邓勇⇒DY。"
                "可追加一行一条，支持 ⇒、=>、= 分隔。"
            ),
        )
        mask_html_body = st.checkbox(
            "HTML 正文同步脱敏（场景与翻车卡片文案按上表替换）",
            value=False,
            help=(
                "开启后，仅影响生成的 .html 展示；同目录 *_analysis_report.json 仍为完整原文，便于内部分析。"
                "外发 HTML 时建议勾选。"
            ),
        )
        html_watermark = st.text_input(
            "HTML 页脚水印（可选）",
            value="",
            placeholder="例如：仅供内部评审，禁止外传",
            help="显示在报告页脚醒目位置，便于外发合规提示。",
        )

        st.subheader("密钥环境变量")
        st.caption(
            f"{'✅' if _env_configured('DASHSCOPE_API_KEY') else '❌'} DASHSCOPE_API_KEY（阿里云）"
        )
        st.caption(
            f"{'✅' if _env_configured('DEEPSEEK_API_KEY') else '❌'} DEEPSEEK_API_KEY（DeepSeek）"
        )

    if not st.session_state.get("v4_startup_gc_started"):
        st.session_state["v4_startup_gc_started"] = True
        ws_gc = (workspace or "").strip() or str(get_writable_app_root())

        def _startup_gc() -> None:
            try:
                n = sweep_stale_intermediate_json(ws_gc)
                if n:
                    logging.getLogger("garbage_collector").info(
                        "启动静默 GC：已删除 %d 个过期中间 JSON（根：%s）",
                        n,
                        ws_gc,
                    )
            except Exception:
                logging.getLogger("garbage_collector").exception("启动 GC 失败")

        threading.Thread(target=_startup_gc, daemon=True).start()

    # 启动时自动检测：若 .env 已有两个 Key，直接标记全绿，不需要用户再手动点「保存并测试」
    if "env_all_ok" not in st.session_state:
        if _env_configured("DASHSCOPE_API_KEY") and _env_configured("DEEPSEEK_API_KEY"):
            st.session_state["env_all_ok"] = True

    st.title("🚀 AI 路演与访谈复盘系统")

    if st.session_state.get("v86_dashboard_mode"):
        if st.button("⬅️ 返回主控制台", key="btn_v86_close_dash"):
            st.session_state["v86_dashboard_mode"] = False
            st.rerun()
        _v86_render_executive_dashboard(selected_company_id, workspace_root=workspace)
        st.stop()

    # ── FOS Sprint 6：新功能页面路由（不影响任何现有逻辑）────────────────────
    _fos_page = st.session_state.get("fos_page", "")
    if _fos_page == "investor_matcher":
        if st.button("⬅️ 返回主控制台", key="btn_matcher_back"):
            st.session_state["fos_page"] = ""
            st.rerun()
        _render_investor_matcher_page(selected_company_id, workspace)
        st.stop()

    if _fos_page == "pipeline_crm":
        if st.button("⬅️ 返回主控制台", key="btn_pipeline_back"):
            st.session_state["fos_page"] = ""
            st.rerun()
        _render_pipeline_crm_page(selected_company_id, workspace)
        st.stop()
    # ─────────────────────────────────────────────────────────────────────────

    if not st.session_state.get("env_all_ok", False):
        st.warning(
            "⚠️ 请先在左侧侧边栏「🔑 首次使用请配置 API 密钥」中填写 Key，"
            "点击「💾 保存并测试连接」直至阿里云、DeepSeek 与 FFmpeg 全绿后，方可开始生成报告。"
        )

    scene_options = [_SCENE_SELECT_PLACEHOLDER] + list(SCENE_MAP.keys())
    col1, col2 = st.columns(2)
    with col1:
        category = st.selectbox(
            "业务大类（必选）",
            options=scene_options,
            index=0,
            help="请先明确业务场景后再生成，避免 AI 用错复盘视角。",
        )
    with col2:
        institution_name_input = st.text_input(
            "投资机构名称（必填）",
            placeholder="例如：迪策资本、高瓴资本、红杉中国",
            help="输入后系统自动识别历史记录中的同名机构，积累画像。",
            key="v102_institution_name",
        )
        # 模糊匹配提示
        _inst_raw = (institution_name_input or "").strip()
        if _inst_raw and len(_inst_raw) >= 2:
            _inst_match = institution_fuzzy_match(_inst_raw)
            if _inst_match and _inst_match["canonical_name"].lower() != _inst_raw.lower():
                st.caption(
                    f"💡 发现历史记录：**{_inst_match['canonical_name']}**"
                    f"（{_inst_match['session_count']} 场）"
                    "，若是同一机构请统一用上面名称，或直接继续将自动合并。"
                )

        investor_name_input = st.text_input(
            "接待投资人姓名（选填）",
            placeholder="例如：李合伙人、王总监",
            help="记录本次参会的投资人姓名，用于 Partner 级别画像分析。",
            key="v103_investor_name",
        )

        batch_label = st.text_input(
            "项目批次备注（选填）",
            placeholder="例如：尽调第2轮、2026Q1",
            help="区分同一机构多次访谈批次，不影响机构画像归档。",
            key="v102_batch_label",
        )
        # 兼容旧逻辑：batch_name = 机构名 + 批次
        batch_name = _inst_raw or (batch_label or "").strip() or "未命名批次"

    st.caption(
        "上传音频（可 1 条或多条）后，在下方按 **每一条录音** 填写被访谈人、备注，并可选上传该段对应的参考 QA。"
    )

    if category == OTHER_SCENE_KEY:
        st.text_input(
            "请填写具体双方身份（必填）",
            placeholder="例如：供应商质量负责人 vs 买方投资机构",
            key="custom_roles_other",
        )

    tab_qa_file, tab_qa_dir = st.tabs(["参考 QA 说明", "选择参考文件夹"])
    with tab_qa_file:
        st.caption(
            "参考 QA 在下方 **「逐录音填写」** 中按文件上传；仅作用于对应那一条录音。"
            "可多选多个 QA 文件，会先合并再截断前 30000 字。支持 txt、md、pdf、docx、xlsx（PPT 请先另存为 PDF）。"
        )
    with tab_qa_dir:
        st.info("预留：后续支持选择本地参考文件夹批量导入。")
        st.text_input(
            "参考文件夹路径（预留，暂未启用）",
            disabled=True,
            key="qa_folder_placeholder",
        )

    uploaded = st.file_uploader(
        "上传音频（可多选）",
        type=["m4a", "mp3", "wav", "mp4", "mpeg", "mpga", "webm"],
        accept_multiple_files=True,
    )

    uploaded_list: list = []
    if uploaded is not None:
        uploaded_list = list(uploaded) if isinstance(uploaded, (list, tuple)) else [uploaded]

    batch_qa_files_per_index: list[list] = []
    if uploaded_list:
        # ── 转写质量设置（全局，作用于所有录音）──
        with st.expander("🔥 转写质量设置（专有名词热词）", expanded=False):
            if "v80_hot_words_raw" not in st.session_state:
                st.session_state["v80_hot_words_raw"] = ""
            st.text_area(
                "项目专属专有名词（用逗号隔开，注入 ASR 提示词）",
                key="v80_hot_words_raw",
                height=72,
                placeholder="例如：净利润、迪策资本、EBITDA、核心增长率、信号处理链路",
                help=(
                    "输入本项目的专业术语、机构名称、人名等，系统将在转写时注入 ASR 引擎作为提示词，"
                    "从源头提升财务、业务专有名词的识别准确率。用逗号（中英文均可）分隔多个词。"
                ),
            )
            n_words = len([
                w for w in (st.session_state.get("v80_hot_words_raw") or "")
                .replace("，", ",").replace("；", ",").split(",") if w.strip()
            ])
            if n_words:
                st.caption(f"已录入 {n_words} 个热词，转写时将作为 ASR 提示词注入。")
            else:
                st.caption("未录入热词。对于含大量专业术语的录音，建议填写后再转写。")

        st.subheader("逐录音填写（进入 AI 上下文）")
        st.checkbox(
            "根据录音文件名自动填写被访谈人与狙击表首行疑点",
            value=True,
            key="batch_autofill_filename",
            help=(
                "按常见命名「机构-姓名」与可选末尾 8 位日期解析；更换本行对应录音文件名后会按新文件名重新覆盖被访谈人，"
                "并将解析到的备注写入该条「找茬疑点」列首行。关闭后仅手动填写。"
            ),
        )
        st.caption(
            "调整音频顺序或增删文件后，请逐条核对被访谈人、狙击清单与 QA 是否与录音一致。"
        )
        for idx, uf in enumerate(uploaded_list):
            stem = stem_from_audio_filename(uf.name)
            track_key = f"_batch_audio_stem_{idx}"
            autofill_store_key = f"_batch_iv_autofilled_{idx}"  # BUG-C：记录上次自动填充值
            init_key = f"batch_sniper_init_{idx}"   # 初始数据专用，写操作唯一入口
            ed_key = f"batch_sniper_editor_{idx}"   # 仅绑定 data_editor widget，严禁写入
            if init_key not in st.session_state:
                st.session_state[init_key] = pd.DataFrame(
                    [{"原文引用": "", "找茬疑点": ""}]
                )
            st.session_state[init_key] = _normalize_sniper_editor_df(
                st.session_state[init_key]
            )
            if st.session_state.get("batch_autofill_filename", True):
                if st.session_state.get(track_key) != stem:
                    st.session_state[track_key] = stem
                    iv_guess, note_guess = guess_batch_fields_from_stem(stem)
                    # BUG-C 修复：只有用户未手动改过才覆盖（保护手动输入）
                    current_iv = st.session_state.get(f"batch_iv_{idx}", "")
                    last_autofilled = st.session_state.get(autofill_store_key)
                    if should_autofill_iv(current_iv, last_autofilled):
                        st.session_state[f"batch_iv_{idx}"] = iv_guess
                    st.session_state[autofill_store_key] = iv_guess  # 总是记录本次猜测
                    st.session_state[init_key] = pd.DataFrame(
                        [{"原文引用": "", "找茬疑点": note_guess}]
                    )
                    # 新文件检测到：清除 widget 托管状态，强制下次渲染以 init_key 重新初始化
                    if ed_key in st.session_state:
                        del st.session_state[ed_key]

            st.markdown(f"**文件 {idx + 1}：** `{uf.name}`")
            st.text_input(
                "被访谈人（必填）",
                key=f"batch_iv_{idx}",
                placeholder="本段录音对应的对象",
                help="仅作用于当前这一条录音的打分与复盘。",
            )
            st.caption(
                "🎯 **结构化狙击清单**（`key` 绑定会话，勿把返回值写回 state，避免循环刷新丢数）："
                "「原文引用」贴原话，「找茬疑点」写找茬方向；可多行。"
            )
            # 安全红线（铁律三）：init_key 提供初始数据，ed_key 绑定 widget，严禁反向赋值。
            # result_key 存 data_editor 返回的完整 DataFrame（非 widget key，可安全写入）。
            result_key = f"batch_sniper_result_{idx}"
            _sniper_edited_df = st.data_editor(
                st.session_state[init_key],
                column_config={
                    "原文引用": st.column_config.TextColumn("原文引用", width="large"),
                    "找茬疑点": st.column_config.TextColumn("找茬疑点", width="large"),
                },
                num_rows="dynamic",
                key=ed_key,
                hide_index=True,
            )
            st.session_state[result_key] = _sniper_edited_df
            suf = _qa_uploader_key_suffix(uf.name)
            qf = st.file_uploader(
                "本段参考 QA（可选，可多选）",
                type=["txt", "md", "pdf", "docx", "xlsx"],
                accept_multiple_files=True,
                key=f"batch_qa_{idx}_{suf}",
                help=(
                    f"仅用于本条录音「{uf.name}」。不上传仍会生成报告，但对齐深度可能下降。"
                    "多文件会先合并再截断前 30000 字。"
                ),
            )
            batch_qa_files_per_index.append(_as_upload_list(qf))

    st.info(
        "💡 **建议**：尽量为每条录音上传 **对应该方向的 QA** 或口径材料；未上传时仍会生成报告。"
    )

    if "v71_plain_body" not in st.session_state:
        st.session_state["v71_plain_body"] = ""

    if st.button(
        "📄 仅提取文字稿 (提取后可复制原话进行精准核实)",
        key="v71_btn_extract_transcript",
    ):
        if not st.session_state.get("env_all_ok"):
            st.error("请先在左侧侧栏完成 API 与 FFmpeg 全绿自检后再提取文字稿。")
        elif not uploaded_list:
            st.error("请先上传至少一个音频文件。")
        else:
            uf0 = uploaded_list[0]
            if len(uploaded_list) > 1:
                st.caption(
                    f"当前对上传列表中的 **第 1 条** 执行转写：`{uf0.name}`（共 {len(uploaded_list)} 个文件）。"
                )
            try:
                with st.status("📄 正在提取文字稿…", expanded=True) as v71_status:

                    def _v71_line(msg: str) -> None:
                        v71_status.write(msg)

                    plain = _v71_transcribe_upload_to_plain(uf0, on_line=_v71_line)
                st.session_state["v71_plain_body"] = plain
                st.success(
                    f"已提取约 **{len(plain)}** 字，可复制到上方对应录音的「原文引用」列。"
                )
            except Exception as ex:
                logging.getLogger("ai_pitch_coach.ui").exception("仅提取文字稿失败")
                st.error(f"转写失败：{ex!s}")

    st.text_area(
        "提取的文字稿（可复制到上方逐录音「原文引用」列）",
        key="v71_plain_body",
        height=280,
        help="先点击上方按钮；按说话人分段的文字可粘贴到狙击清单「原文引用」列以降低切片错位。",
    )

    run = st.button(
        "开始生成复盘报告",
        type="primary",
        disabled=not st.session_state.get("env_all_ok", False),
    )

    if not run:
        st.info("配置侧边栏与业务场景，上传音频后点击按钮开始。")
        if st.session_state.get("v3_review_stems"):
            st.divider()
            _v3_render_review_workbench()
        return

    if not uploaded_list:
        st.warning("请先上传至少一个音频文件。")
        return

    if category == _SCENE_SELECT_PLACEHOLDER:
        st.error("请先在下拉框中选择真实的「业务大类」，不能保留「请先选择业务场景」。")
        return

    if not (institution_name_input or "").strip():
        st.error("请填写「投资机构名称」（必填）。")
        return

    for idx in range(len(uploaded_list)):
        iv = (st.session_state.get(f"batch_iv_{idx}") or "").strip()
        if not iv:
            st.error(f"请为录音「{uploaded_list[idx].name}」填写被访谈人（必填）。")
            return

    if category == OTHER_SCENE_KEY:
        cr = (st.session_state.get("custom_roles_other") or "").strip()
        if not cr:
            st.error('业务大类为「05_其他」时，必须填写「具体双方身份」。')
            return

    try:
        sensitive_words = parse_sensitive_words(
            str(st.session_state.get("sensitive_words_raw") or "")
        )
        # V8.0：解析项目专属热词库
        hot_words_raw = str(st.session_state.get("v80_hot_words_raw") or "")
        hot_words: list[str] = [
            w.strip() for w in hot_words_raw.replace("，", ",").replace("；", ",").split(",")
            if w.strip()
        ] or None

        html_mask_map = _merge_html_filename_masks(filename_mask_input)
        project_name = (batch_name or "").strip()

        batch_qa_texts: list[str] = []
        for idx, uf in enumerate(uploaded_list):
            if idx >= len(batch_qa_files_per_index):
                st.error("内部状态异常：请刷新页面后重新上传音频。")
                return
            per_files = batch_qa_files_per_index[idx]
            batch_qa_texts.append(
                extract_text_from_files(per_files, max_chars=30000)
            )

        root_path = Path(workspace).expanduser() if workspace else get_writable_app_root()
        try:
            root_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            st.error(f"无法创建或使用归档根目录：{e}")
            return

        target_dir = root_path / safe_fs_segment(category) / safe_fs_segment(batch_name)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            st.error(f"无法创建目标目录：{target_dir}\n{e}")
            return

        _v3_clear_review_session_state()

        errors: list[str] = []
        n = len(uploaded_list)
        v3_review_stems: list[str] = []
        # 先固化展示名（与 session_state 下标对齐），再极速落盘，避免长耗时后 UploadedFile 缓存失效导致 WinError 2
        recording_labels: list[str] = [
            getattr(uf, "name", "") or f"recording_{i}" for i, uf in enumerate(uploaded_list)
        ]
        saved_audio_paths: list[Path | None] = [None] * n
        for i, uf in enumerate(uploaded_list):
            fname = recording_labels[i]
            audio_path = target_dir / fname
            try:
                audio_path.write_bytes(uf.getvalue())
                saved_audio_paths[i] = audio_path.resolve()
            except OSError as e:
                errors.append(f"{fname}: 抢救落盘失败（无法写入归档目录）{e}")
            except Exception as e:
                errors.append(f"{fname}: 抢救落盘失败 {e!s}")

        progress_bar = st.progress(0)

        custom_roles = (
            (st.session_state.get("custom_roles_other") or "").strip()
            if category == OTHER_SCENE_KEY
            else ""
        )

        html_opts = HtmlExportOptions(
            footer_watermark=(html_watermark or "").strip(),
            content_replace_map=html_mask_map if mask_html_body else None,
            show_generated_timestamp=True,
        )

        st.session_state.pop("v7_qa_truncation_warn", None)

        mem_cid = (
            ""
            if selected_company_id == "__new__"
            else (selected_company_id or "").strip()
        )

        # V10.2 机构注册：resolve 确保 institution_id 稳定，自动合并别名
        _inst_input = (st.session_state.get("v102_institution_name") or "").strip()
        _institution_id, _institution_canonical = institution_resolve(_inst_input) if _inst_input else ("", "")

        for i in range(n):
            fname = recording_labels[i]
            stem = Path(fname).stem
            audio_path = saved_audio_paths[i]

            if audio_path is None:
                progress_bar.progress((i + 1) / n)
                continue

            try:
                with st.status(
                    "🚀 正在执行 AI 深度复盘...",
                    expanded=True,
                ) as status:
                    raw_bytes = audio_path.read_bytes()
                    orig_len = len(raw_bytes)
                    orig_size_mb = orig_len / (1024 * 1024)
                    status.write(
                        f"📥 接收原始文件：大小 {orig_size_mb:.2f} MB"
                    )

                    # ── P1 修复：先算 hash，先查缓存，缓存命中完全跳过 FFmpeg ──
                    # 旧逻辑：FFmpeg 压缩 → 计算 hash → 查缓存（缓存命中也白跑 FFmpeg）
                    # 新逻辑：计算 hash → 查缓存 → 未命中才启动 FFmpeg（节省 CPU 数十秒）
                    file_hash = _file_md5(raw_bytes)
                    asr_cache: dict = st.session_state.setdefault("asr_cache", {})
                    cached_entry = asr_cache.get(file_hash)
                    disk_entry = None  # 显式初始化，避免 else 块外未定义
                    cached_words_models = None
                    gw_compressed: Path | None = None  # 阅后即焚：跟踪待清理的临时压缩文件

                    if cached_entry:
                        # Level 1：内存缓存命中 → 完全跳过 FFmpeg
                        cached_words_models = [
                            TranscriptionWord.model_validate(w)
                            for w in cached_entry["words"]
                        ]
                        status.write("✅ 命中内存缓存（本次 session 已转写），跳过压缩与云端 ASR，节省资源。")
                        work_audio = audio_path
                    else:
                        disk_entry = load_asr_cache(file_hash)
                        if disk_entry:
                            # Level 2：磁盘缓存命中 → 完全跳过 FFmpeg
                            cached_words_models = [
                                TranscriptionWord.model_validate(w)
                                for w in disk_entry["words"]
                            ]
                            # 同步写入内存缓存，供后续同 session 操作复用
                            asr_cache[file_hash] = disk_entry
                            status.write("✅ 命中磁盘缓存（历史已转写文件），完全跳过压缩与云端 ASR，节省资源。")
                            work_audio = audio_path
                        else:
                            # Level 3：两级缓存均未命中，才执行 FFmpeg + 云端 ASR
                            if orig_len < 10 * 1024 * 1024:
                                status.write("✅ 文件极轻量，免压缩直通 ASR。")
                                work_audio = audio_path
                            else:
                                status.write(
                                    "⚙️ 启动智能音频网关 (抽离视频轨 & 语音降采样)..."
                                )
                                cres = smart_compress_media(
                                    raw_bytes, filename_hint=fname
                                )
                                if cres.did_compress:
                                    new_size_mb = len(cres.data) / (1024 * 1024)
                                    ratio = (1.0 - len(cres.data) / max(1, orig_len)) * 100.0
                                    st.success(
                                        f"🚀 极致压缩完成！新大小：{new_size_mb:.2f} MB "
                                        f"(体积缩减 {ratio:.1f}%)"
                                    )
                                    gw = target_dir / f"{stem}_v62_asr_gateway.mp3"
                                    gw.write_bytes(cres.data)
                                    work_audio = gw.resolve()
                                    gw_compressed = gw  # 阅后即焚：记录待清理路径
                                else:
                                    st.warning(
                                        "⚠️ 压缩遇到特殊格式，已安全回退至原文件处理。"
                                    )
                                    work_audio = audio_path
                            status.write(
                                "⏱️ 里程碑：云端转写 → 敏感词脱敏 → DeepSeek 多维度 QA 对齐（结构化 JSON）→ 初稿进入审查台。"
                            )

                    per_iv = (st.session_state.get(f"batch_iv_{i}") or "").strip()
                    sniper_json = _batch_sniper_targets_json(i)

                    explicit_context = build_explicit_context(
                        category,
                        project_name,
                        per_iv,
                        session_notes="",
                        sniper_targets_json=sniper_json,
                        recording_label=fname,
                        custom_roles_other=custom_roles,
                    )

                    trans_json = target_dir / f"{stem}_transcription.json"
                    analysis_json = target_dir / f"{stem}_analysis_report.json"
                    html_stem = apply_html_filename_masks(stem, html_mask_map)
                    html_name = f"{html_stem}_复盘报告.html"
                    html_path = target_dir / html_name

                    qa_text = batch_qa_texts[i]

                    params = PitchFileJobParams(
                        transcription_json_path=trans_json,
                        analysis_json_path=analysis_json,
                        html_output_path=html_path,
                        sensitive_words=sensitive_words,
                        explicit_context=explicit_context,
                        qa_text=qa_text,
                        model_choice="deepseek",
                        html_export_options=html_opts,
                        hot_words=hot_words,
                        company_background=current_company_bg,
                        memory_company_id=mem_cid,
                    )

                    def _pipe_status(m: str) -> None:
                        status.update(label=m, state="running")
                        if "QA 补充材料字数超载" in m:
                            st.session_state["v7_qa_truncation_warn"] = m

                    words, report = run_pitch_file_job(
                        work_audio,
                        params,
                        on_status=_pipe_status,
                        skip_html_export=True,
                        cached_words=cached_words_models,
                    )

                    # 首次转写后同时写入内存缓存 + 磁盘缓存（跨 session 持久化）
                    if not cached_entry and not disk_entry:
                        cache_payload = {
                            "words": [w.model_dump() for w in words],
                            "plain": format_transcript_plain_by_speaker(words),
                        }
                        asr_cache[file_hash] = cache_payload
                        try:
                            save_asr_cache(file_hash, cache_payload["words"], cache_payload["plain"])
                        except Exception:
                            logging.getLogger("ai_pitch_coach.ui").warning(
                                "磁盘 ASR 缓存写入失败（不影响主流程）", exc_info=True
                            )

                    # ── 阅后即焚：ASR 已入库，立即清理 FFmpeg 临时压缩音频 ──
                    # 防止每次批量处理堆积大量 _v62_asr_gateway.mp3，撑爆磁盘
                    if gw_compressed is not None:
                        try:
                            gw_compressed.unlink(missing_ok=True)
                        except OSError:
                            logging.getLogger("ai_pitch_coach.ui").warning(
                                "阅后即焚：无法删除临时压缩音频 %s（已跳过，不影响主流程）",
                                gw_compressed,
                            )
                        gw_compressed = None

                    draft = report.model_dump()
                    for _rp in draft.get("risk_points") or []:
                        _rp.setdefault("_rid", uuid.uuid4().hex[:16])

                    st.session_state[f"report_draft_{stem}"] = draft
                    st.session_state[f"v3_initial_report_{stem}"] = copy.deepcopy(draft)
                    st.session_state[f"words_{stem}"] = [w.model_dump() for w in words]
                    _draft_ctx = {
                        "audio_path": str(audio_path),
                        "analysis_json": str(analysis_json),
                        "html_path": str(html_path),
                        "project_name": project_name,
                        "interviewee": per_iv,
                        "watermark": (html_watermark or "").strip(),
                        "mask_html_body": bool(mask_html_body),
                        "html_mask_map": dict(html_mask_map),
                        "company_id": mem_cid,
                        "institution_id": _institution_id,
                        "institution_canonical": _institution_canonical,
                    }
                    st.session_state[f"v3_ctx_{stem}"] = _draft_ctx

                    # ── V10.1 凡运行必留痕：AI初稿就绪时立即写 draft analytics ──
                    export_analytics(report, _draft_ctx, status="draft")

                    v3_review_stems.append(stem)

                    status.update(
                        label=f"✅ {fname} AI 初稿已就绪，请在下方审查台锁定后导出 HTML",
                        state="complete",
                    )
            except Exception as e:
                errors.append(f"{fname}: {e!s}")

            progress_bar.progress((i + 1) / n)

        progress_bar.progress(1.0)

        try:
            n_gc = sweep_stale_intermediate_json(root_path)
            if n_gc:
                logging.getLogger("garbage_collector").info(
                    "批次结束后 GC：已删除 %d 个过期中间 JSON", n_gc
                )
        except Exception:
            logging.getLogger("garbage_collector").exception("批次结束后 GC 失败")

        st.session_state["v3_review_stems"] = v3_review_stems

        qa_trunc_warn = st.session_state.pop("v7_qa_truncation_warn", None)
        if qa_trunc_warn:
            st.warning(qa_trunc_warn)

        if errors:
            st.warning("部分文件处理失败：")
            for e in errors:
                st.error(e)

        if len(errors) < n:
            st.balloons()
            st.success(
                f"✅ AI 初稿与转写已归档至：**{target_dir}**（JSON 已写；"
                f"**最终 HTML 需在下方审查台锁定后生成**）"
            )
            _v3_render_review_workbench()
        else:
            st.error("全部任务失败，请检查上方错误与 API 配置。")

    except Exception as e:
        logging.getLogger("ai_pitch_coach.ui").exception("主流程未捕获异常")
        st.error(f"系统在处理过程中发生意外错误，已记录到诊断日志：{e!s}")
        st.caption("若需技术支持，请下载下方 `debug.log` 并附上复现步骤。")
        st.download_button(
            label="🆘 下载系统诊断日志",
            data=read_debug_log_bytes(),
            file_name="debug.log",
            mime="text/plain",
            key="v4_fatal_debug_log_download",
        )


if __name__ == "__main__":
    main()
