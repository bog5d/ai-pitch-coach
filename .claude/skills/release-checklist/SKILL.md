# Skill: release-checklist
# AI Pitch Coach · 全自动发版流水线（V2.0）

## 技能定位

**何时调用**：任何修复或功能开发完成后，准备对外交付时立即执行。
**执行方式**：Claude 全程自主完成，无需人工确认每步，最终只汇报结果。

---

## 历史教训（为什么需要这个技能）

从 V6.2 到 V9.6.x，每次发版会出现以下问题之一：
- `CURRENT_VERSION` 改了但说明 txt 忘了加白名单 → 包里没有新说明文档
- CHANGELOG 没更新 → 历史可追溯性断裂
- **AGENTS.md 握手区版本号没同步** → 下次新 AI 会话读到过期版本信息
- git push 不知道用哪个代理 → 反复试错浪费 10+ 分钟

---

## 版本分级规则（先判断再执行）

| 变更类型 | 版本规则 | 示例 |
|---------|---------|------|
| Bug 修复 / 体验微调 | X.Y.**Z+1**（Patch） | V9.6.2 → V9.6.3 |
| 新功能 / 较大重构 | X.**Y+1**.0（Minor） | V9.6.3 → V9.7.0 |
| 架构重写 | **X+1**.0.0（Major） | V9.x → V10.0.0 |

---

## 十步流水线速查

| Step | 动作 | 验证方式 |
|------|------|----------|
| 0 | `pytest tests/ -q` → 全绿 | `N passed, 0 failed` |
| 1 | 决定新版本号（分级规则） | — |
| 2 | **四处**版本号同步（build_release.py / CLAUDE.md / .cursorrules / AGENTS.md） | grep 确认一致 |
| 3 | CHANGELOG.md 顶部插入新版本块 | 最新条目版本号正确 |
| 4 | 写 VX.X.Z_*说明.txt（大白话，非工程师可读） | 文件已创建 |
| 5 | build_release.py OPTIONAL_ROOT_FILES 加新 txt | grep 确认已加 |
| 6 | `python build_release.py` → ZIP 生成 | 输出含新版本号的 ZIP 路径 |
| 7 | Commit A：代码变更（若未提交） | `git log --oneline -1` |
| 8 | Commit B：文档 + 发版文件 | `git show --stat HEAD` |
| 9 | `socks5:7897` push → unset proxy | `git log --oneline -1` 确认已推 |

---

## 详细步骤

见 `.claude/commands/release-checklist.md`（含完整命令片段）。

---

## 关键记忆点

- **AGENTS.md 必须同步** — 握手区是新 AI 会话首读，版本号过期会误导接手
- **ZIP 名由 `CURRENT_VERSION` 决定** — 改了版本号才会生成新名称的包
- **说明 txt 必须加入 OPTIONAL_ROOT_FILES 白名单** — 否则不会打进 ZIP
- **两次 commit 分职责** — 代码 commit + 文档发版 commit，回滚更清晰
- **git push 固定用 `socks5://127.0.0.1:7897`** — 推送后立即 unset proxy

*本技能 V2.0 由 V9.6.3 发版实战提炼，2026-04-11 更新。*
