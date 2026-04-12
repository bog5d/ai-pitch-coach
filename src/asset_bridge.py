"""
asset_bridge — FOS 数据桥接模块 (行动项三)

从 .fos_data/asset_index.json 读取仓颉 FSS 生成的资产清单，
供 AI Coach 在会前简报中引用「库中相关文件」。

设计原则：
- 全程纯标准库（json, os, re），不引入新依赖
- 所有失败路径静默降级（返回空列表/空字符串），不影响主流程
- 关键词匹配用命中计数，不调用 LLM（零 API 成本，零延迟）
- FOS_DATA_DIR 环境变量可覆盖默认路径，便于测试重定向
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


def _get_fos_data_dir() -> Path:
    """
    返回 .fos_data 桥接目录。
    优先读取 FOS_DATA_DIR 环境变量（测试可覆盖），
    否则从本文件向上两级 → AI_Workspaces/.fos_data。
    """
    override = os.environ.get("FOS_DATA_DIR", "").strip()
    if override:
        return Path(override)
    # src/asset_bridge.py → src → AI_Pitch_Coach → AI_Workspaces
    return Path(__file__).resolve().parent.parent.parent / ".fos_data"


def load_asset_index(fos_data_dir: Path | str | None = None) -> list[dict]:
    """
    读取 asset_index.json，返回 assets 列表。
    文件不存在、JSON 损坏、字段缺失时静默返回空列表。
    """
    if fos_data_dir is not None:
        data_dir = Path(fos_data_dir)
    else:
        data_dir = _get_fos_data_dir()

    index_path = data_dir / "asset_index.json"
    try:
        text = index_path.read_text(encoding="utf-8")
        data = json.loads(text)
        return data.get("assets") or []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def find_related_assets(
    keyword_text: str,
    assets: list[dict],
    top_n: int = 3,
) -> list[dict]:
    """
    在 assets 的 filename + summary + tags 中做关键词命中计数，
    返回命中数 > 0 的 Top N 资产（按命中数降序）。

    keyword_text: 空格或逗号分隔的关键词字符串
    """
    if not keyword_text or not assets:
        return []

    # 分词：按空格、逗号、中文分隔符拆分，过滤空串
    keywords = [k for k in re.split(r"[\s,，、]+", keyword_text) if k]
    if not keywords:
        return []

    scored: list[tuple[int, dict]] = []
    for asset in assets:
        searchable = " ".join([
            asset.get("filename", ""),
            asset.get("summary", ""),
            " ".join(asset.get("tags", [])),
        ])
        hits = sum(1 for kw in keywords if kw in searchable)
        if hits > 0:
            scored.append((hits, asset))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [asset for _, asset in scored[:top_n]]


def build_asset_section(keywords: list[str], assets: list[dict]) -> str:
    """
    给定关键词列表，查找相关资产并生成 Markdown 段落。
    无匹配时返回空字符串（调用方可直接追加到简报末尾）。
    """
    if not assets:
        return ""

    keyword_text = " ".join(keywords)
    related = find_related_assets(keyword_text, assets, top_n=5)
    if not related:
        return ""

    lines = ["\n\n---\n\n### 📁 库中相关资产"]
    for a in related:
        name = a.get("filename", "")
        path = a.get("relative_path", "") or "根目录"
        updated = a.get("last_modified", "")
        summary = a.get("summary", "")
        line = f"- **{name}**（路径：{path}，更新：{updated}）"
        if summary:
            line += f"\n  > {summary}"
        lines.append(line)

    return "\n".join(lines)
