# Windows EXE 打包说明（Streamlit Launcher）

> **交付策略（必读）**  
> **推荐生产/同事分发**：使用根目录 `python build_release.py` 生成的 **BAT + 源码纯净包**（`AI路演教练_纯净交付版/`），路径与子进程行为与开发机一致，排障成本最低。  
> **EXE**：实验性「双击启动」体验；Streamlit 在冻结环境下偶发路径/体积问题，打包与运行说明见下文。若 EXE 异常，请优先退回 BAT 包。

## 前置条件

- Python 3.10+（本仓库在 3.13 上已验证可构建）
- **PyInstaller 依赖 `pkg_resources`**：若遇 `ModuleNotFoundError: No module named 'pkg_resources'`，请固定：
  ```bash
  pip install "setuptools<81"
  ```
- 安装打包工具：
  ```bash
  pip install pyinstaller
  ```

## 启动器

根目录 **`run_exe.py`**：冻结环境下 `chdir(sys._MEIPASS)`，并调用 `streamlit.web.cli.main()` 等价于 `streamlit run app.py`。

## 推荐命令（onedir）

在项目根目录执行（Windows 下 `--add-data` 使用分号 `;`）：

```bash
REM 推荐使用 --console（有黑框），可看到 Local URL 与报错；不要用 --windowed 调试「无反应」
python -m PyInstaller --noconfirm --onedir --console --name "AI路演复盘教练" ^
  --add-data "app.py;." --add-data "src;src" ^
  --collect-all streamlit --collect-all pyarrow --collect-all altair ^
  --hidden-import=document_reader --hidden-import=llm_judge --hidden-import=report_builder ^
  --hidden-import=transcriber --hidden-import=schema --hidden-import=runtime_paths ^
  --hidden-import=job_pipeline ^
  run_exe.py
```

若确认一切正常后再隐藏黑框，可改为 `--windowed` 重新打包（仍须保持 `run_exe.py` 里 `--server.headless=false`，否则浏览器不会自动打开）。

产物目录：**`dist/AI路演复盘教练/`**（若控制台编码异常，资源管理器中名称可能显示为乱码，以实际文件夹为准）。

> **说明**：`--collect-all streamlit` 会连带分析环境中已安装的依赖，若本机装有 PyTorch / OpenCV 等，**体积会显著增大**。建议在**仅安装本项目 `requirements.txt` 的虚拟环境**中打包以获得更小体积。

## 与 BAT 纯净包的关系

- **EXE**：面向「双击运行」的单体体验（仍需本机依赖已打入目录）。
- **BAT 纯净包**：由 `python build_release.py` 生成 **`AI路演教练_纯净交付版/`**，走 `pip install` + `streamlit run`，不含 `dist/`。
