# Skill: release-checklist
# AI Pitch Coach · 全自动发版流水线技能

## 技能定位

**何时调用**：每次发布新版本（VX.X）时，必须调用此技能，不得凭记忆操作。

**核心价值**：消除发版中最容易遗漏的三处版本号对齐问题，并确保文档、打包、推送全链路完整。

---

## 历史教训（为什么需要这个技能）

从 V6.2 到 V7.6，每次发版都会出现以下问题之一：
- `CURRENT_VERSION` 改了但说明 txt 忘了加白名单 → 包里没有新说明文档
- CHANGELOG 没更新 → 历史可追溯性断裂
- PROJECT_PLAN 忘记标 COMPLETED → 下次接手不知道当前进度
- git push 不知道用哪个代理 → 反复试错浪费 10+ 分钟

---

## 十步流水线速查

| Step | 动作 | 验证方式 |
|------|------|----------|
| 1 | `pytest tests/ -q` → 全绿 | 输出 `N passed` |
| 2 | 三处版本号对齐（build_release.py / CLAUDE.md / .cursorrules） | `grep -n "V[0-9]"` 确认一致 |
| 3 | CHANGELOG.md 顶部插入新版本块 | 文件存在且最新条目正确 |
| 4 | PROJECT_PLAN.md 标 COMPLETED + 更新"当前状态" | 文件已更新 |
| 5 | ARCHITECTURE.md 版本号 + 新章节 | 顶部标题含新版本号 |
| 6 | 写 VX.X_*说明.txt（大白话版） | 文件存在 |
| 7 | build_release.py OPTIONAL_ROOT_FILES 加新 txt | grep 确认已加 |
| 8 | `python build_release.py` → ZIP 生成 | 输出包含新版本号的 ZIP 路径 |
| 9 | git add → commit → socks5:7897 push → unset proxy | `git log --oneline -1` 确认 |
| 10 | 发版后自检：确认以上文件均在 commit 中 | `git show --stat HEAD` |

---

## 详细步骤

见 `.claude/commands/release-checklist.md`（slash command 版本，含完整代码片段）。

---

## 关键记忆点

- **ZIP 名由 `CURRENT_VERSION` 决定**，改了版本号才会生成新名称的包
- **说明 txt 必须加入 OPTIONAL_ROOT_FILES 白名单**，否则不会打进 ZIP
- **git push 固定用 `socks5://127.0.0.1:7897`**（见 `/git-push-cn` 技能）
- **推送后立即 `--unset http.proxy`**，不清理会影响其他工具

*本技能由协作历史提炼，V7.6 收官后写入。*
