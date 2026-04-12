"""
GitHub 数据同步模块 — V10.2 数据飞轮。

将本地 *_analytics.json 文件推送到私有 GitHub repo（coach_data），
实现多人、多机器产生的数据向主理人处汇聚。

目录结构：
  coach_data/
    analytics/{company_id}/{stem}_analytics.json
    institutions/institutions.json  （机构注册表）

设计原则：
- 推送失败静默返回 False，不影响主流程（与 analytics_exporter 一致）
- 使用 GitHub REST API v3（Content API），无需 git 命令行
- PAT 读取自 .env → COACH_DATA_GITHUB_PAT
- REPO 地址读取自 .env → COACH_DATA_GITHUB_REPO
- 内容用 base64 编码，支持 Create + Update（自动检测 SHA）
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import tempfile
from datetime import datetime, timezone

from dotenv import load_dotenv
from runtime_paths import get_writable_app_root

load_dotenv(get_writable_app_root() / ".env")
logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_TIMEOUT = 30
_STATUS_FILENAME = "github_sync_status.json"
_MAX_CONSECUTIVE_FAILURES = 3   # 超过此次数在 Dashboard 显示红色告警


def _get_status_path() -> Path:
    try:
        return Path(get_writable_app_root()) / _STATUS_FILENAME
    except Exception:
        return Path(".") / _STATUS_FILENAME


def _load_sync_status() -> dict:
    path = _get_status_path()
    if not path.exists():
        return {"last_attempt": None, "last_success": None,
                "last_error": None, "consecutive_failures": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"last_attempt": None, "last_success": None,
                "last_error": None, "consecutive_failures": 0}


def _save_sync_status(status: dict) -> None:
    """原子写入同步状态。"""
    path = _get_status_path()
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(status, ensure_ascii=False, indent=2))
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception as exc:
        logger.debug("github_sync: 状态写入失败（%s）", exc)


def _record_success() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    status = _load_sync_status()
    status.update(last_attempt=now, last_success=now,
                  last_error=None, consecutive_failures=0)
    _save_sync_status(status)


def _record_failure(error: str) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    status = _load_sync_status()
    status["last_attempt"] = now
    status["last_error"] = error
    status["consecutive_failures"] = status.get("consecutive_failures", 0) + 1
    _save_sync_status(status)


def get_sync_status() -> dict:
    """
    返回当前同步状态，供 Dashboard 显示。

    返回字段：
      last_attempt        : ISO 时间字符串 | None
      last_success        : ISO 时间字符串 | None
      last_error          : 错误描述 | None
      consecutive_failures: 连续失败次数
      needs_alert         : bool — 是否需要显示红色告警
      configured          : bool — PAT/REPO 是否已配置
    """
    status = _load_sync_status()
    try:
        _get_config()
        configured = True
    except ValueError:
        configured = False
    status["needs_alert"] = (
        not configured
        or status.get("consecutive_failures", 0) >= _MAX_CONSECUTIVE_FAILURES
    )
    status["configured"] = configured
    return status


def _get_config() -> tuple[str, str, str]:
    """
    返回 (pat, owner, repo_name)。
    COACH_DATA_GITHUB_REPO 格式：https://github.com/owner/repo.git 或 owner/repo
    """
    pat = os.getenv("COACH_DATA_GITHUB_PAT", "").strip()
    repo_url = os.getenv("COACH_DATA_GITHUB_REPO", "").strip()

    if not pat or not repo_url:
        raise ValueError("COACH_DATA_GITHUB_PAT 或 COACH_DATA_GITHUB_REPO 未配置")

    # 解析 owner/repo
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", repo_url)
    if m:
        owner_repo = m.group(1)
    else:
        owner_repo = repo_url.rstrip(".git")

    parts = owner_repo.strip("/").split("/")
    if len(parts) < 2:
        raise ValueError(f"无法解析 repo 地址：{repo_url}")

    return pat, parts[-2], parts[-1]


def _api_request(
    method: str,
    path: str,
    pat: str,
    payload: Optional[dict] = None,
) -> tuple[int, dict]:
    """发送 GitHub API 请求，返回 (status_code, response_body)。"""
    url = f"{_API_BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            pass
        return e.code, body


def _get_file_sha(owner: str, repo: str, path_in_repo: str, pat: str) -> Optional[str]:
    """获取文件当前 SHA（用于 Update）；文件不存在返回 None。"""
    status, body = _api_request("GET", f"/repos/{owner}/{repo}/contents/{path_in_repo}", pat)
    if status == 200:
        return body.get("sha")
    return None


def push_file(
    local_path: Path,
    path_in_repo: str,
    commit_message: str = "",
) -> bool:
    """
    推送单个文件到 GitHub repo。
    文件已存在则 Update，否则 Create。
    失败静默返回 False。
    """
    try:
        pat, owner, repo = _get_config()
    except ValueError as exc:
        err_msg = f"配置缺失：{exc}"
        logger.warning("github_sync: %s", err_msg)
        _record_failure(err_msg)
        return False

    try:
        content = local_path.read_bytes()
        content_b64 = base64.b64encode(content).decode("ascii")

        sha = _get_file_sha(owner, repo, path_in_repo, pat)

        message = commit_message or f"sync: {local_path.name}"
        payload: dict = {
            "message": message,
            "content": content_b64,
        }
        if sha:
            payload["sha"] = sha

        status, body = _api_request(
            "PUT",
            f"/repos/{owner}/{repo}/contents/{path_in_repo}",
            pat,
            payload,
        )

        if status in (200, 201):
            logger.info("github_sync: 推送成功 %s → %s", local_path.name, path_in_repo)
            _record_success()
            return True
        else:
            msg = body.get("message", "unknown error")
            err_msg = f"HTTP {status}: {msg}"
            logger.warning("github_sync: 推送失败 %s（%s）", local_path.name, err_msg)
            _record_failure(err_msg)
            return False

    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        logger.warning("github_sync: 推送异常，静默跳过（%s）", err_msg)
        _record_failure(err_msg)
        return False


def sync_analytics(
    analytics_path: Path,
    company_id: str,
) -> bool:
    """
    推送单个 analytics JSON 到 coach_data/analytics/{company_id}/{filename}。
    供 analytics_exporter 锁定时调用。
    """
    if not analytics_path or not analytics_path.exists():
        return False

    safe_company = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", company_id or "unknown")
    path_in_repo = f"analytics/{safe_company}/{analytics_path.name}"
    return push_file(
        analytics_path,
        path_in_repo,
        commit_message=f"sync analytics: {analytics_path.name}",
    )


def sync_institutions() -> bool:
    """
    推送机构注册表 institutions.json 到 coach_data/institutions/institutions.json。
    """
    try:
        from institution_registry import _get_registry_path
        reg_path = _get_registry_path()
        if not reg_path.exists():
            return False
        return push_file(
            reg_path,
            "institutions/institutions.json",
            commit_message="sync institutions registry",
        )
    except Exception as exc:
        logger.warning("github_sync: sync_institutions 失败（%s）", exc)
        return False


def pull_all_analytics(dest_dir: Path) -> int:
    """
    从 coach_data/analytics/ 拉取所有 analytics JSON 到本地 dest_dir。
    返回成功拉取的文件数。
    供主理人一键聚合所有数据使用。
    """
    try:
        pat, owner, repo = _get_config()
    except ValueError as exc:
        logger.warning("github_sync: 配置缺失，拉取失败（%s）", exc)
        return 0

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 列出 analytics/ 目录
    status, body = _api_request("GET", f"/repos/{owner}/{repo}/contents/analytics", pat)
    if status != 200:
        logger.warning("github_sync: 无法列出 analytics 目录（%d）", status)
        return 0

    count = 0
    for company_entry in body:
        if company_entry.get("type") != "dir":
            continue
        company_name = company_entry["name"]
        s2, files = _api_request(
            "GET",
            f"/repos/{owner}/{repo}/contents/analytics/{company_name}",
            pat,
        )
        if s2 != 200:
            continue
        for f in files:
            if not f.get("name", "").endswith("_analytics.json"):
                continue
            dl_url = f.get("download_url", "")
            if not dl_url:
                continue
            try:
                req = urllib.request.Request(
                    dl_url,
                    headers={"Authorization": f"Bearer {pat}"},
                )
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    content = resp.read()
                out_path = dest_dir / company_name / f["name"]
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(content)
                count += 1
            except Exception as exc:
                logger.warning("github_sync: 拉取文件失败 %s（%s）", f["name"], exc)

    logger.info("github_sync: 拉取完成，共 %d 个文件", count)
    return count
