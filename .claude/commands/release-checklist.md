# /release-checklist — AI Pitch Coach 全自动发版流水线 V2.0

**触发时机**：修复或功能开发完成后，准备发版交付时调用。
**执行策略**：Claude 全程自主完成，不中断确认，最终汇报"版本号/ZIP路径/测试数/commit hash"。

---

## 版本分级（先判断）

| 变更类型 | 版本规则 | 示例 |
|---------|---------|------|
| Bug 修复 / 体验微调 | X.Y.**Z+1** | V9.6.2 → V9.6.3 |
| 新功能 / 较大重构 | X.**Y+1**.0 | V9.6.3 → V9.7.0 |
| 架构重写 | **X+1**.0.0 | V9.x → V10.0.0 |

---

## Phase 0 · 测试绿灯（前置条件，不可跳过）

```bash
python -m pytest tests/ -q --tb=short
# 必须：N passed, 0 failed
# 有红灯 → 先修复 → 重跑 → 再继续
```

---

## Phase 1 · 决定新版本号

```bash
grep "CURRENT_VERSION" build_release.py
# 读取当前版本，按分级规则 +1，记为 NEW_VER
```

---

## Phase 2 · 四处版本号同步（铁律，全部改完再继续）

```bash
# 1. build_release.py（两处：注释行 + CURRENT_VERSION）
#    注释行：发版版本以本文件内 CURRENT_VERSION 为准（当前 NEW_VER）
#    代码行：CURRENT_VERSION = "NEW_VER"

# 2. CLAUDE.md 末行时间戳
#    *最后更新：NEW_VER {版本副标题}；铁律一至五仍适用。*

# 3. .cursorrules 第3行
#    当前版本：NEW_VER。

# 4. AGENTS.md 握手区三行（新增，V2.0 必须）
#    | **发版号** | ...（现为 **NEW_VER**）... |
#    | **能力代际** | ...末尾追加 **NEW_VER**：{一句话概括}。 |
#    | **回归测试** | ...当前全量 **N passed**... |

# 验证四处一致
grep -n "V[0-9]\+\.[0-9]" build_release.py CLAUDE.md .cursorrules AGENTS.md 2>/dev/null | grep -v "history\|old\|example"
```

---

## Phase 3 · CHANGELOG.md 顶部插入

在文件第一个 `---` 分隔线**之前**插入（即顶部标题后）：

```markdown
## [NEW_VER] — YYYY-MM-DD · {版本副标题}

{一句话摘要}，**N passed**。

### 修复（N 项）

| # | 问题描述 | 根因 | 修复位置 |
|---|---------|------|---------|
| Fix-A | {现象} | {根因} | {文件:函数} |
```

---

## Phase 4 · 写用户侧说明 txt

**文件名**：`NEW_VER_{版本副标题}_说明.txt`
**语言**：纯中文大白话，非工程师可读，不用技术词汇

```
AI 路演教练 NEW_VER — {副标题}
更新日期：YYYY-MM-DD
======================================

本版{一句话概括，避免技术词汇}。

──────────────────────────────────────
一、{第一项改动的用户感知描述}
──────────────────────────────────────
• 修复前：{用户看到的现象}
• 修复后：{修复后的效果}

（按修复项数重复）

──────────────────────────────────────
测试与版本
──────────────────────────────────────
• 全量自动化回归：N passed（Mock 外部 API，不扣费）。
• 纯净包目录名以 build_release.py 里的版本号为准，本版为 NEW_VER。

祝使用顺利。若仍有异常，请把 debug.log 与操作步骤一并反馈给主理人。
```

---

## Phase 5 · build_release.py 白名单追加

在 `OPTIONAL_ROOT_FILES` 列表，`.env.example` 前插入：

```python
"NEW_VER_{副标题}_说明.txt",
```

---

## Phase 6 · 打包

```bash
python build_release.py
# 确认输出：✅ 纯净交付版打包并压缩成功！
# 确认输出路径中含新版本号
```

---

## Phase 7 · Git 提交（两次，职责分离）

```bash
# ── Commit A：代码变更（若本次修复尚未提交）──
git add src/*.py app.py tests/  # 按实际改动文件精确 add
git commit -m "$(cat <<'EOF'
fix/feat({scope}): {描述}

{可选：多行补充说明}

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"

# ── Commit B：文档 + 发版文件 ──
git add CHANGELOG.md CLAUDE.md AGENTS.md .cursorrules build_release.py "NEW_VER_*说明.txt"
git commit -m "$(cat <<'EOF'
release(NEW_VER): 文档更新 + 发版号对齐，{副标题}收官

- CHANGELOG.md：补 NEW_VER 条目
- AGENTS.md / CLAUDE.md / .cursorrules：版本号对齐
- build_release.py：CURRENT_VERSION + 白名单新增说明书
- 新增 NEW_VER_{副标题}_说明.txt

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 8 · 推送（中国网络代理）

```bash
# 直接用 proven 配置，不要逐一试
git config --global http.proxy socks5://127.0.0.1:7897
git push origin main
git config --global --unset http.proxy   # 推送后立即清理！
echo "✅ 推送成功，代理已清理"
```

**推送失败回退顺序**（每步等 5s 再重试）：
1. `socks5://127.0.0.1:7890`
2. `http://127.0.0.1:7890`
3. 检查 Clash 是否运行 + 是否为「全局」模式

---

## Phase 9 · 自检汇报

```bash
git log --oneline -3   # 确认最新 commits 已推送
```

向主理人汇报（固定格式）：
```
✅ NEW_VER 发版完毕

- 测试：N passed, 0 failed
- ZIP：AI路演教练_纯净交付版_NEW_VER.zip
- 推送：main ← {commit_hash} "{commit_msg}"
```

---

## 发版后自检 checklist

- [ ] `build_release.py`：版本号已更新
- [ ] `CLAUDE.md`：末行时间戳已更新
- [ ] `.cursorrules`：版本引用已更新
- [ ] `AGENTS.md`：握手区三行已更新 ← **V2.0 新增必检项**
- [ ] `CHANGELOG.md`：顶部有新版本块
- [ ] `NEW_VER_*说明.txt`：已创建且在白名单
- [ ] ZIP：已生成，文件名含新版本号
- [ ] GitHub：commits 已推送

---

## 快速召唤

对话中输入 `/release-checklist` 即可加载此流水线。
