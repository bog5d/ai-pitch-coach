"""
公司档案 CRUD — V8.4
纯函数，无 Streamlit 依赖。存储格式：.company_profiles/{company_id}.json
原子写入：写 .tmp → os.replace，防止崩溃产生损坏文件。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from schema import CompanyProfile

_DEFAULT_DIR_NAME = ".company_profiles"


def _resolve_dir(profiles_dir: Path | None) -> Path:
    if profiles_dir is not None:
        return profiles_dir
    return Path(__file__).parent.parent / _DEFAULT_DIR_NAME


def list_companies(profiles_dir: Path | None = None) -> list[CompanyProfile]:
    """返回所有公司档案列表；目录不存在或为空时返回 []。"""
    d = _resolve_dir(profiles_dir)
    if not d.exists():
        return []
    result: list[CompanyProfile] = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result.append(CompanyProfile.model_validate(data))
        except Exception:
            continue
    return result


def load_company(company_id: str, profiles_dir: Path | None = None) -> CompanyProfile | None:
    """按 company_id 加载档案；不存在时返回 None。"""
    d = _resolve_dir(profiles_dir)
    path = d / f"{company_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CompanyProfile.model_validate(data)
    except Exception:
        return None


def save_company(profile: CompanyProfile, profiles_dir: Path | None = None) -> None:
    """原子写入公司档案；自动创建目录；更新 updated_at 时间戳。"""
    d = _resolve_dir(profiles_dir)
    d.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    updated = profile.model_copy(
        update={
            "updated_at": now,
            "created_at": profile.created_at or now,
        }
    )
    final_path = d / f"{profile.company_id}.json"
    tmp_path = d / f"{profile.company_id}.tmp"
    tmp_path.write_text(
        json.dumps(updated.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, final_path)


def delete_company(company_id: str, profiles_dir: Path | None = None) -> None:
    """删除公司档案；不存在时静默跳过。"""
    d = _resolve_dir(profiles_dir)
    path = d / f"{company_id}.json"
    try:
        path.unlink()
    except FileNotFoundError:
        pass
