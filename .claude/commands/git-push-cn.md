# /git-push-cn — 中国网络环境 Git 推送（Clash 代理自动识别）

**触发时机**：每次需要 `git push` 到 GitHub 时，直接调用此技能，不要手动猜代理端口。

---

## 背景（为什么需要这个技能）

国内网络直连 GitHub 443 端口极不稳定。本项目主理人使用 Clash 代理，
经过多次实测，唯一稳定可用的配置是：

```
socks5://127.0.0.1:7897
```

以下配置**已确认不可用或不稳定**（节省下次排查时间）：

| 配置 | 结果 |
|------|------|
| `http://127.0.0.1:7890` | `Could not connect to server`（端口未监听） |
| `http://127.0.0.1:7897` | `TLS unexpected eof`（协议不匹配） |
| 无代理直连 | `TLS unexpected eof`（GFW 干扰） |
| `socks5://127.0.0.1:7890` | 未测试，可尝试 |
| **`socks5://127.0.0.1:7897`** | **✅ 稳定成功** |

---

## 标准执行流程

```bash
# Step 1：确认远程仓库地址
git remote -v

# Step 2：设置代理（直接用 proven 配置，不要逐一试）
git config --global http.proxy socks5://127.0.0.1:7897

# Step 3：推送
git push origin main

# Step 4：推送成功后立即清理代理（避免影响其他工具）
git config --global --unset http.proxy
git config --global http.sslVerify true   # 确保 SSL 验证已恢复

# Step 5：确认推送成功
git log --oneline -3
```

---

## 推送失败时的系统化排查顺序

若 `socks5://7897` 也失败，按以下顺序排查（每步等 5s 再重试）：

```bash
# 1. 确认 Clash 正在运行（任务栏图标或系统代理）

# 2. 尝试 socks5:7890
git config --global http.proxy socks5://127.0.0.1:7890
git push origin main

# 3. 尝试 http:7890（Clash 混合端口）
git config --global http.proxy http://127.0.0.1:7890
git push origin main

# 4. 若均失败：检查 Clash 模式是否设置为「全局」而非「规则」
# 5. 若仍失败：确认 Clash 版本，重启 Clash 后重试 socks5:7897
```

---

## 一键复制命令（最常用场景）

```bash
git config --global http.proxy socks5://127.0.0.1:7897 && git push origin main && git config --global --unset http.proxy && echo "✅ 推送成功，代理已清理"
```

---

## 安全说明

- 代理设置为 `--global`，会影响系统所有 git 操作，因此推送后**必须**立即 unset。
- 永远不要在 CI/CD 环境中使用 `--global` 代理。
- sslVerify 保持 `true`，不要为了省事关闭（降低安全性）。

---

## 快速召唤

对话中输入 `/git-push-cn` 即可加载此技能。
