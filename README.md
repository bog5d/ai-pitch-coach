<div align="center">

# 🎯 AI 路演与访谈复盘教练

**Pipeline · 词级锚定 · 单文件离线报告**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?style=flat-square&logo=streamlit&logoColor=white)](https://streamlit.io/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active-success?style=flat-square)]()

<br/>

> **一句话**：将冗长路演 / 访谈录音，自动转化为带 **毒舌找茬**、**QA 对齐** 与 **可点击音频切片** 的结构化复盘报告。

<br/>

</div>

---

## ✨ 核心功能特性

| 能力 | 说明 |
| :--- | :--- |
| **分层文档 & RAG 解析** | 多格式 QA（PDF / Word / Excel / txt / md）抽取与 **智能截断**（默认 15k 字符防爆仓）；架构预留 **Hierarchical RAG** 演进（见 `PROJECT_PLAN.md` v3.0） |
| **阿里云大模型极速转写** | 百炼 DashScope 兼容链路 + 多引擎兜底，输出 **词级时间戳** 转写 |
| **DeepSeek 毒舌对齐分析** | 结构化打分与「找茬」点评，显式业务上下文 + QA 注入 |
| **非对称音频切割** | 按词索引锚定切片，非对称缓冲（起止留白可配），报告内嵌 **Base64 音频** |

---

## 🚀 Quick Start（极速上手）

### 环境要求

- **Python 3.10+**（推荐 3.11）
- 可访问互联网（调用云端转写与 LLM API）

### 1. 克隆仓库

```bash
git clone https://github.com/<your-org>/<your-repo>.git
cd <your-repo>
```

> 将 `<your-org>/<your-repo>` 替换为你的 GitHub 远程地址。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 `.env`（必填）

在**项目根目录**新建 `.env`（可复制 `.env.example`；**切勿将含真实 Key 的 `.env` 提交到 Git**），至少配置：

| 变量 | 用途 |
| :--- | :--- |
| `DASHSCOPE_API_KEY` | 阿里云百炼 DashScope（转写 / 兼容接口探活等） |
| `DEEPSEEK_API_KEY` | DeepSeek 官方 API（默认「毒舌」复盘评委） |

可选：`SILICONFLOW_API_KEY`、`KIMI_API_KEY` 等（见各模块说明）。

### 4. 启动控制台

```bash
streamlit run app.py
```

浏览器打开后，在侧边栏完成 **API 密钥保存与连通性测试（双绿灯）**，再上传音频与 QA 文档，一键批量生成归档报告。

---

## 📦 小白专属 · Windows 无代码交付

仓库内置 **`build_release.py`**：一键生成面向 Windows 同事的 **纯净交付包**（含 **`一键启动系统.bat`**、`requirements.txt`、`app.py`、`src/` 等白名单资源，**不含** `.env` / 测试数据 / `output`）。

```bash
python build_release.py
```

生成目录：**`AI路演教练_纯净交付版/`** — 可直接拷贝至 U 盘分发；同事双击 BAT 即可完成依赖安装与启动。

> 更细的操作说明见根目录 **`小白保姆级操作手册.md`**（若随仓分发）。

---

## 🏗️ 架构速览

- **Pipeline + Pydantic 契约**（`src/schema.py`），拒绝不可控 Agent 编排  
- **词级索引** 驱动音频切割，避免全文模糊匹配  
- 详细设计见 **`PROJECT_PLAN.md`**

---

## 📁 目录结构（节选）

| 路径 | 说明 |
| :--- | :--- |
| `app.py` | Streamlit 企业控制台（密钥自检、批量归档） |
| `src/` | 转写、打分、报告拼装、文档读取、路径两栖模块等 |
| `tests/` | 测试与黄金数据（大文件见 `.gitignore`） |
| `build_release.py` | 纯净交付包打包脚本 |

---

## 📄 许可

**MIT License** — 使用第三方 API 时请遵守各平台条款，并妥善保管密钥与访谈数据。

---

<div align="center">

**Built with ☕ and strict schemas**

</div>
