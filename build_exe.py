#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键打包：清理旧构建 + PyInstaller（含 plotly 全量收集，修复 _validators.json 缺失）。
仓库发版 V7.5（与 build_release.py → CURRENT_VERSION 对齐；首选交付仍为 BAT 纯净包）。
在项目根目录执行: python build_exe.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent


def clean_build_artifacts() -> None:
    """删除 build/、dist/ 及常见 spec，避免旧缓存污染。"""
    for name in ("build", "dist"):
        p = ROOT / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    for spec in (
        ROOT / "run_exe.spec",
        ROOT / "AI路演复盘教练.spec",
    ):
        try:
            spec.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> None:
    os.chdir(str(ROOT))

    if not (ROOT / "run_exe.py").is_file():
        print("错误：未找到 run_exe.py，请在项目根目录运行本脚本。", file=sys.stderr)
        sys.exit(1)

    clean_build_artifacts()

    print("[build] 启动 PyInstaller 打包...")
    PyInstaller.__main__.run(
        [
            "run_exe.py",
            "--name=AI路演复盘教练",
            "--onedir",
            "--windowed",
            "--noconfirm",
            "--clean",
            "--add-data=app.py;.",
            "--add-data=src;src",
            "--collect-all=streamlit",
            "--collect-all=pyarrow",
            "--collect-all=altair",
            "--collect-all=plotly",
            "--hidden-import=document_reader",
            "--hidden-import=llm_judge",
            "--hidden-import=report_builder",
            "--hidden-import=transcriber",
            "--hidden-import=schema",
            "--hidden-import=runtime_paths",
            "--hidden-import=job_pipeline",
            "--hidden-import=sensitive_words",
            "--hidden-import=audio_filename_hints",
            "--hidden-import=audio_preprocess",
        ]
    )
    print("[build] 打包完成，请查看 dist/ 目录。")


if __name__ == "__main__":
    main()
