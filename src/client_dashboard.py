"""
客户只读 Dashboard — V10.3 P2.2

功能：扫描指定 company_id 的 analytics JSON，
      聚合为摘要数据，生成可安全分享给客户公司的静态 HTML 文件。

设计原则：
- 不暴露：风险点原文/改进建议/主理人批注等机密内容
- 只展示：会话次数、平均得分、得分趋势、风险类型分布、融资进展
- 生成独立 HTML，无需 Streamlit 即可查看
- collect_company_data() 纯数据，无 LLM
- generate_client_dashboard_html() 纯模板渲染
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def collect_company_data(
    company_id: str,
    workspace_root: Path | str,
) -> dict[str, Any]:
    """
    扫描 workspace_root 下所有 *_analytics.json，聚合指定公司的摘要数据。

    返回字段（全部不含敏感信息）：
      company_id        : str
      total_sessions    : int
      locked_sessions   : int
      avg_score         : float
      score_trend       : list[int]     按时间排序的每场得分
      risk_type_summary : dict[str,int] 风险类型频次汇总
      fundraising_outcomes: dict[str,int] 融资状态统计
      latest_date       : str          最近一场的 generated_at
      session_dates     : list[str]    每场的日期（YYYY-MM-DD）
    """
    workspace_root = Path(workspace_root)
    cid = (company_id or "").strip()

    sessions: list[dict] = []

    for p in workspace_root.rglob("*_analytics.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if (data.get("company_id", "") or "").strip() != cid:
            continue
        sessions.append(data)

    if not sessions:
        return {
            "company_id": cid,
            "total_sessions": 0,
            "locked_sessions": 0,
            "avg_score": 0.0,
            "score_trend": [],
            "risk_type_summary": {},
            "fundraising_outcomes": {},
            "latest_date": "",
            "session_dates": [],
        }

    # 按时间排序
    def _parse_dt(s: dict) -> str:
        return s.get("generated_at") or s.get("locked_at") or ""
    sessions.sort(key=_parse_dt)

    total = len(sessions)
    locked = sum(1 for s in sessions if s.get("status") == "locked")
    scores = [s.get("total_score", 0) for s in sessions]
    avg_score = round(sum(scores) / total, 1) if total > 0 else 0.0

    # 风险类型频次（聚合 risk_type_counts）
    risk_counter: Counter[str] = Counter()
    for s in sessions:
        for rt, cnt in (s.get("risk_type_counts") or {}).items():
            risk_counter[rt] += cnt

    # 融资状态统计
    fundraising_counter: Counter[str] = Counter()
    for s in sessions:
        fo = (s.get("fundraising_outcome") or "").strip()
        if fo:
            fundraising_counter[fo] += 1

    # 日期列表
    dates: list[str] = []
    for s in sessions:
        dt_str = s.get("generated_at") or ""
        if len(dt_str) >= 10:
            dates.append(dt_str[:10])
        else:
            dates.append("")

    latest = dates[-1] if dates else ""

    return {
        "company_id": cid,
        "total_sessions": total,
        "locked_sessions": locked,
        "avg_score": avg_score,
        "score_trend": scores,
        "risk_type_summary": dict(risk_counter.most_common(10)),
        "fundraising_outcomes": dict(fundraising_counter),
        "latest_date": latest,
        "session_dates": dates,
    }


def generate_client_dashboard_html(
    data: dict[str, Any],
    output_path: Path | str,
) -> Path:
    """
    将 collect_company_data() 返回的摘要渲染为独立 HTML 文件。

    HTML 不含任何机密信息（风险点详情、改进建议、逐字稿）。
    返回写入的 Path。
    """
    output_path = Path(output_path)
    cid = data.get("company_id", "")
    total = data.get("total_sessions", 0)
    locked = data.get("locked_sessions", 0)
    avg = data.get("avg_score", 0.0)
    trend = data.get("score_trend", [])
    risk_summary = data.get("risk_type_summary", {})
    fundraising = data.get("fundraising_outcomes", {})
    latest_date = data.get("latest_date", "")
    session_dates = data.get("session_dates", [])

    # 得分趋势图数据（JSON 序列化给 JS）
    trend_labels = [f"第{i+1}场 {session_dates[i] if i < len(session_dates) else ''}" for i in range(len(trend))]
    trend_json = json.dumps(trend)
    labels_json = json.dumps(trend_labels, ensure_ascii=False)

    # 风险类型列表
    risk_rows = ""
    for rt, cnt in risk_summary.items():
        risk_rows += f"<tr><td>{rt}</td><td>{cnt}</td></tr>"

    # 融资进展
    fundraising_html = ""
    for status, cnt in fundraising.items():
        badge_class = "badge-success" if status == "已成功" else (
            "badge-warning" if status == "进行中" else "badge-muted"
        )
        fundraising_html += f'<span class="badge {badge_class}">{status} × {cnt}</span> '

    # 均分颜色
    if avg >= 80:
        score_class = "score-green"
    elif avg >= 65:
        score_class = "score-yellow"
    else:
        score_class = "score-red"

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>路演复盘进度报告 — {cid}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
  .card {{ background: #fff; border-radius: 12px; padding: 24px; margin-bottom: 20px;
           box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
  .subtitle {{ color: #64748b; font-size: .9rem; margin-bottom: 24px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 16px; }}
  .kpi {{ text-align: center; padding: 16px; background: #f1f5f9; border-radius: 8px; }}
  .kpi-val {{ font-size: 2rem; font-weight: 700; }}
  .kpi-label {{ font-size: .8rem; color: #64748b; margin-top: 4px; }}
  .score-green {{ color: #16a34a; }}
  .score-yellow {{ color: #ca8a04; }}
  .score-red {{ color: #dc2626; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-size: .9rem; }}
  th {{ background: #f8fafc; font-weight: 600; }}
  .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: .8rem; margin-right: 6px; }}
  .badge-success {{ background: #dcfce7; color: #166534; }}
  .badge-warning {{ background: #fef9c3; color: #854d0e; }}
  .badge-muted {{ background: #f1f5f9; color: #475569; }}
  canvas {{ max-height: 220px; }}
  .footer {{ text-align: center; color: #94a3b8; font-size: .75rem; margin-top: 32px; }}
  .watermark {{ color: #e2e8f0; font-size: .7rem; text-align: right; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>

<div class="card">
  <h1>📊 路演复盘进度报告</h1>
  <div class="subtitle">公司：{cid} &nbsp;｜&nbsp; 生成时间：{now_str}</div>

  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-val">{total}</div>
      <div class="kpi-label">总复盘场次</div>
    </div>
    <div class="kpi">
      <div class="kpi-val">{locked}</div>
      <div class="kpi-label">已完成场次</div>
    </div>
    <div class="kpi">
      <div class="kpi-val {score_class}">{avg:.1f}</div>
      <div class="kpi-label">平均路演得分</div>
    </div>
    <div class="kpi">
      <div class="kpi-val">{latest_date or "—"}</div>
      <div class="kpi-label">最近复盘日期</div>
    </div>
  </div>
</div>

{"" if not trend else f'''
<div class="card">
  <h2 style="font-size:1rem;margin-bottom:16px;">📈 得分趋势</h2>
  <canvas id="trendChart"></canvas>
</div>
<script>
  new Chart(document.getElementById("trendChart"), {{
    type: "line",
    data: {{
      labels: {labels_json},
      datasets: [{{
        label: "路演得分",
        data: {trend_json},
        borderColor: "#3b82f6",
        backgroundColor: "rgba(59,130,246,0.08)",
        tension: 0.3,
        pointRadius: 5,
        fill: true,
      }}]
    }},
    options: {{
      scales: {{ y: {{ min: 0, max: 100, grid: {{ color: "#f1f5f9" }} }} }},
      plugins: {{ legend: {{ display: false }} }},
    }}
  }});
</script>
'''}

{"" if not risk_summary else f'''
<div class="card">
  <h2 style="font-size:1rem;margin-bottom:12px;">⚠️ 高频风险类型（累计）</h2>
  <table>
    <tr><th>风险类型</th><th>出现次数</th></tr>
    {risk_rows}
  </table>
</div>
'''}

{"" if not fundraising else f'''
<div class="card">
  <h2 style="font-size:1rem;margin-bottom:12px;">💰 融资进展</h2>
  <p>{fundraising_html}</p>
</div>
'''}

{"<div class='card'><p style='color:#94a3b8;text-align:center'>暂无复盘数据，完成至少一场后将自动生成。</p></div>" if total == 0 else ""}

<div class="footer">
  本报告由 AI 路演教练系统自动生成，仅供内部参考。<br>
  数据截至 {now_str}
</div>

</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("client_dashboard: 已生成 %s（%d 场）", output_path.name, total)
    return output_path
