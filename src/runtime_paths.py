# 依赖：仅标准库 — PyInstaller / 源码「两栖」路径根
"""
项目内统一路径解析：
- get_project_root：打包资源根（sys._MEIPASS 或项目根），用于定位 src、tests 等内置路径；
- get_writable_app_root：可写根（EXE 旁或项目根），用于 .env、默认归档目录、output 等；
- get_resource_path：相对项目根的拼接（供非 app 模块使用，与 app.py 顶部函数语义一致）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def get_project_root() -> Path:
    """应用包根目录：冻结时为 _MEIPASS；源码运行时为仓库根目录。"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    # 本文件位于 src/runtime_paths.py → 上级为项目根
    return Path(__file__).resolve().parent.parent


def get_writable_app_root() -> Path:
    """
    可写根目录：EXE 所在目录（冻结）或项目根（源码）。
    用于 .env、默认「数据归档根目录」、CLI 输出等，避免写入 _MEIPASS 临时目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return get_project_root()


def get_resource_path(relative_path: str) -> str:
    """相对项目根（get_project_root）的资源绝对路径字符串。"""
    rel = (relative_path or ".").replace("/", os.sep).strip()
    root = get_project_root()
    if not rel or rel == ".":
        return str(root)
    return str(root / rel)
