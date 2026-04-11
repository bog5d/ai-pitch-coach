"""
V10.0 跨公司匿名基准分析引擎测试。

验证：scan_analytics_files 扫描 workspace，build_benchmark 聚合匿名统计，
让每家被访公司都能与行业基准对比。

运行：pytest tests/test_v100_benchmark.py -v
所有测试 zero API cost，无外部依赖。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ── 辅助 ─────────────────────────────────────────────────────────────────────

def _write_analytics(directory: Path, stem: str, payload: dict) -> Path:
    """向指定目录写入一个 analytics JSON 文件并返回路径。"""
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{stem}_analytics.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _make_analytics(
    company_id: str = "迪策资本",
    total_score: int = 72,
    risk_breakdown: dict | None = None,
    refinement_count: int = 0,
    ai_miss_count: int = 0,
    stage1_truncated: bool = False,
) -> dict:
    if risk_breakdown is None:
        risk_breakdown = {
            "严重": {"count": 1, "total_deduction": 15},
            "一般": {"count": 2, "total_deduction": 10},
            "轻微": {"count": 1, "total_deduction": 4},
        }
    return {
        "session_id": "test-uuid",
        "generated_at": "2026-04-11T10:00:00Z",
        "version": "V10.0",
        "company_id": company_id,
        "interviewee": "测试高管",
        "biz_type": "01_机构路演",
        "total_score": total_score,
        "total_risk_count": sum(v["count"] for v in risk_breakdown.values()),
        "risk_breakdown": risk_breakdown,
        "refinement_count": refinement_count,
        "ai_miss_count": ai_miss_count,
        "stage1_truncated": stage1_truncated,
    }


# ════════════════════════════════════════════════════════
# TestScanAnalyticsFiles — 文件扫描
# ════════════════════════════════════════════════════════

class TestScanAnalyticsFiles:
    """scan_analytics_files 正确扫描 workspace 中的 *_analytics.json 文件。"""

    def test_finds_analytics_files_recursively(self, tmp_path):
        """深层嵌套目录下的 analytics 文件都能被找到。"""
        from benchmark_engine import scan_analytics_files

        _write_analytics(tmp_path / "01_机构路演" / "批次A", "session1", _make_analytics())
        _write_analytics(tmp_path / "02_高管访谈" / "迪策", "session2", _make_analytics())

        results = scan_analytics_files(tmp_path)
        assert len(results) == 2

    def test_returns_parsed_dicts(self, tmp_path):
        """返回值为 dict 列表，不是路径列表。"""
        from benchmark_engine import scan_analytics_files

        _write_analytics(tmp_path / "batches", "s1", _make_analytics(total_score=85))
        results = scan_analytics_files(tmp_path)
        assert len(results) == 1
        assert isinstance(results[0], dict)
        assert results[0]["total_score"] == 85

    def test_ignores_non_analytics_json(self, tmp_path):
        """普通 *_analysis_report.json 不被误扫。"""
        from benchmark_engine import scan_analytics_files

        d = tmp_path / "data"
        d.mkdir()
        (d / "session1_analysis_report.json").write_text("{}", encoding="utf-8")
        (d / "session1_transcription.json").write_text("{}", encoding="utf-8")
        _write_analytics(d, "session1", _make_analytics())

        results = scan_analytics_files(tmp_path)
        assert len(results) == 1

    def test_empty_workspace_returns_empty_list(self, tmp_path):
        """空目录返回空列表，不抛异常。"""
        from benchmark_engine import scan_analytics_files
        assert scan_analytics_files(tmp_path) == []

    def test_corrupted_json_skipped(self, tmp_path):
        """损坏的 analytics JSON 被跳过，不中断扫描。"""
        from benchmark_engine import scan_analytics_files

        d = tmp_path / "data"
        d.mkdir()
        (d / "broken_analytics.json").write_text("{NOT JSON}", encoding="utf-8")
        _write_analytics(d, "good", _make_analytics())

        results = scan_analytics_files(tmp_path)
        assert len(results) == 1  # 只有 good_analytics.json

    def test_nonexistent_workspace_returns_empty(self, tmp_path):
        """workspace_root 不存在时返回空列表，不抛异常。"""
        from benchmark_engine import scan_analytics_files
        assert scan_analytics_files(tmp_path / "nonexistent") == []


# ════════════════════════════════════════════════════════
# TestBuildBenchmark — 基准统计聚合
# ════════════════════════════════════════════════════════

class TestBuildBenchmark:
    """build_benchmark 正确聚合跨公司匿名统计数据。"""

    def test_total_sessions(self, tmp_path):
        """total_sessions 等于输入列表长度。"""
        from benchmark_engine import build_benchmark

        data = [_make_analytics() for _ in range(5)]
        result = build_benchmark(data)
        assert result["total_sessions"] == 5

    def test_total_companies(self, tmp_path):
        """total_companies 计算唯一 company_id 数量。"""
        from benchmark_engine import build_benchmark

        data = [
            _make_analytics(company_id="A公司"),
            _make_analytics(company_id="A公司"),
            _make_analytics(company_id="B公司"),
        ]
        result = build_benchmark(data)
        assert result["total_companies"] == 2

    def test_avg_score(self):
        """avg_score 为所有 total_score 的均值。"""
        from benchmark_engine import build_benchmark

        data = [_make_analytics(total_score=s) for s in [60, 70, 80, 90]]
        result = build_benchmark(data)
        assert result["avg_score"] == pytest.approx(75.0)

    def test_score_distribution_buckets(self):
        """score_distribution 正确按档位分桶。"""
        from benchmark_engine import build_benchmark

        data = [
            _make_analytics(total_score=45),   # 0-59
            _make_analytics(total_score=65),   # 60-74
            _make_analytics(total_score=65),   # 60-74
            _make_analytics(total_score=80),   # 75-89
            _make_analytics(total_score=95),   # 90-100
        ]
        result = build_benchmark(data)
        dist = {d["range"]: d["count"] for d in result["score_distribution"]}
        assert dist["0-59"] == 1
        assert dist["60-74"] == 2
        assert dist["75-89"] == 1
        assert dist["90-100"] == 1

    def test_risk_type_frequency(self):
        """risk_type_frequency 正确统计各级别风险点总出现次数。"""
        from benchmark_engine import build_benchmark

        rb_heavy = {"严重": {"count": 2, "total_deduction": 20},
                    "一般": {"count": 1, "total_deduction": 5},
                    "轻微": {"count": 0, "total_deduction": 0}}
        rb_light = {"严重": {"count": 0, "total_deduction": 0},
                    "一般": {"count": 3, "total_deduction": 12},
                    "轻微": {"count": 2, "total_deduction": 4}}
        data = [_make_analytics(risk_breakdown=rb_heavy),
                _make_analytics(risk_breakdown=rb_light)]
        result = build_benchmark(data)
        freq = result["risk_type_frequency"]
        assert freq["严重"] == 2
        assert freq["一般"] == 4
        assert freq["轻微"] == 2

    def test_refinement_rate(self):
        """refinement_rate = 有精炼的场次 / 总场次。"""
        from benchmark_engine import build_benchmark

        data = [
            _make_analytics(refinement_count=2),
            _make_analytics(refinement_count=0),
            _make_analytics(refinement_count=1),
            _make_analytics(refinement_count=0),
        ]
        result = build_benchmark(data)
        assert result["refinement_rate"] == pytest.approx(0.5)

    def test_truncation_rate(self):
        """truncation_rate = stage1_truncated=True 的场次 / 总场次。"""
        from benchmark_engine import build_benchmark

        data = [
            _make_analytics(stage1_truncated=True),
            _make_analytics(stage1_truncated=False),
            _make_analytics(stage1_truncated=False),
        ]
        result = build_benchmark(data)
        assert result["truncation_rate"] == pytest.approx(1 / 3, rel=1e-3)

    def test_required_keys(self):
        """build_benchmark 返回所有必要字段。"""
        from benchmark_engine import build_benchmark

        result = build_benchmark([_make_analytics()])
        required = {
            "total_sessions", "total_companies", "avg_score",
            "score_distribution", "risk_type_frequency",
            "refinement_rate", "truncation_rate",
        }
        missing = required - set(result.keys())
        assert not missing, f"缺少字段：{missing}"

    def test_empty_list_returns_zero_stats(self):
        """空输入返回零值结构，不抛异常。"""
        from benchmark_engine import build_benchmark

        result = build_benchmark([])
        assert result["total_sessions"] == 0
        assert result["avg_score"] == 0.0
        assert result["refinement_rate"] == 0.0


# ════════════════════════════════════════════════════════
# TestBuildBenchmarkFromWorkspace — 端到端扫描+聚合
# ════════════════════════════════════════════════════════

class TestBuildBenchmarkFromWorkspace:
    """scan + build 端到端：从 workspace 扫描并生成基准报告。"""

    def test_end_to_end(self, tmp_path):
        """从 workspace 扫描并聚合，得到正确的基准数据。"""
        from benchmark_engine import build_benchmark, scan_analytics_files

        _write_analytics(tmp_path / "A公司", "s1", _make_analytics(company_id="A公司", total_score=70))
        _write_analytics(tmp_path / "B公司", "s2", _make_analytics(company_id="B公司", total_score=90))
        _write_analytics(tmp_path / "A公司", "s3", _make_analytics(company_id="A公司", total_score=80))

        items = scan_analytics_files(tmp_path)
        result = build_benchmark(items)

        assert result["total_sessions"] == 3
        assert result["total_companies"] == 2
        assert result["avg_score"] == pytest.approx(80.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
