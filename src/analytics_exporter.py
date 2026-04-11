"""
Analytics JSON 导出层 — V10.0 数据飞轮。

每次锁定生成 HTML 时，在 analysis_json 同目录下静默生成
{stem}_analytics.json，包含得分、风险分布、精炼次数等结构化字段，
为后续跨公司数据分析打基础。

设计原则：
- 失败时返回 None 并静默跳过，绝不影响主流程（HTML/JSON 生成）
- 不依赖 Streamlit session_state，只接收已完成的 report 和 ctx
- 不改 schema.py，不改 AnalysisReport 结构

输出格式示例：
{
  "session_id": "uuid-v4",
  "generated_at": "2026-04-11T10:00:00Z",
  "version": "V10.0",
  "company_id": "迪策资本",
  "interviewee": "李志新",
  "biz_type": "01_机构路演",
  "total_score": 72,
  "total_risk_count": 3,
  "risk_breakdown": {
    "严重": {"count": 1, "total_deduction": 15},
    "一般": {"count": 1, "total_deduction": 8},
    "轻微": {"count": 1, "total_deduction": 5}
  },
  "refinement_count": 1,
  "ai_miss_count": 1,
  "stage1_truncated": false
}
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from schema import AnalysisReport

logger = logging.getLogger(__name__)

_ANALYTICS_VERSION = "V10.0"
# 截断标记关键词（与 llm_judge.py BUG-1 修复对齐）
_TRUNCATION_KEYWORDS = ("截断", "salvage", "被截断")


def _iso_now_utc_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_risk_breakdown(report: AnalysisReport) -> dict:
    """按严重程度分桶，统计 count 和 total_deduction。"""
    breakdown: dict[str, dict] = {
        "严重": {"count": 0, "total_deduction": 0},
        "一般": {"count": 0, "total_deduction": 0},
        "轻微": {"count": 0, "total_deduction": 0},
    }
    for rp in report.risk_points:
        level = rp.risk_level
        if level in breakdown:
            breakdown[level]["count"] += 1
            breakdown[level]["total_deduction"] += int(rp.score_deduction or 0)
    return breakdown


def _is_stage1_truncated(report: AnalysisReport) -> bool:
    """判断报告是否经历过阶段一 JSON 截断（检查 deduction_reason 中的截断标记）。"""
    reason = (report.total_score_deduction_reason or "").lower()
    return any(kw in reason for kw in _TRUNCATION_KEYWORDS)


def export_analytics(
    report: AnalysisReport,
    ctx: dict,
) -> Path | None:
    """
    基于锁定后的报告和上下文，生成 analytics JSON 并落盘。

    参数：
        report: 已完成审查台编辑、经 apply_asr_original_text_override 处理的报告。
        ctx: v3_ctx_{stem} 字典，含 analysis_json、company_id、interviewee 等。

    返回：
        成功时返回 analytics 文件的 Path；写入失败时静默返回 None。
    """
    try:
        analysis_json_path = Path(ctx.get("analysis_json", ""))
        if not analysis_json_path.parent.exists():
            # 目录不存在，无法写入，静默返回
            return None

        analytics_path = analysis_json_path.parent / (
            analysis_json_path.stem + "_analytics.json"
        )

        payload = {
            "session_id": str(uuid.uuid4()),
            "generated_at": _iso_now_utc_z(),
            "version": _ANALYTICS_VERSION,
            "company_id": (ctx.get("company_id") or "").strip(),
            "interviewee": (ctx.get("interviewee") or "").strip(),
            "biz_type": (ctx.get("biz_type") or "").strip(),
            "recording_label": analysis_json_path.stem,
            "total_score": report.total_score,
            "total_risk_count": len(report.risk_points),
            "risk_breakdown": _build_risk_breakdown(report),
            "refinement_count": sum(
                1 for rp in report.risk_points if rp.needs_refinement
            ),
            "ai_miss_count": sum(
                1 for rp in report.risk_points if rp.is_manual_entry
            ),
            "stage1_truncated": _is_stage1_truncated(report),
        }

        serialized = json.dumps(payload, ensure_ascii=False, indent=2)

        # 原子写入，防崩溃损坏（与 disk_asr_cache 一致）
        fd, tmp_path = tempfile.mkstemp(
            dir=analytics_path.parent, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(serialized)
            os.replace(tmp_path, analytics_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        logger.info("analytics_exporter: 已导出 %s（得分=%d，风险点=%d）",
                    analytics_path.name, report.total_score, len(report.risk_points))
        return analytics_path

    except Exception as exc:
        logger.warning("analytics_exporter: 导出失败，静默跳过（%s）", exc)
        return None
