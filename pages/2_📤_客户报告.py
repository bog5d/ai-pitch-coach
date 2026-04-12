"""
客户进度报告生成器 — 独立 Streamlit 页面（V10.3 P2.3）

此页面可在多页模式下独立访问，无需通过主控台进入。
核心逻辑复用 client_dashboard.py。
"""
from __future__ import annotations

import sys
from pathlib import Path
import re

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

st.set_page_config(
    page_title="客户报告 — AI 路演教练",
    page_icon="📤",
    layout="centered",
)

st.title("📤 客户进度报告")
st.caption("生成可安全分享给客户公司的只读 HTML 报告（不含机密信息）。")

try:
    from runtime_paths import get_writable_app_root
    from client_dashboard import collect_company_data, generate_client_dashboard_html
except ImportError as e:
    st.error(f"依赖加载失败：{e}")
    st.stop()

ws_path = Path(get_writable_app_root()).parent

company_id = st.text_input(
    "被访公司 ID",
    placeholder="如：泽天智航_1775917777",
    key="client_report_cid",
)

if not company_id:
    st.info("👆 请先输入被访公司 ID。")
    st.stop()

col_info, col_gen = st.columns([3, 1])
with col_info:
    with st.spinner("扫描数据…"):
        preview = collect_company_data(company_id, ws_path)
    st.caption(
        f"共 {preview['total_sessions']} 场复盘，"
        f"已完成 {preview['locked_sessions']} 场，"
        f"平均得分 {preview['avg_score']:.1f}"
    )

with col_gen:
    st.write("")
    if st.button("生成报告", type="primary", key="client_report_gen"):
        out_dir = ws_path / "client_reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_cid = re.sub(r"[^\w\-]", "_", company_id)
        out_file = out_dir / f"{safe_cid}_client_report.html"
        data = collect_company_data(company_id, ws_path)
        generate_client_dashboard_html(data, out_file)
        html_bytes = out_file.read_bytes()
        st.success(f"✅ 报告已生成：`{out_file.name}`")
        st.download_button(
            "⬇️ 下载 HTML",
            data=html_bytes,
            file_name=out_file.name,
            mime="text/html",
            key="client_report_dl",
        )
