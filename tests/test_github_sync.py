"""
github_sync 单元测试 — V10.2
全部 Mock 网络调用，零 API 费用，零网络依赖。
运行：pytest tests/test_github_sync.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import github_sync as gs


def _mock_env(pat="test_pat", repo="https://github.com/owner/repo.git"):
    return patch.dict("os.environ", {
        "COACH_DATA_GITHUB_PAT": pat,
        "COACH_DATA_GITHUB_REPO": repo,
    })


# ── _get_config ───────────────────────────────────────────────────────────────

def test_get_config_parses_https_url():
    with _mock_env(repo="https://github.com/myowner/myrepo.git"):
        pat, owner, repo = gs._get_config()
    assert pat == "test_pat"
    assert owner == "myowner"
    assert repo == "myrepo"


def test_get_config_parses_url_without_git_suffix():
    with _mock_env(repo="https://github.com/myowner/myrepo"):
        _, owner, repo = gs._get_config()
    assert owner == "myowner"
    assert repo == "myrepo"


def test_get_config_missing_pat_raises():
    with patch.dict("os.environ", {"COACH_DATA_GITHUB_PAT": "",
                                    "COACH_DATA_GITHUB_REPO": "https://github.com/a/b.git"}):
        try:
            gs._get_config()
            assert False, "should raise"
        except ValueError:
            pass


# ── push_file ─────────────────────────────────────────────────────────────────

def test_push_file_creates_when_no_sha(tmp_path):
    """文件不存在时应发 Create（无 sha 字段）。"""
    f = tmp_path / "test_analytics.json"
    f.write_text('{"hello": "world"}', encoding="utf-8")

    with _mock_env():
        with patch.object(gs, "_get_file_sha", return_value=None) as mock_sha:
            with patch.object(gs, "_api_request", return_value=(201, {})) as mock_api:
                result = gs.push_file(f, "analytics/company/test.json")

    assert result is True
    call_args = mock_api.call_args
    payload = call_args[0][3]  # 4th positional arg
    assert "sha" not in payload


def test_push_file_updates_when_sha_exists(tmp_path):
    """文件已存在时应发 Update（含 sha 字段）。"""
    f = tmp_path / "test_analytics.json"
    f.write_text('{"hello": "world"}', encoding="utf-8")

    with _mock_env():
        with patch.object(gs, "_get_file_sha", return_value="abc123"):
            with patch.object(gs, "_api_request", return_value=(200, {})) as mock_api:
                result = gs.push_file(f, "analytics/company/test.json")

    assert result is True
    payload = mock_api.call_args[0][3]
    assert payload["sha"] == "abc123"


def test_push_file_returns_false_on_api_error(tmp_path):
    f = tmp_path / "test.json"
    f.write_text("{}", encoding="utf-8")
    with _mock_env():
        with patch.object(gs, "_get_file_sha", return_value=None):
            with patch.object(gs, "_api_request", return_value=(403, {"message": "forbidden"})):
                result = gs.push_file(f, "some/path.json")
    assert result is False


def test_push_file_returns_false_on_missing_config(tmp_path):
    f = tmp_path / "test.json"
    f.write_text("{}", encoding="utf-8")
    with patch.dict("os.environ", {"COACH_DATA_GITHUB_PAT": "",
                                    "COACH_DATA_GITHUB_REPO": ""}):
        result = gs.push_file(f, "some/path.json")
    assert result is False


def test_push_file_returns_false_on_exception(tmp_path):
    f = tmp_path / "test.json"
    f.write_text("{}", encoding="utf-8")
    with _mock_env():
        with patch.object(gs, "_get_file_sha", side_effect=Exception("network down")):
            result = gs.push_file(f, "some/path.json")
    assert result is False


# ── sync_analytics ────────────────────────────────────────────────────────────

def test_analytics_repo_company_segment_stable():
    import hashlib

    cid = "泽天智航_1775917777"
    seg = gs.analytics_repo_company_segment(cid)
    assert seg.endswith("_" + hashlib.sha1(cid.encode("utf-8")).hexdigest()[:8])
    assert seg.startswith("1775917777_")


def test_pull_analytics_for_company_filters_by_date(tmp_path, monkeypatch):
    """Mock API + download；日期过滤应跳过旧文件。"""
    import urllib.request as urllib_req

    old_json = json.dumps(
        {"company_id": "X", "locked_at": "2026-01-01T00:00:00Z"},
        ensure_ascii=False,
    ).encode("utf-8")
    new_json = json.dumps(
        {"company_id": "X", "locked_at": "2026-06-01T12:00:00Z"},
        ensure_ascii=False,
    ).encode("utf-8")

    seg = gs.analytics_repo_company_segment("Co_A")

    def fake_api(method, path, pat, payload=None):
        if method == "GET" and "/contents/analytics/" in path and seg in path:
            return 200, [
                {
                    "type": "file",
                    "name": "a.json",
                    "download_url": "https://example.com/a.json",
                },
                {
                    "type": "file",
                    "name": "b.json",
                    "download_url": "https://example.com/b.json",
                },
            ]
        return 404, {}

    calls = {"i": 0}

    class FakeResp:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def fake_urlopen(req, timeout=30):
        calls["i"] += 1
        return FakeResp(old_json if calls["i"] == 1 else new_json)

    with _mock_env():
        with patch.object(gs, "_api_request", side_effect=fake_api):
            with patch.object(urllib_req, "urlopen", side_effect=fake_urlopen):
                n = gs.pull_analytics_for_company(
                    "Co_A",
                    tmp_path,
                    not_before=date(2026, 4, 1),
                )
    assert n == 1
    out = tmp_path / ".coach_data_pull" / "analytics" / seg
    assert set(out.glob("*.json")) == {out / "b.json"}


def test_sync_analytics_builds_correct_path(tmp_path):
    f = tmp_path / "stem_analytics.json"
    f.write_text("{}", encoding="utf-8")
    with _mock_env():
        with patch.object(gs, "_get_file_sha", return_value=None):
            with patch.object(gs, "_api_request", return_value=(201, {})) as mock_api:
                gs.sync_analytics(f, "泽天智航")
    path_arg = mock_api.call_args[0][1]
    assert "analytics/" in path_arg
    assert ".json" in path_arg


def test_sync_analytics_nonexistent_file_returns_false():
    result = gs.sync_analytics(Path("/nonexistent/file.json"), "company")
    assert result is False


# ── pull_all_analytics ────────────────────────────────────────────────────────

def test_pull_all_analytics_returns_count(tmp_path):
    """Mock GitHub API 返回1个公司1个文件，验证拉取逻辑。"""
    company_list = [{"type": "dir", "name": "company_A"}]
    file_list = [{
        "name": "session1_analytics.json",
        "download_url": "https://raw.githubusercontent.com/owner/repo/main/analytics/company_A/session1_analytics.json",
    }]
    file_content = b'{"total_score": 80}'

    def mock_api(method, path, pat, payload=None):
        if "contents/analytics" in path and "company_A" not in path:
            return 200, company_list
        elif "company_A" in path:
            return 200, file_list
        return 404, {}

    import urllib.request as urllib_req

    class FakeResp:
        status = 200
        def read(self): return file_content
        def __enter__(self): return self
        def __exit__(self, *a): pass

    with _mock_env():
        with patch.object(gs, "_api_request", side_effect=mock_api):
            with patch.object(urllib_req, "urlopen", return_value=FakeResp()):
                count = gs.pull_all_analytics(tmp_path / "pulled")

    assert count == 1
    pulled = list((tmp_path / "pulled" / "company_A").glob("*.json"))
    assert len(pulled) == 1
