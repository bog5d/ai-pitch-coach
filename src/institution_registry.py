"""
机构注册表 — V10.3 短名称修复 + 备份机制。

V10.2 Bug：difflib("红杉", "红杉资本") = 0.667 < 0.80，导致同一机构重复注册。
V10.3 修复：
  1. 名称标准化（去掉常见后缀/地区词）再比较，"红杉" vs "红杉资本" → 核心均为"红杉" → 相似度 1.0
  2. 内置常见 VC 简称词典（别名预热）
  3. 每次写入前滚动备份（.bak1/.bak2/.bak3）

持久化：{MEMORY_ROOT}/institutions.json
每条记录：{id, canonical_name, aliases, created_at, session_count}
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 匹配阈值 ────────────────────────────────────────────────────────────────
_DIRECT_THRESHOLD = 0.80     # 原始名称直接比较
_CORE_THRESHOLD   = 0.75     # 去后缀后的核心名比较（更宽松，因噪音已去除）
_REGISTRY_FILENAME = "institutions.json"
_BACKUP_COUNT = 3            # 保留最近 N 个备份

# ── 去噪词表 ─────────────────────────────────────────────────────────────────
# 从机构名中剥离的常见后缀（从长到短，避免先去短后缀导致长后缀匹配失败）
_STRIP_SUFFIXES: tuple[str, ...] = (
    "有限责任公司", "有限公司",
    "资本管理", "创业投资", "股权投资", "风险投资",
    "私募基金", "投资基金",
    "资产管理", "财富管理",
    "资本", "基金", "投资", "创投", "集团",
    "partners", "capital", "ventures", "fund", "investment",
    "管理", "控股",
)
# 地区/修饰词（去掉后再比核心名）
_STRIP_REGIONS: tuple[str, ...] = (
    "中国", "亚太", "亚洲", "全球", "国际",
    "china", "asia", "global",
)

# ── 内置常见 VC 简称词典（预热别名，防止首次输入简称新建重复记录） ──────────
# 格式：canonical_name → [别名列表]
# 只在注册表**为空**时使用，不强制覆盖已有记录
_KNOWN_ALIASES: dict[str, list[str]] = {
    "红杉资本中国": ["红杉", "红杉中国", "红杉资本", "Sequoia China"],
    "高瓴资本": ["高瓴", "Hillhouse"],
    "IDG资本": ["IDG"],
    "经纬中国": ["经纬", "Matrix China"],
    "真格基金": ["真格"],
    "源码资本": ["源码"],
    "光速中国": ["光速", "Lightspeed China"],
    "顺为资本": ["顺为", "Shunwei"],
    "峰瑞资本": ["峰瑞", "FREES"],
    "云启资本": ["云启"],
    "高榕资本": ["高榕"],
    "愉悦资本": ["愉悦"],
    "险峰长青": ["险峰"],
    "蓝驰创投": ["蓝驰", "BlueRun"],
    "北极光创投": ["北极光"],
    "启明创投": ["启明", "Qiming"],
    "GGV纪源资本": ["GGV", "纪源"],
    "五源资本": ["五源", "Matrix Partners"],
    "华兴资本": ["华兴"],
    "中金公司": ["中金", "CICC"],
    "鼎晖投资": ["鼎晖"],
    "弘毅投资": ["弘毅"],
    "CPE源峰": ["CPE"],
    "春华资本": ["春华"],
    "深创投": ["深创"],
}


# ── 路径与文件操作 ────────────────────────────────────────────────────────────

def _get_registry_path() -> Path:
    try:
        from runtime_paths import get_memory_root
        return Path(get_memory_root()) / _REGISTRY_FILENAME
    except Exception:
        return Path(".") / _REGISTRY_FILENAME


def _load_registry(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("institution_registry: 读取失败（%s），尝试恢复备份", exc)
        return _try_load_backup(path)


def _try_load_backup(path: Path) -> list[dict]:
    """主文件损坏时尝试恢复最近的备份。"""
    for i in range(1, _BACKUP_COUNT + 1):
        bak = path.with_suffix(f".bak{i}")
        if bak.exists():
            try:
                data = json.loads(bak.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    logger.info("institution_registry: 从备份 %s 恢复成功", bak.name)
                    return data
            except Exception:
                continue
    return []


def _rotate_backups(path: Path) -> None:
    """滚动备份：.bak1 → .bak2 → .bak3（最旧的丢弃）。"""
    for i in range(_BACKUP_COUNT, 1, -1):
        src = path.with_suffix(f".bak{i-1}")
        dst = path.with_suffix(f".bak{i}")
        if src.exists():
            try:
                import shutil
                shutil.copy2(src, dst)
            except OSError:
                pass
    # 当前主文件 → .bak1
    if path.exists():
        try:
            import shutil
            shutil.copy2(path, path.with_suffix(".bak1"))
        except OSError:
            pass


def _save_registry(path: Path, records: list[dict]) -> bool:
    """原子写入 + 写前滚动备份。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_backups(path)            # ← P0.2 备份
        serialized = json.dumps(records, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(serialized)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception as exc:
        logger.warning("institution_registry: 写入失败（%s）", exc)
        return False


# ── P0.1 名称标准化与增强匹配 ────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """
    剥离常见后缀和地区词，提取机构核心名称。

    "红杉资本中国" → "红杉"
    "高瓴资本"     → "高瓴"
    "IDG资本"      → "IDG"
    """
    n = name.strip().lower()
    # 先去地区词（防止"中国"被当核心留下）
    for region in _STRIP_REGIONS:
        n = n.replace(region.lower(), "")
    # 再去后缀（从长到短）
    for suffix in _STRIP_SUFFIXES:
        if n.endswith(suffix.lower()) and len(n) > len(suffix):
            n = n[: -len(suffix)]
            break  # 只去一层，避免过度裁剪
    return n.strip()


def _similarity(a: str, b: str) -> float:
    """原始名称序列相似度。"""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _enhanced_similarity(name: str, candidate: str) -> float:
    """
    增强相似度：取「直接比较」与「去后缀核心名比较」的最大值。

    "红杉" vs "红杉资本":
      直接: 0.667 → 不够
      核心: "红杉" vs "红杉" = 1.0 → 命中 ✓

    "高盛" vs "高瓴资本":
      直接: 0.444
      核心: "高盛" vs "高瓴" = 0.5 → 不命中 ✓
    """
    direct = _similarity(name, candidate)
    core_n = _normalize_name(name)
    core_c = _normalize_name(candidate)
    # 只有核心名均非空且不退化为单字时才参考核心相似度
    if core_n and core_c and len(core_n) >= 2 and len(core_c) >= 2:
        core_score = SequenceMatcher(None, core_n, core_c).ratio()
        return max(direct, core_score)
    return direct


def _best_match(name: str, records: list[dict]) -> tuple[Optional[dict], float]:
    """返回增强相似度最高的机构记录及得分。"""
    best_record: Optional[dict] = None
    best_score = 0.0
    for rec in records:
        candidates = [rec["canonical_name"]] + rec.get("aliases", [])
        for cand in candidates:
            score = _enhanced_similarity(name, cand)
            if score > best_score:
                best_score = score
                best_record = rec
    return best_record, best_score


def _effective_threshold(name: str) -> float:
    """
    根据名称长度动态调整阈值：
    - 核心名 ≥ 3 字：用标准阈值 _DIRECT_THRESHOLD
    - 核心名 1-2 字：用更宽松的 _CORE_THRESHOLD（短名称直接相似度天花板低）
    """
    core = _normalize_name(name)
    return _DIRECT_THRESHOLD if len(core) >= 3 else _CORE_THRESHOLD


# ── 内置词典预热 ──────────────────────────────────────────────────────────────

def _seed_known_aliases(path: Path, records: list[dict]) -> list[dict]:
    """
    仅当注册表为空时，预热内置 VC 词典。
    已有数据时不操作，避免覆盖用户自定义。
    """
    if records:
        return records
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for canonical, aliases in _KNOWN_ALIASES.items():
        records.append({
            "id": str(uuid.uuid4()),
            "canonical_name": canonical,
            "aliases": aliases,
            "created_at": now,
            "session_count": 0,
        })
    _save_registry(path, records)
    logger.info("institution_registry: 内置 VC 词典已预热（%d 条）", len(records))
    return records


# ── 公开 API ──────────────────────────────────────────────────────────────────

def fuzzy_match(name: str) -> Optional[dict]:
    """
    输入任意机构写法，返回匹配的已知机构，否则返回 None。
    使用增强相似度（去后缀 + 动态阈值）。
    """
    if not name or not name.strip():
        return None
    path = _get_registry_path()
    records = _load_registry(path)
    rec, score = _best_match(name.strip(), records)
    threshold = _effective_threshold(name.strip())
    if rec and score >= threshold:
        return {**rec, "similarity": round(score, 3)}
    return None


def register(canonical_name: str, alias: Optional[str] = None) -> dict:
    """注册新机构，或为已有机构添加别名（使用增强匹配）。"""
    canonical_name = (canonical_name or "").strip()
    if not canonical_name:
        raise ValueError("canonical_name 不能为空")

    path = _get_registry_path()
    records = _seed_known_aliases(path, _load_registry(path))

    # 精确匹配 canonical
    for rec in records:
        if rec["canonical_name"].lower() == canonical_name.lower():
            if alias and alias.strip() and alias.strip() not in rec.get("aliases", []):
                rec.setdefault("aliases", []).append(alias.strip())
                _save_registry(path, records)
            return rec

    # 增强模糊匹配
    threshold = _effective_threshold(canonical_name)
    best, score = _best_match(canonical_name, records)
    if best and score >= threshold:
        if canonical_name not in [best["canonical_name"]] + best.get("aliases", []):
            best.setdefault("aliases", []).append(canonical_name)
        if alias and alias.strip() and alias.strip() not in best.get("aliases", []):
            best["aliases"].append(alias.strip())
        _save_registry(path, records)
        return best

    # 新建
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    new_rec = {
        "id": str(uuid.uuid4()),
        "canonical_name": canonical_name,
        "aliases": [alias.strip()] if alias and alias.strip() else [],
        "created_at": now,
        "session_count": 0,
    }
    records.append(new_rec)
    _save_registry(path, records)
    logger.info("institution_registry: 新建机构「%s」（id=%s）", canonical_name, new_rec["id"])
    return new_rec


def resolve(name: str) -> tuple[str, str]:
    """统一入口：输入任意名称 → (institution_id, canonical_name)。"""
    name = (name or "").strip()
    if not name:
        return "", ""

    path = _get_registry_path()
    records = _seed_known_aliases(path, _load_registry(path))
    threshold = _effective_threshold(name)
    best, score = _best_match(name, records)

    if best and score >= threshold:
        if name not in [best["canonical_name"]] + best.get("aliases", []):
            best.setdefault("aliases", []).append(name)
            _save_registry(path, records)
        return best["id"], best["canonical_name"]

    rec = register(name)
    return rec["id"], rec["canonical_name"]


def increment_session_count(institution_id: str) -> None:
    """锁定时调用，为机构会话计数 +1。"""
    path = _get_registry_path()
    records = _load_registry(path)
    for rec in records:
        if rec["id"] == institution_id:
            rec["session_count"] = rec.get("session_count", 0) + 1
            _save_registry(path, records)
            return


def get_all() -> list[dict]:
    """返回全部机构记录（包含预热词典）。"""
    path = _get_registry_path()
    records = _seed_known_aliases(path, _load_registry(path))
    return list(records)


def get_by_id(institution_id: str) -> Optional[dict]:
    """按 id 精确查找。"""
    for rec in _load_registry(_get_registry_path()):
        if rec["id"] == institution_id:
            return rec
    return None


def list_backup_status() -> dict:
    """返回备份状态，供 Dashboard 显示。"""
    path = _get_registry_path()
    result = {"main_exists": path.exists(), "backups": []}
    for i in range(1, _BACKUP_COUNT + 1):
        bak = path.with_suffix(f".bak{i}")
        result["backups"].append({
            "name": bak.name,
            "exists": bak.exists(),
            "size": bak.stat().st_size if bak.exists() else 0,
        })
    return result
