@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo ========================================
echo   AI 路演教练 — 依赖安装与启动
echo ========================================
echo.
echo [1/2] 正在安装/更新依赖（requirements.txt）...
python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo 依赖安装失败，请检查 Python 与网络。
    pause
    exit /b 1
)
echo [2/2] 正在启动 Streamlit 控制台...
echo 浏览器将自动打开；若未打开请访问终端提示的 Local URL。
echo.
python -m streamlit run app.py
if errorlevel 1 pause
