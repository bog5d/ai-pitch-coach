# /release-checklist — AI Pitch Coach 全自动发版流水线

**触发时机**：每当需要发布新版本时，必须调用此技能，按序执行每一步。
跳过任何一步都会导致版本号不一致、用户拿到缺失文档的包或 GitHub 落后于本地。

---

## 发版前：三处版本号必须同时对齐（Iron Rule）

```
build_release.py → CURRENT_VERSION = "VX.X"   ← 决定 ZIP 名
CLAUDE.md → 附：快速检查清单末尾时间戳        ← 记录更新时间
.cursorrules（若存在）→ 版本引用              ← IDE 插件读取
```

**执行命令：用 grep 确认三处一致，再动手改**

```bash
grep -n "CURRENT_VERSION\|V[0-9]\.[0-9]" build_release.py CLAUDE.md .cursorrules 2>/dev/null
```

---

## 发版流水线（顺序执行，全程自主，无需请示）

### Step 1 · 确认测试全绿
```bash
python -m pytest tests/ -q
# 必须 N passed, 0 failed。有红灯→修复→重跑，不得跳过。
```

### Step 2 · 三处版本号同步
- `build_release.py::CURRENT_VERSION` → `"VX.X"`
- `CLAUDE.md` 末尾时间戳 → 当前日期
- `.cursorrules`（若存在）→ 版本引用更新

### Step 3 · 更新 CHANGELOG.md

在文件顶部插入新版本块，格式：

```markdown
## [VX.X] — YYYY-MM-DD · {版本副标题}

### 新增
- ...

### 修复
- ...

### 改动
- ...
```

### Step 4 · 更新 PROJECT_PLAN.md
- 找到上个版本的阶段条目，确认已标 `[x]`
- 追加新版本阶段：`- [x] **阶段 X.X：VX.X {功能名}** 【COMPLETED YYYY-MM-DD】`
- 更新"当前状态"描述段落

### Step 5 · 更新 ARCHITECTURE.md
- 顶部标题加入新版本号
- 总览表格中涉及模块的描述追加 `**VX.X** 新增/修改描述`
- 若有新架构模式，追加对应章节
- 末尾文档版本号更新

### Step 6 · 写业务说明 txt（用大白话，非工程师能看懂）

文件名格式：`VX.X_专家共驾版_功能说明.txt`（或对应副标题）

内容结构：
```
🚀 VX.X {版本名} — 本版做了哪三件事

━━━━━━━━━━━━
第一件：{用户能感知的变化，非技术语言}
━━━━━━━━━━━━
{类比解释，避免技术词汇}

（重复 N 件事）

━━━━━━━━━━━━
工程侧说明（给技术同事）
━━━━━━━━━━━━
{测试数量、关键文件、发版命令}
```

### Step 7 · 将新 txt 加入白名单
打开 `build_release.py`，在 `OPTIONAL_ROOT_FILES` 列表中追加：
```python
"VX.X_专家共驾版_功能说明.txt",
```

### Step 8 · 执行打包
```bash
python build_release.py
# 确认输出：✅ 纯净交付版打包并压缩成功！
# 确认 ZIP 文件名包含新版本号
```

### Step 9 · Git 提交与推送

```bash
git add {所有变更文件}
git status --short   # 确认 staged 内容无误，无敏感文件混入

git commit -m "release: VX.X {版本副标题} - {核心变更摘要}"

# 中国环境推送（proven：socks5:7897）
git config --global http.proxy socks5://127.0.0.1:7897
git push origin main
git config --global --unset http.proxy   # 推送成功后立即清理
```

### Step 10 · 发版后自检

```bash
git log --oneline -3   # 确认最新 commit 已推送
```

确认以下文件均在本次 commit 中：
- [ ] `build_release.py`（版本号）
- [ ] `CHANGELOG.md`（新条目）
- [ ] `PROJECT_PLAN.md`（COMPLETED）
- [ ] `ARCHITECTURE.md`（版本号 + 新章节）
- [ ] `VX.X_*说明.txt`（业务说明）
- [ ] `CLAUDE.md`（时间戳）

---

## 快速召唤

对话中输入 `/release-checklist` 即可加载此流水线。
