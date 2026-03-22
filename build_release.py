"""
一键生成「纯净交付」文件夹：仅拷贝白名单资源，排除 .env / 测试 / 缓存等。
运行：在项目根目录执行  python build_release.py

编码策略（跨中文 Windows / CMD / Python）：
- 交付目录中的「一键启动系统.bat」一律以 utf-8-sig（带 BOM）写入，便于 CMD 识别 UTF-8；
- bat 内设置 chcp 65001 + PYTHONUTF8 + PYTHONIOENCODING，避免系统 ANSI 干扰 Python；
- requirements.txt 经规范化后以 UTF-8（无 BOM）写入，符合 pip 惯例且避免隐形乱码。
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

# Windows 控制台 UTF-8（构建机）
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
OUT_NAME = "AI路演教练_纯净交付版"
OUT = ROOT / OUT_NAME

WHITELIST_DIRS = ["src"]
WHITELIST_FILES = [
    "app.py",
    "requirements.txt",
    "一键启动系统.bat",
    "写给同事的使用说明书.txt",
]

# 交付包内 .bat 由脚本生成（不依赖源文件编码）；utf-8-sig + 环境变量 = 国内 Windows CMD 友好
_BAT_RELEASE_LINES = [
    "@echo off",
    "chcp 65001 >nul",
    "set PYTHONUTF8=1",
    "set PYTHONIOENCODING=utf-8",
    'cd /d "%~dp0"',
    "echo ========================================",
    "echo   AI 路演教练 — 依赖安装与启动",
    "echo ========================================",
    "echo.",
    "echo [1/2] 正在安装/更新依赖（requirements.txt）...",
    "python -m pip install -r requirements.txt -q",
    "if errorlevel 1 (",
    "    echo 依赖安装失败，请检查 Python 与网络。",
    "    pause",
    "    exit /b 1",
    ")",
    "echo [2/2] 正在启动 Streamlit 控制台...",
    "echo 浏览器将自动打开；若未打开请访问终端提示的 Local URL。",
    "echo.",
    "python -m streamlit run app.py",
    "if errorlevel 1 pause",
]


def _ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _read_text_flexible(path: Path) -> str:
    """优先按 UTF-8（含 BOM）读取，避免源文件编码漂移。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _normalize_requirements_text(text: str) -> str:
    """去掉 BOM/杂字符，统一换行，末尾单一换行，杜绝不可见乱码行。"""
    t = text.lstrip("\ufeff")
    lines: list[str] = []
    for line in t.splitlines():
        line = line.rstrip("\r\n\t ")
        if not line:
            lines.append("")
            continue
        # 去掉零宽字符等（常见于错误复制）
        line = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", line)
        lines.append(line)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def _write_text_crlf_utf8_sig(path: Path, content: str) -> None:
    """UTF-8 带 BOM + CRLF：与 Windows 记事本/ CMD 展示中文最稳。"""
    body = content.replace("\r\n", "\n").replace("\r", "\n")
    crlf = "\r\n".join(body.split("\n"))
    path.write_bytes(crlf.encode("utf-8-sig"))


def _write_text_crlf_utf8(path: Path, content: str) -> None:
    """UTF-8 无 BOM + CRLF：requirements / 部分工具链期望无 BOM。"""
    body = content.replace("\r\n", "\n").replace("\r", "\n")
    crlf = "\r\n".join(body.split("\n"))
    path.write_bytes(crlf.encode("utf-8"))


def _write_release_bat(dest: Path) -> None:
    content = "\n".join(_BAT_RELEASE_LINES) + "\n"
    _write_text_crlf_utf8_sig(dest, content)


def _copy_whitelist_file(src_name: str, dest_dir: Path) -> None:
    src = ROOT / src_name
    dst = dest_dir / src_name

    if src_name == "一键启动系统.bat":
        _write_release_bat(dst)
        return

    if src_name == "requirements.txt":
        text = _normalize_requirements_text(_read_text_flexible(src))
        _write_text_crlf_utf8(dst, text)
        return

    if src_name == "写给同事的使用说明书.txt":
        text = _read_text_flexible(src)
        text = text.lstrip("\ufeff")
        text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
        if not text.endswith("\n"):
            text += "\n"
        _write_text_crlf_utf8_sig(dst, text)
        return

    # app.py 等：UTF-8 无 BOM 写出，换行 CRLF 减少跨编辑器差异
    text = _read_text_flexible(src)
    _write_text_crlf_utf8(dst, text)


def main() -> int:
    missing: list[str] = []
    for name in WHITELIST_FILES:
        if not (ROOT / name).is_file():
            missing.append(name)
    for d in WHITELIST_DIRS:
        if not (ROOT / d).is_dir():
            missing.append(f"{d}/")
    if missing:
        print("错误：以下白名单项不存在，请补齐后再打包：")
        for m in missing:
            print(f"  - {m}")
        return 1

    _ensure_clean_dir(OUT)

    for dname in WHITELIST_DIRS:
        src = ROOT / dname
        dst = OUT / dname
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                ".mypy_cache",
                ".pytest_cache",
            ),
        )

    for fname in WHITELIST_FILES:
        _copy_whitelist_file(fname, OUT)

    env_out = OUT / ".env"
    env_out.write_bytes(b"")

    print()
    print("\033[1m\033[92m🎉 打包成功！请直接将【AI路演教练_纯净交付版】文件夹拷贝进 U 盘，其余文件无需理会！\033[0m")
    print(f"输出目录：{OUT}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
