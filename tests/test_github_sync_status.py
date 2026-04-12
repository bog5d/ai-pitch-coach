"""
github_sync 状态追踪测试 — V10.3 P0.3
运行：pytest tests/test_github_sync_status.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import github_sync as gs


def _mock_status_path(tmp_path: Path):
    status_file = tmp_path / "github_sync_status.json"
    return patch.object(gs, "_get_status_path", return_value=status_file)


# ── 成功记录 ──────────────────────────────────────────────────────────────────

def test_record_success_resets_failures(tmp_path):
    with _mock_status_path(tmp_path):
        gs._record_failure("first error")
        gs._record_failure("second error")
        gs._record_success()
        status = gs._load_sync_status()
    assert status["consecutive_failures"] == 0
    assert status["last_error"] is None
    assert status["last_success"] is not None


def test_record_failure_increments_count(tmp_path):
    with _mock_status_path(tmp_path):
        gs._record_failure("err1")
        gs._record_failure("err2")
        status = gs._load_sync_status()
    assert status["consecutive_failures"] == 2
    assert status["last_error"] == "err2"


# ── needs_alert 触发条件 ──────────────────────────────────────────────────────

def test_needs_alert_after_threshold_failures(tmp_path):
    with _mock_status_path(tmp_path):
        for _ in range(gs._MAX_CONSECUTIVE_FAILURES):
            gs._record_failure("persistent error")
        with patch.dict("os.environ", {
            "COACH_DATA_GITHUB_PAT": "fake_pat",
            "COACH_DATA_GITHUB_REPO": "https://github.com/a/b.git",
        }):
            s = gs.get_sync_status()
    assert s["needs_alert"] is True


def test_no_alert_after_success(tmp_path):
    with _mock_status_path(tmp_path):
        for _ in range(gs._MAX_CONSECUTIVE_FAILURES):
            gs._record_failure("error")
        gs._record_success()
        with patch.dict("os.environ", {
            "COACH_DATA_GITHUB_PAT": "fake_pat",
            "COACH_DATA_GITHUB_REPO": "https://github.com/a/b.git",
        }):
            s = gs.get_sync_status()
    assert s["needs_alert"] is False


def test_needs_alert_when_not_configured(tmp_path):
    with _mock_status_path(tmp_path):
        with patch.dict("os.environ", {
            "COACH_DATA_GITHUB_PAT": "",
            "COACH_DATA_GITHUB_REPO": "",
        }):
            s = gs.get_sync_status()
    assert s["needs_alert"] is True
    assert s["configured"] is False


def test_configured_true_when_pat_set(tmp_path):
    with _mock_status_path(tmp_path):
        with patch.dict("os.environ", {
            "COACH_DATA_GITHUB_PAT": "ghp_xxx",
            "COACH_DATA_GITHUB_REPO": "https://github.com/owner/repo.git",
        }):
            s = gs.get_sync_status()
    assert s["configured"] is True


# ── push_file 联动状态记录 ─────────────────────────────────────────────────────

def test_push_file_success_calls_record_success(tmp_path):
    f = tmp_path / "test.json"
    f.write_text("{}", encoding="utf-8")
    with _mock_status_path(tmp_path):
        with patch.dict("os.environ", {
            "COACH_DATA_GITHUB_PAT": "fake",
            "COACH_DATA_GITHUB_REPO": "https://github.com/a/b.git",
        }):
            with patch.object(gs, "_get_file_sha", return_value=None):
                with patch.object(gs, "_api_request", return_value=(201, {})):
                    gs.push_file(f, "test/path.json")
        status = gs._load_sync_status()
    assert status["consecutive_failures"] == 0
    assert status["last_success"] is not None


def test_push_file_failure_calls_record_failure(tmp_path):
    f = tmp_path / "test.json"
    f.write_text("{}", encoding="utf-8")
    with _mock_status_path(tmp_path):
        with patch.dict("os.environ", {
            "COACH_DATA_GITHUB_PAT": "fake",
            "COACH_DATA_GITHUB_REPO": "https://github.com/a/b.git",
        }):
            with patch.object(gs, "_get_file_sha", return_value=None):
                with patch.object(gs, "_api_request", return_value=(403, {"message": "forbidden"})):
                    gs.push_file(f, "test/path.json")
        status = gs._load_sync_status()
    assert status["consecutive_failures"] == 1
    assert "403" in (status["last_error"] or "")


def test_empty_status_file_handled_gracefully(tmp_path):
    status_file = tmp_path / "github_sync_status.json"
    status_file.write_text("BROKEN", encoding="utf-8")
    with patch.object(gs, "_get_status_path", return_value=status_file):
        status = gs._load_sync_status()
    assert status["consecutive_failures"] == 0
