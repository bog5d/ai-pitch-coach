"""
一键生成「纯净交付」文件夹 + 交付级 ZIP（Green-Box Release）。
运行：在项目根目录执行  python build_release.py
发版版本以本文件内 CURRENT_VERSION 为准（当前 V7.0）；目录 / ZIP 名随其变化。
若根目录存在 `.streamlit/`（如 `config.toml` 上调 `maxUploadSize`），会一并打入交付目录。

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

# 发版时与主理人约定版本对齐；ZIP / 交付文件夹名均由此派生
CURRENT_VERSION = "V7.0"
OUT_NAME = f"AI路演教练_纯净交付版_{CURRENT_VERSION}"
OUT = ROOT / OUT_NAME

WHITELIST_DIRS = ["src"]
# Streamlit 服务端配置（上传上限等），与 app 同级时由 streamlit 自动读取
STREAMLIT_DIR = ".streamlit"

# 强制存在，否则打包失败（架构传承）
REQUIRED_ROOT_FILES = [
    "app.py",
    "requirements.txt",
    "README.md",
    "ARCHITECTURE.md",
]

# 存在则拷贝，缺失不报错
OPTIONAL_ROOT_FILES = [
    "写给同事的使用说明书.txt",
    "小白保姆级操作手册.md",
    "V6.2_新功能与体验大升级.txt",
    "V7.0_新功能与体验大升级.txt",
    ".env.example",
]

# 由脚本生成，不依赖仓库里同名文件编码
GENERATED_BAT = "一键启动系统.bat"

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


def _discover_extra_launch_scripts() -> tuple[list[str], list[str]]:
    """根目录下除「一键启动系统.bat」外的 .bat / .sh，用于一并打入交付包。"""
    bats = sorted(
        p.name
        for p in ROOT.glob("*.bat")
        if p.is_file() and p.name != GENERATED_BAT
    )
    shs = sorted(p.name for p in ROOT.glob("*.sh") if p.is_file())
    return bats, shs


def _copy_whitelist_file(src_name: str, dest_dir: Path) -> None:
    src = ROOT / src_name
    dst = dest_dir / src_name

    if src_name == GENERATED_BAT:
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

    # app.py / *.md / .env.example 等：UTF-8 无 BOM + CRLF
    text = _read_text_flexible(src)
    _write_text_crlf_utf8(dst, text)


def _validate_prereqs(extra_bats: list[str], extra_shs: list[str]) -> list[str]:
    """返回缺失项列表；空表示可继续。"""
    missing: list[str] = []
    for name in REQUIRED_ROOT_FILES:
        if not (ROOT / name).is_file():
            missing.append(name)
    for d in WHITELIST_DIRS:
        if not (ROOT / d).is_dir():
            missing.append(f"{d}/")
    for name in extra_bats + extra_shs:
        if not (ROOT / name).is_file():
            missing.append(name)
    return missing


def _copy_dot_streamlit(dest_root: Path) -> None:
    """将项目根 `.streamlit` 目录原样拷入交付目录（不存在则跳过，不阻断打包）。"""
    src = ROOT / STREAMLIT_DIR
    if not src.is_dir():
        return
    dst = dest_root / STREAMLIT_DIR
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".mypy_cache"),
    )


def _make_release_zip() -> Path:
    """将纯净版文件夹打成 ZIP，位于项目根目录（与 OUT_NAME 一致，随 CURRENT_VERSION 变化）。"""
    zip_base = str(ROOT / OUT_NAME)
    zip_path = Path(zip_base + ".zip")
    if zip_path.is_file():
        zip_path.unlink()
    created = shutil.make_archive(zip_base, "zip", root_dir=str(ROOT), base_dir=OUT_NAME)
    return Path(created)


def main() -> int:
    extra_bats, extra_shs = _discover_extra_launch_scripts()
    missing = _validate_prereqs(extra_bats, extra_shs)
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

    _copy_dot_streamlit(OUT)

    _write_release_bat(OUT / GENERATED_BAT)

    for fname in REQUIRED_ROOT_FILES:
        _copy_whitelist_file(fname, OUT)

    for fname in OPTIONAL_ROOT_FILES:
        if (ROOT / fname).is_file():
            _copy_whitelist_file(fname, OUT)

    for name in extra_bats + extra_shs:
        shutil.copy2(ROOT / name, OUT / name)

    env_out = OUT / ".env"
    env_out.write_bytes(b"")

    try:
        zip_file = _make_release_zip()
    except OSError as e:
        print(f"\033[91m错误：生成 ZIP 失败：{e}\033[0m")
        print(f"纯净文件夹已生成，可手动压缩：{OUT}")
        return 1

    zip_name = zip_file.name
    print()
    print(
        "\033[1m\033[92m✅ 纯净交付版打包并压缩成功！\033[0m"
        f"\n\033[92m您现在可以直接将 【{zip_name}】 通过微信发送给同事或高管。\033[0m"
    )
    print(f"\033[96m文件夹：{OUT}\033[0m")
    print(f"\033[96m压缩包：{zip_file.resolve()}\033[0m")
    print(
        "\033[90m（已保留解压后的文件夹，便于本地校验；分发以 ZIP 为主。）\033[0m"
    )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
