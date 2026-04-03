"""
高管错题本（Executive Memory）— V8.6 纯 JSON 落盘引擎 + 静默收割（防噪门）。

按 company_id 分子目录、tag 分文件：{store_dir}/{safe_company}/{safe_tag}.json
原子写入；损坏 JSON 降级；单条校验失败跳过；兼容 Task1 扁平 `_default` 遗留文件。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from runtime_paths import get_writable_app_root
from schema import ExecutiveMemory

logger = logging.getLogger(__name__)

EXECUTIVE_MEMORY_SUBDIR = ".executive_memory"
_STORE_VERSION = 1

_WIN_INVALID = '\\/:*?"<>|\n\r\t'

# 防噪门：相对编辑距离 > 10% 或 绝对字数差 > 10 才进入 LLM 提炼
_MEMORY_NOISE_RATIO_THRESHOLD = 0.10
_MEMORY_NOISE_LEN_DIFF_THRESHOLD = 10


def _iso_now_utc_z() -> str:
    """UTC ISO8601，Z 后缀，便于日志与跨区一致。"""
    return (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def default_store_dir() -> Path:
    """默认可写根下的 `.executive_memory` 目录。"""
    return get_writable_app_root() / EXECUTIVE_MEMORY_SUBDIR


def _safe_fs_segment(name: str) -> str:
    t = (name or "").strip()
    if not t:
        return "_default"
    s = "".join("_" if c in _WIN_INVALID else c for c in t)
    s = s.strip()[:200]
    return s if s else "_default"


def normalized_company_id(company_id: str) -> str:
    t = (company_id or "").strip()
    return t if t else "_default"


def get_company_memory_dir(company_id: str, store_dir: Path) -> Path:
    return Path(store_dir) / _safe_fs_segment(normalized_company_id(company_id))


def get_memory_store_file(company_id: str, tag: str, store_dir: Path) -> Path:
    """返回该 (company, tag) 对应的 JSON 文件路径。"""
    return get_company_memory_dir(company_id, store_dir) / f"{_safe_fs_segment(tag)}.json"


def _legacy_flat_file(tag: str, store_dir: Path) -> Path:
    """Task1 遗留：文件直接在 store_dir 根下。"""
    return Path(store_dir) / f"{_safe_fs_segment(tag)}.json"


def _levenshtein(a: str, b: str) -> int:
    """经典动态规划；空串安全。"""
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            ins, delete, sub = cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def memory_diff_noise_gate_passes(original: str, refined: str) -> bool:
    """
    防噪门：避免「改个错别字」也进错题本。
    满足任一即通过：Levenshtein 相对距离 > 10%；或 |Δ字数| > 10。
    完全相同文本不通过。
    """
    o = original or ""
    r = refined or ""
    if o == r:
        return False
    lo, lr = len(o), len(r)
    if abs(lo - lr) > _MEMORY_NOISE_LEN_DIFF_THRESHOLD:
        return True
    mx = max(lo, lr, 1)
    dist = _levenshtein(o, r)
    return (dist / mx) > _MEMORY_NOISE_RATIO_THRESHOLD


def load_executive_memories(
    company_id: str,
    tag: str,
    *,
    store_dir: Path | None = None,
) -> list[ExecutiveMemory]:
    """
    读取某公司某 tag 桶内全部记忆；文件不存在、JSON 损坏或结构不符时返回 []。
    """
    d = Path(store_dir) if store_dir is not None else default_store_dir()
    cid = normalized_company_id(company_id)
    path = get_memory_store_file(cid, tag, d)
    if not path.is_file() and cid in ("_default",):
        leg = _legacy_flat_file(tag, d)
        if leg.is_file():
            path = leg
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return []
    out: list[ExecutiveMemory] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(ExecutiveMemory.model_validate(raw))
        except ValidationError:
            continue
    return out


def save_executive_memories(
    company_id: str,
    tag: str,
    memories: list[ExecutiveMemory],
    *,
    store_dir: Path | None = None,
) -> None:
    """覆写保存某公司某 tag 桶（原子写入）。"""
    d = Path(store_dir) if store_dir is not None else default_store_dir()
    cid = normalized_company_id(company_id)
    company_dir = get_company_memory_dir(cid, d)
    company_dir.mkdir(parents=True, exist_ok=True)
    path = get_memory_store_file(cid, tag, d)
    payload = {
        "version": _STORE_VERSION,
        "items": [m.model_dump(mode="json") for m in memories],
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)

    fd, tmp_path = tempfile.mkstemp(dir=company_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def append_executive_memory(
    company_id: str,
    tag: str,
    item: ExecutiveMemory,
    *,
    store_dir: Path | None = None,
) -> None:
    items = load_executive_memories(company_id, tag, store_dir=store_dir)
    items.append(item)
    save_executive_memories(company_id, tag, items, store_dir=store_dir)


def list_executive_memory_tags(company_id: str, *, store_dir: Path | None = None) -> list[str]:
    """列出某公司目录下所有 tag 文件名（安全化 stem）。"""
    d = Path(store_dir) if store_dir is not None else default_store_dir()
    company_dir = get_company_memory_dir(company_id, d)
    if not company_dir.is_dir():
        return []
    return sorted({p.stem for p in company_dir.glob("*.json") if p.is_file()})


def load_top_executive_memories_for_prompt(
    company_id: str,
    tag: str,
    *,
    limit: int = 5,
    store_dir: Path | None = None,
) -> list[ExecutiveMemory]:
    """按 weight 降序取 Top N，供 Prompt 注入（防 Token 爆炸）。"""
    items = load_executive_memories(company_id, tag, store_dir=store_dir)
    items.sort(key=lambda m: m.weight, reverse=True)
    return items[: max(0, limit)]


def list_all_executive_memories_for_company(
    company_id: str,
    *,
    store_dir: Path | None = None,
) -> list[tuple[str, ExecutiveMemory]]:
    """(桶文件名 stem, 记忆条目) 扁平列表，供看板展示。"""
    out: list[tuple[str, ExecutiveMemory]] = []
    for stem_tag in list_executive_memory_tags(company_id, store_dir=store_dir):
        for m in load_executive_memories(company_id, stem_tag, store_dir=store_dir):
            out.append((stem_tag, m))
    return out


def delete_executive_memory_by_uuid(
    company_id: str,
    memory_uuid: str,
    *,
    store_dir: Path | None = None,
) -> bool:
    """在公司全部 tag 桶中删除指定 uuid；命中返回 True。"""
    uid = (memory_uuid or "").strip()
    if not uid:
        return False
    changed = False
    for stem_tag in list_executive_memory_tags(company_id, store_dir=store_dir):
        items = load_executive_memories(company_id, stem_tag, store_dir=store_dir)
        new_items = [m for m in items if m.uuid != uid]
        if len(new_items) != len(items):
            save_executive_memories(company_id, stem_tag, new_items, store_dir=store_dir)
            changed = True
    return changed


def update_executive_memory_weight(
    company_id: str,
    memory_uuid: str,
    weight: float,
    *,
    store_dir: Path | None = None,
) -> bool:
    """更新指定 uuid 的 weight（需 >=0）；命中返回 True。"""
    uid = (memory_uuid or "").strip()
    if not uid or weight < 0:
        return False
    changed = False
    for stem_tag in list_executive_memory_tags(company_id, store_dir=store_dir):
        items = load_executive_memories(company_id, stem_tag, store_dir=store_dir)
        new_items: list[ExecutiveMemory] = []
        hit = False
        for m in items:
            if m.uuid == uid:
                new_items.append(m.model_copy(update={"weight": float(weight)}))
                hit = True
            else:
                new_items.append(m)
        if hit:
            save_executive_memories(company_id, stem_tag, new_items, store_dir=store_dir)
            changed = True
    return changed


def capture_and_distill_diff(
    original: str,
    refined: str,
    company_id: str,
    tag: str,
    *,
    risk_type: str = "",
    store_dir: Path | None = None,
) -> ExecutiveMemory | None:
    """
    静默收割：防噪门未通过则返回 None；通过则调用 LLM 提炼并追加落盘。
    API/密钥缺失或提炼失败时记录日志并返回 None，不抛异常（不拖垮主流程）。
    """
    cid = (company_id or "").strip()
    tg = (tag or "").strip() or "default"
    if not cid:
        return None
    if not memory_diff_noise_gate_passes(original, refined):
        return None
    try:
        from llm_judge import distill_executive_memory_from_diff

        mem = distill_executive_memory_from_diff(original, refined, tg)
        rt = (risk_type or "").strip()
        if rt not in ("严重", "一般", "轻微"):
            rt = rt[:20] if rt else ""
        mem = mem.model_copy(
            update={
                "tag": tg,
                "risk_type": rt,
                "updated_at": _iso_now_utc_z(),
                "hit_count": 0,
            }
        )
        append_executive_memory(cid, tg, mem, store_dir=store_dir)
        return mem
    except Exception:
        logger.exception("V8.6 capture_and_distill_diff 失败（已静默跳过）")
        return None


def count_executive_memories_for_company(company_id: str, *, store_dir: Path | None = None) -> int:
    return len(list_all_executive_memories_for_company(company_id, store_dir=store_dir))


def top_risk_type_counts_for_company(
    company_id: str,
    *,
    limit: int = 3,
    store_dir: Path | None = None,
) -> list[tuple[str, int]]:
    """
    按 risk_type 聚合条数，降序取 Top N；空类型计入「未标注」。
    供看板「高频雷区」与 pills / 进度条展示。
    """
    pairs = list_all_executive_memories_for_company(company_id, store_dir=store_dir)
    c: Counter[str] = Counter()
    for _, m in pairs:
        rt = (m.risk_type or "").strip()
        c[rt if rt else "未标注"] += 1
    return c.most_common(max(0, limit))


def record_executive_memory_prompt_hits(
    company_id: str,
    tag: str,
    used: list[ExecutiveMemory],
    *,
    store_dir: Path | None = None,
) -> None:
    """
    主评 Prompt 注入后：对本次选用的记忆条 hit_count+1 并刷新 updated_at（同 tag 桶内原地写回）。
    """
    if not used:
        return
    uid_hit = {m.uuid for m in used if getattr(m, "uuid", None)}
    if not uid_hit:
        return
    items = load_executive_memories(company_id, tag, store_dir=store_dir)
    if not items:
        return
    now = _iso_now_utc_z()
    new_items: list[ExecutiveMemory] = []
    changed = False
    for m in items:
        if m.uuid in uid_hit:
            new_items.append(
                m.model_copy(
                    update={
                        "hit_count": int(m.hit_count) + 1,
                        "updated_at": now,
                    }
                )
            )
            changed = True
        else:
            new_items.append(m)
    if changed:
        save_executive_memories(company_id, tag, new_items, store_dir=store_dir)


def get_company_dashboard_stats(
    company_id: str,
    *,
    store_dir: Path | None = None,
) -> dict[str, Any]:
    """
    V9.0 机构画像：仅按 **当前 company_id** 聚合 `.executive_memory` 下该公司目录，绝不跨公司。

    返回结构稳定，无数据时为零值/空列表，供 UI 与 Plotly 安全消费。
    """
    empty: dict[str, Any] = {
        "total_memories": 0,
        "active_executives": 0,
        "risk_distribution": {},
        "executive_hit_trends": {
            "by_executive": [],
            "daily_activity": [],
        },
        "total_hit_count": 0,
        "last_updated_at": "",
    }
    cid = (company_id or "").strip()
    if not cid or cid == "__new__":
        return empty

    try:
        pairs = list_all_executive_memories_for_company(cid, store_dir=store_dir)
    except Exception:
        logger.exception("get_company_dashboard_stats 读取失败，返回空结构")
        return empty

    if not pairs:
        return empty

    total = len(pairs)
    distinct_tags = {(m.tag or "").strip() for _, m in pairs if (m.tag or "").strip()}
    active_executives = len(distinct_tags)

    risk_dist: Counter[str] = Counter()
    for _, m in pairs:
        rt = (m.risk_type or "").strip()
        risk_dist[rt if rt else "未标注"] += 1

    by_tag_hits: dict[str, int] = defaultdict(int)
    by_tag_count: Counter[str] = Counter()
    for _, m in pairs:
        t = (m.tag or "").strip() or "未标注"
        by_tag_hits[t] += int(m.hit_count)
        by_tag_count[t] += 1
    by_exec = [
        {"tag": t, "total_hits": by_tag_hits[t], "memory_count": by_tag_count[t]}
        for t in sorted(by_tag_hits.keys(), key=lambda x: (-by_tag_hits[x], x))
    ]

    daily: Counter[str] = Counter()
    for _, m in pairs:
        u = (m.updated_at or "").strip()
        if len(u) >= 10 and u[4] == "-" and u[7] == "-":
            daily[u[:10]] += 1
    daily_activity = [{"date": d, "count": daily[d]} for d in sorted(daily.keys())]

    total_hits = sum(int(m.hit_count) for _, m in pairs)
    timestamps = [(m.updated_at or "").strip() for _, m in pairs if (m.updated_at or "").strip()]
    last_u = max(timestamps) if timestamps else ""

    return {
        "total_memories": total,
        "active_executives": active_executives,
        "risk_distribution": dict(risk_dist),
        "executive_hit_trends": {
            "by_executive": by_exec,
            "daily_activity": daily_activity,
        },
        "total_hit_count": total_hits,
        "last_updated_at": last_u,
    }
