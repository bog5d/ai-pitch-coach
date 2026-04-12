"""
客户只读 Dashboard 测试 — V10.3 P2.2
运行：pytest tests/test_client_dashboard.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import client_dashboard as cd


def _make_analytics(tmp_path: Path, company_id: str, score: int, sessions: int = 1) -> list[Path]:
    """在 tmp_path 下创建若干 analytics JSON 文件（按公司分子目录，避免文件名冲突）。"""
    import re
    safe_cid = re.sub(r"[^\w]", "_", company_id)
    company_dir = tmp_path / safe_cid
    company_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(sessions):
        payload = {
            "session_id": f"session-{safe_cid}-{i}",
            "status": "locked",
            "company_id": company_id,
            "interviewee": "张总",
            "recording_label": f"{safe_cid}-session-{i}_analysis_report",
            "total_score": score,
            "total_risk_count": 3,
            "risk_breakdown": {
                "严重": {"count": 1, "total_deduction": 10},
                "一般": {"count": 1, "total_deduction": 5},
                "轻微": {"count": 1, "total_deduction": 2},
            },
            "refinement_count": 0,
            "ai_miss_count": 0,
            "risk_type_counts": {"估值回避": 2, "数据含糊": 1},
            "generated_at": f"2026-04-{10+i:02d}T12:00:00Z",
            "fundraising_outcome": "进行中",
        }
        p = company_dir / f"{safe_cid}_session_{i}_analytics.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        paths.append(p)
    return paths


# ── 数据聚合 ──────────────────────────────────────────────────────────────────

def test_collect_company_data_returns_correct_sessions(tmp_path):
    """collect_company_data 正确扫描并聚合该公司的 analytics。"""
    _make_analytics(tmp_path, "公司A", score=80, sessions=3)
    data = cd.collect_company_data("公司A", tmp_path)
    assert data["total_sessions"] == 3
    assert data["company_id"] == "公司A"


def test_collect_company_data_excludes_other_companies(tmp_path):
    """仅包含指定公司的数据，不混入其他公司。"""
    _make_analytics(tmp_path, "公司A", score=80, sessions=2)
    _make_analytics(tmp_path, "公司B", score=70, sessions=3)
    data = cd.collect_company_data("公司A", tmp_path)
    assert data["total_sessions"] == 2


def test_collect_company_data_calculates_avg_score(tmp_path):
    """avg_score 正确计算：4 场均分 = (80+80+80+80)/4 = 80。"""
    _make_analytics(tmp_path, "公司A", score=80, sessions=4)
    data = cd.collect_company_data("公司A", tmp_path)
    assert data["total_sessions"] == 4
    assert abs(data["avg_score"] - 80.0) < 0.1


def test_collect_company_data_empty_workspace(tmp_path):
    """空工作区返回零值字典，不崩溃。"""
    data = cd.collect_company_data("公司A", tmp_path)
    assert data["total_sessions"] == 0
    assert data["avg_score"] == 0.0


def test_collect_company_data_locked_count(tmp_path):
    """locked_sessions 正确统计。"""
    # 混合 locked 和 draft
    payload_locked = {
        "session_id": "s1", "status": "locked",
        "company_id": "公司A", "total_score": 80,
        "risk_breakdown": {}, "risk_type_counts": {},
    }
    payload_draft = {
        "session_id": "s2", "status": "draft",
        "company_id": "公司A", "total_score": 70,
        "risk_breakdown": {}, "risk_type_counts": {},
    }
    (tmp_path / "a_analytics.json").write_text(json.dumps(payload_locked), encoding="utf-8")
    (tmp_path / "b_analytics.json").write_text(json.dumps(payload_draft), encoding="utf-8")
    data = cd.collect_company_data("公司A", tmp_path)
    assert data["locked_sessions"] == 1
    assert data["total_sessions"] == 2


# ── HTML 生成 ─────────────────────────────────────────────────────────────────

def test_generate_html_creates_file(tmp_path):
    """generate_client_dashboard_html 生成有效 HTML 文件。"""
    _make_analytics(tmp_path, "公司A", score=80, sessions=2)
    data = cd.collect_company_data("公司A", tmp_path)
    out_path = tmp_path / "client_dashboard.html"
    cd.generate_client_dashboard_html(data, out_path)
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "<html" in content.lower()


def test_generate_html_contains_company_id(tmp_path):
    """HTML 包含公司 ID（基本内容校验）。"""
    _make_analytics(tmp_path, "公司A", score=80)
    data = cd.collect_company_data("公司A", tmp_path)
    out_path = tmp_path / "out.html"
    cd.generate_client_dashboard_html(data, out_path)
    content = out_path.read_text(encoding="utf-8")
    assert "公司A" in content


def test_generate_html_no_sensitive_data(tmp_path):
    """HTML 不应包含主理人机密信息（如 AI 纠偏内容、原始音频路径等）。"""
    _make_analytics(tmp_path, "公司A", score=80)
    data = cd.collect_company_data("公司A", tmp_path)
    out_path = tmp_path / "out.html"
    cd.generate_client_dashboard_html(data, out_path)
    content = out_path.read_text(encoding="utf-8")
    # 不应出现原始逐字稿、风险点原文等机密内容
    assert "tier1_general_critique" not in content
    assert "original_text" not in content


def test_generate_html_empty_data(tmp_path):
    """空数据（无 sessions）生成 HTML 不崩溃。"""
    data = cd.collect_company_data("空公司", tmp_path)
    out_path = tmp_path / "empty.html"
    cd.generate_client_dashboard_html(data, out_path)
    assert out_path.exists()


def test_generate_html_contains_score(tmp_path):
    """HTML 包含平均得分数据。"""
    _make_analytics(tmp_path, "公司A", score=85, sessions=2)
    data = cd.collect_company_data("公司A", tmp_path)
    out_path = tmp_path / "out.html"
    cd.generate_client_dashboard_html(data, out_path)
    content = out_path.read_text(encoding="utf-8")
    assert "85" in content


# ── 无敏感字段检测 ────────────────────────────────────────────────────────────

def test_collect_data_strips_risk_details(tmp_path):
    """collect_company_data 不暴露逐条风险点详情。"""
    _make_analytics(tmp_path, "公司A", score=75)
    data = cd.collect_company_data("公司A", tmp_path)
    # 数据层不应含有逐条 risk_point 内容
    data_str = json.dumps(data, ensure_ascii=False)
    assert "tier1_general_critique" not in data_str
    assert "improvement_suggestion" not in data_str
