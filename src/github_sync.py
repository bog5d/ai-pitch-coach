"""
GitHub 数据同步模块 — V10.2 数据飞轮。

将本地 *_analytics.json 文件推送到私有 GitHub repo（coach_data），
实现多人、多机器产生的数据向主理人处汇聚。

目录结构：
  coach_data/
    analytics/{ascii_safe_company_segment}/  （与本地 company_id 一一对应，见 analytics_repo_company_segment）
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
import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import tempfile
from datetime import date, datetime, timezone

from dotenv import load_dotenv
from runtime_paths import get_writable_app_root

load_dotenv(get_writable_app_root() / ".env")
logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_TIMEOUT = 30
_STATUS_FILENAME = "github_sync_status.json"
_MAX_CONSECUTIVE_FAILURES = 3   # 超过此次数在 Dashboard 显示红色告警


def _default_channel_status() -> dict:
    return {
        "last_attempt": None,
        "last_success": None,
        "last_error": None,
        "consecutive_failures": 0,
    }


def _default_status() -> dict:
    return {
        "last_attempt": None,
        "last_success": None,
        "last_error": None,
        "consecutive_failures": 0,
        "channels": {
            "analytics": _default_channel_status(),
            "institutions": _default_channel_status(),
        },
    }


def _get_status_path() -> Path:
    try:
        return Path(get_writable_app_root()) / _STATUS_FILENAME
    except Exception:
        return Path(".") / _STATUS_FILENAME


def _load_sync_status() -> dict:
    path = _get_status_path()
    if not path.exists():
        return _default_status()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _default_status()
        merged = _default_status()
        # 兼容旧版顶层状态
        for k in ("last_attempt", "last_success", "last_error", "consecutive_failures"):
            if k in raw:
                merged[k] = raw.get(k)
        # 新版按通道状态
        channels = raw.get("channels")
        if isinstance(channels, dict):
            for ch in ("analytics", "institutions"):
                if isinstance(channels.get(ch), dict):
                    for k in ("last_attempt", "last_success", "last_error", "consecutive_failures"):
                        if k in channels[ch]:
                            merged["channels"][ch][k] = channels[ch].get(k)
        else:
            # 旧版文件：把顶层状态映射到 analytics 通道，避免丢失历史
            for k in ("last_attempt", "last_success", "last_error", "consecutive_failures"):
                merged["channels"]["analytics"][k] = merged.get(k)
        return merged
    except Exception:
        return _default_status()


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


def _record_success(channel: str = "analytics") -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    status = _load_sync_status()
    ch = status.setdefault("channels", {}).setdefault(channel, _default_channel_status())
    ch.update(last_attempt=now, last_success=now, last_error=None, consecutive_failures=0)
    # 顶层保留“最近一次任意成功”的语义
    status.update(last_attempt=now, last_success=now, last_error=None, consecutive_failures=0)
    _save_sync_status(status)


def _record_failure(error: str, channel: str = "analytics") -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    status = _load_sync_status()
    ch = status.setdefault("channels", {}).setdefault(channel, _default_channel_status())
    ch["last_attempt"] = now
    ch["last_error"] = error
    ch["consecutive_failures"] = int(ch.get("consecutive_failures", 0) or 0) + 1
    # 顶层保留“最近一次失败”的兼容字段
    status["last_attempt"] = now
    status["last_error"] = f"{channel}: {error}"
    status["consecutive_failures"] = ch["consecutive_failures"]
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
    analytics = status.get("channels", {}).get("analytics", _default_channel_status())
    institutions = status.get("channels", {}).get("institutions", _default_channel_status())
    analytics_needs_alert = (
        not configured
        or int(analytics.get("consecutive_failures", 0) or 0) >= _MAX_CONSECUTIVE_FAILURES
    )
    institutions_needs_alert = (
        not configured
        or int(institutions.get("consecutive_failures", 0) or 0) >= _MAX_CONSECUTIVE_FAILURES
    )
    status["analytics"] = {
        **analytics,
        "needs_alert": analytics_needs_alert,
    }
    status["institutions"] = {
        **institutions,
        "needs_alert": institutions_needs_alert,
    }
    status["needs_alert"] = analytics_needs_alert or institutions_needs_alert
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
    *,
    channel: str = "analytics",
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
        _record_failure(err_msg, channel=channel)
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
            _record_success(channel=channel)
            return True
        else:
            msg = body.get("message", "unknown error")
            err_msg = f"HTTP {status}: {msg}"
            logger.warning("github_sync: 推送失败 %s（%s）", local_path.name, err_msg)
            _record_failure(err_msg, channel=channel)
            return False

    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        logger.warning("github_sync: 推送异常，静默跳过（%s）", err_msg)
        _record_failure(err_msg, channel=channel)
        return False


def analytics_repo_company_segment(company_id: str) -> str:
    """
    与 sync_analytics 上传一致的仓库子目录名：analytics/{segment}/。
    GitHub Contents API 要求路径 URL-safe；中文 company_id 经 ASCII 化 + 短哈希防碰撞。
    """
    raw_company = (company_id or "").strip() or "unknown"
    ascii_part = re.sub(r"[^A-Za-z0-9_-]", "_", raw_company).strip("_")
    if not ascii_part:
        ascii_part = "company"
    return f"{ascii_part}_{hashlib.sha1(raw_company.encode('utf-8')).hexdigest()[:8]}"


def _analytics_json_not_before(data: dict, not_before: date | None) -> bool:
    """若 not_before 有值，仅当 locked_at/generated_at 的日期部分 >= not_before 时返回 True。"""
    if not_before is None:
        return True
    ts = (data.get("locked_at") or data.get("generated_at") or "").strip()
    if len(ts) < 10:
        return True
    try:
        return ts[:10] >= not_before.isoformat()
    except Exception:
        return True


def sync_analytics(
    analytics_path: Path,
    company_id: str,
) -> bool:
    """
    推送单个 analytics JSON 到 coach_data/analytics/{segment}/{filename}。
    供 analytics_exporter 锁定时调用。
    """
    if not analytics_path or not analytics_path.exists():
        return False

    safe_company = analytics_repo_company_segment(company_id)
    raw_name = analytics_path.name
    suffix = analytics_path.suffix or ".json"
    stem = analytics_path.stem
    stem_ascii = re.sub(r"[^A-Za-z0-9_-]", "_", stem).strip("_")
    if not stem_ascii:
        stem_ascii = "analytics"
    safe_name = f"{stem_ascii}_{hashlib.sha1(raw_name.encode('utf-8')).hexdigest()[:8]}{suffix}"
    path_in_repo = f"analytics/{safe_company}/{safe_name}"
    return push_file(
        analytics_path,
        path_in_repo,
        commit_message=f"sync analytics: {raw_name}",
        channel="analytics",
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
            channel="institutions",
        )
    except Exception as exc:
        logger.warning("github_sync: sync_institutions 失败（%s）", exc)
        return False


def pull_analytics_for_company(
    company_id: str,
    workspace_root: Path | str,
    *,
    not_before: date | None = None,
) -> int:
    """
    仅从 coach_data 拉取**当前公司**对应目录下的 analytics JSON，写入本机工作区：

      {workspace_root}/.coach_data_pull/analytics/{segment}/*.json

    与上传路径 `analytics/{segment}/` 一致，不拉取其他公司目录。
    not_before：可选，仅写入 locked_at/generated_at 日期 >= 该日的记录（按 JSON 内字段过滤）。

    返回成功写入的文件数。配置缺失或远端无目录时返回 0。
    """
    try:
        pat, owner, repo = _get_config()
    except ValueError as exc:
        logger.warning("github_sync: pull_analytics_for_company 配置缺失（%s）", exc)
        return 0

    segment = analytics_repo_company_segment(company_id)
    ws = Path(workspace_root)
    out_dir = ws / ".coach_data_pull" / "analytics" / segment
    out_dir.mkdir(parents=True, exist_ok=True)

    # segment 已由 analytics_repo_company_segment 约束为 [A-Za-z0-9_]，无需再 quote
    status, body = _api_request(
        "GET",
        f"/repos/{owner}/{repo}/contents/analytics/{segment}",
        pat,
    )
    if status == 404:
        logger.info("github_sync: 远端无 analytics/%s，跳过拉取", segment)
        return 0
    if status != 200 or not isinstance(body, list):
        logger.warning("github_sync: 列出 analytics/%s 失败（HTTP %s）", segment, status)
        return 0

    count = 0
    for entry in body:
        if entry.get("type") != "file":
            continue
        name = entry.get("name") or ""
        if not name.endswith(".json"):
            continue
        dl_url = entry.get("download_url") or ""
        if not dl_url:
            continue
        try:
            req = urllib.request.Request(
                dl_url,
                headers={"Authorization": f"Bearer {pat}"},
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                content = resp.read()
            if not_before is not None:
                try:
                    data = json.loads(content.decode("utf-8"))
                    if not isinstance(data, dict) or not _analytics_json_not_before(data, not_before):
                        continue
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
            out_path = out_dir / name
            out_path.write_bytes(content)
            count += 1
        except Exception as exc:
            logger.warning("github_sync: 拉取失败 %s（%s）", name, exc)

    logger.info(
        "github_sync: 公司已拉取 %d 个文件 → %s",
        count,
        out_dir,
    )
    return count


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
            name = f.get("name") or ""
            if not name.endswith(".json"):
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
