"""
历史数据迁移脚本 — V10.3 P1.1

功能：扫描工作区所有 *_analytics.json，对缺少 institution_id 的文件，
      从 recording_label 逆推机构名，通过 institution_registry.resolve()
      补写 institution_id + institution_canonical。

用法：
  python scripts/migrate_institution_ids.py [workspace_root]

  不传参数时使用默认工作区根目录（runtime_paths.get_memory_root() 的父目录）。

设计原则：
- 已有 institution_id 的文件跳过，幂等安全
- 推断失败（无短横线、名称过短）时跳过并记录警告
- 原子写入防止损坏
- 返回 (total, migrated, skipped) 统计三元组
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

# 确保 src/ 在 path 中，无论从哪里调用
_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import institution_registry as ir

logger = logging.getLogger(__name__)

# 机构名称最少字符数（少于此长度视为无效，避免单字误匹配）
_MIN_INSTITUTION_LENGTH = 2


def _extract_institution_hint(recording_label: str) -> Optional[str]:
    """
    从 recording_label 推断机构名称。

    规则：机构名在第一个「-」之前，且该「-」必须出现在第一个「_」之前。
    - "迪策资本-李志新_前1-5测试_analysis_report" → "迪策资本"  (- 在 _ 之前)
    - "红杉资本中国-张总-20240101"               → "红杉资本中国"
    - "李志新_前1-5测试_analysis_report"         → None  (- 在 _ 之后，非机构前缀)
    - "A-李志新_report"                          → None  (候选太短)
    """
    if not recording_label or "-" not in recording_label:
        return None

    first_dash = recording_label.index("-")
    first_underscore = recording_label.find("_")

    # 如果 _ 出现在 - 之前，说明这个 - 不是机构-姓名分隔符
    if first_underscore != -1 and first_underscore < first_dash:
        return None

    candidate = recording_label[:first_dash].strip()
    if len(candidate) < _MIN_INSTITUTION_LENGTH:
        return None
    return candidate


def migrate_file(analytics_path: Path) -> bool:
    """
    迁移单个 analytics JSON 文件。

    返回：
      True  — 成功补写 institution_id
      False — 跳过（已有 id / 无法推断 / JSON 损坏）
    """
    try:
        text = analytics_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception as exc:
        logger.warning("migrate: 读取失败 %s（%s）", analytics_path.name, exc)
        return False

    # 已有合法 institution_id → 跳过
    if data.get("institution_id", "").strip():
        return False

    # 从 recording_label 推断机构名
    recording_label = data.get("recording_label", "")
    hint = _extract_institution_hint(recording_label)
    if not hint:
        logger.info(
            "migrate: 跳过 %s（无法从 recording_label 推断机构名）",
            analytics_path.name,
        )
        return False

    # 解析机构
    try:
        institution_id, canonical = ir.resolve(hint)
    except Exception as exc:
        logger.warning(
            "migrate: institution_registry.resolve 失败 %s（%s）",
            analytics_path.name, exc,
        )
        return False

    if not institution_id:
        return False

    # 补写字段（保留所有原有字段）
    data["institution_id"] = institution_id
    data["institution_canonical"] = canonical

    # 原子写入
    try:
        fd, tmp = tempfile.mkstemp(
            dir=analytics_path.parent, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=2))
            os.replace(tmp, analytics_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as exc:
        logger.warning("migrate: 写入失败 %s（%s）", analytics_path.name, exc)
        return False

    logger.info(
        "migrate: ✓ %s → institution=%s（%s）",
        analytics_path.name, canonical, institution_id,
    )
    return True


def migrate_workspace(workspace_root: Path) -> tuple[int, int, int]:
    """
    批量扫描 workspace_root 下所有 *_analytics.json 并迁移。

    返回：(total, migrated, skipped)
    """
    workspace_root = Path(workspace_root)
    analytics_files = list(workspace_root.rglob("*_analytics.json"))

    total = len(analytics_files)
    migrated = 0
    skipped = 0

    for f in analytics_files:
        if migrate_file(f):
            migrated += 1
        else:
            skipped += 1

    logger.info(
        "migrate: 扫描完成 — 共 %d 个文件，迁移 %d，跳过 %d",
        total, migrated, skipped,
    )
    return total, migrated, skipped


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    else:
        try:
            sys.path.insert(0, str(_SRC_DIR))
            from runtime_paths import get_writable_app_root
            root = Path(get_writable_app_root()).parent
        except Exception:
            root = Path(".")

    if not root.exists():
        print(f"错误：目录不存在 — {root}")
        sys.exit(1)

    print(f"扫描工作区：{root}")
    total, migrated, skipped = migrate_workspace(root)
    print(f"\n完成：共 {total} 个文件，迁移 {migrated}，跳过 {skipped}")


if __name__ == "__main__":
    main()
