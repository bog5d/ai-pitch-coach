"""
Analytics JSON 导出层 — V10.1 双态覆写。

写入时机：
  1. AI 分析完成（初稿就绪）→ status="draft"，refinement/ai_miss 为 0
  2. 用户在审查台锁定导出 → status="locked"，更新所有字段为最终值

同一个 stem 始终写同一个文件（{stem}_analytics.json），locked 覆盖 draft，
保证"凡运行必留痕"，不依赖用户是否修改或锁定。

设计原则：
- 失败时返回 None 并静默跳过，绝不影响主流程（HTML/JSON 生成）
- 不依赖 Streamlit session_state，只接收已完成的 report 和 ctx
- session_id 由 stem 名确定性生成，同一 stem 始终相同（uuid5）
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

_ANALYTICS_VERSION = "V10.3"
# 截断标记关键词（与 llm_judge.py BUG-1 修复对齐）
_TRUNCATION_KEYWORDS = ("截断", "salvage", "被截断")
# uuid5 namespace for deterministic session_id based on stem
_SESSION_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_URL


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


def _build_risk_type_counts(report: AnalysisReport) -> dict[str, int]:
    """统计每种 risk_type 出现次数，供个人成长引擎分析弱点维度。"""
    from collections import Counter
    counts: Counter[str] = Counter()
    for rp in report.risk_points:
        rt = (rp.risk_type or "").strip()
        if rt:
            counts[rt] += 1
    return dict(counts)


def _is_stage1_truncated(report: AnalysisReport) -> bool:
    """判断报告是否经历过阶段一 JSON 截断（检查 deduction_reason 中的截断标记）。"""
    reason = (report.total_score_deduction_reason or "").lower()
    return any(kw in reason for kw in _TRUNCATION_KEYWORDS)


def export_analytics(
    report: AnalysisReport,
    ctx: dict,
    *,
    status: str = "locked",
) -> Path | None:
    """
    基于报告和上下文，生成（或覆写）analytics JSON 并落盘。

    参数：
        report  : AnalysisReport 对象（draft 时为 AI 初稿，locked 时为最终版）。
        ctx     : v3_ctx_{stem} 字典，含 analysis_json、company_id、interviewee 等。
        status  : "draft"（AI 生成完毕，用户未审查）或 "locked"（用户锁定导出后）。
                  locked 会覆盖同 stem 的 draft 记录。

    返回：
        成功时返回 analytics 文件的 Path；写入失败时静默返回 None。
    """
    try:
        analysis_json_path = Path(ctx.get("analysis_json", ""))
        if not analysis_json_path.parent.exists():
            return None

        analytics_path = analysis_json_path.parent / (
            analysis_json_path.stem + "_analytics.json"
        )

        # 确定性 session_id：用完整绝对路径生成，不同目录同名文件不会碰撞
        # 同一路径 draft/locked 多次调用保持相同 ID（覆盖语义）
        stem_name = analysis_json_path.stem  # 保留 stem_name 供后续 recording_label 字段使用
        _id_seed = str(analysis_json_path.resolve())
        session_id = str(uuid.uuid5(_SESSION_NS, _id_seed))

        # draft 覆写时保留原始生成时间；locked 时更新为当前时间
        generated_at = _iso_now_utc_z()
        if status == "locked" and analytics_path.exists():
            try:
                existing = json.loads(analytics_path.read_text(encoding="utf-8"))
                generated_at = existing.get("generated_at", generated_at)
            except (json.JSONDecodeError, OSError):
                pass

        payload = {
            "session_id": session_id,
            "generated_at": generated_at,
            "locked_at": _iso_now_utc_z() if status == "locked" else None,
            "status": status,
            "version": _ANALYTICS_VERSION,
            "company_id": (ctx.get("company_id") or "").strip(),
            "interviewee": (ctx.get("interviewee") or "").strip(),
            "biz_type": (ctx.get("biz_type") or "").strip(),
            "recording_label": stem_name,
            "institution_id": (ctx.get("institution_id") or "").strip(),
            "institution_canonical": (ctx.get("institution_canonical") or "").strip(),
            # V10.3 P1.2 融资结果（锁定时由用户填写，空 = 未记录）
            "fundraising_outcome": (ctx.get("fundraising_outcome") or "").strip(),
            "fundraising_amount": (ctx.get("fundraising_amount") or "").strip(),
            "fundraising_valuation": (ctx.get("fundraising_valuation") or "").strip(),
            # V10.3 P3.2 投资人姓名（Partner 级别画像）
            "investor_name": (ctx.get("investor_name") or "").strip(),
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
            "risk_type_counts": _build_risk_type_counts(report),
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
