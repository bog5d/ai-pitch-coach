"""
PyInstaller 专用启动器：冻结环境下进入 _MEIPASS 并拉起 Streamlit。
仓库发版 V7.2（与 build_release.py → CURRENT_VERSION 对齐；EXE 为实验性交付形态）。

注意：
- 切勿使用 --server.headless=true，否则不会自动打开浏览器，用户会误以为程序无响应。
- Windows 下建议在 __main__ 中调用 multiprocessing.freeze_support()，避免子进程异常。
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import traceback
from pathlib import Path


def _write_log(line: str) -> None:
    """EXE 同目录写入日志，便于无控制台时排错。"""
    try:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
        else:
            base = Path(__file__).resolve().parent
        log_path = base / "pitch_coach_launch.log"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except Exception:
        pass


def main() -> None:
    multiprocessing.freeze_support()

    # 冻结时工作目录设为打包资源根（含 app.py、src/）
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            os.chdir(meipass)
        # 减少遥测弹窗干扰
        os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")

    root = Path.cwd()
    app_py = root / "app.py"
    if not app_py.is_file():
        msg = (
            f"[ERROR] 未找到 app.py。cwd={root} "
            f"_MEIPASS={getattr(sys, '_MEIPASS', None)}"
        )
        _write_log(msg)
        print(msg, file=sys.stderr)
        sys.exit(1)

    try:
        import streamlit.web.cli as stcli
    except Exception as e:
        _write_log(f"import streamlit 失败: {e!s}\n{traceback.format_exc()}")
        raise

    # 必须为 false，否则不自动打开浏览器 → 用户以为双击无反应
    sys.argv = [
        "streamlit",
        "run",
        str(app_py.resolve()),
        "--global.developmentMode=false",
        "--server.headless=false",
    ]

    try:
        code = stcli.main()
        sys.exit(code if code is not None else 0)
    except SystemExit as e:
        c = e.code
        if c not in (0, None):
            _write_log(f"streamlit 退出: {c!r}")
        raise
    except Exception as e:
        _write_log(f"streamlit 异常: {e!s}\n{traceback.format_exc()}")
        raise


if __name__ == "__main__":
    main()
