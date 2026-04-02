# Skill: git-push-cn
# AI Pitch Coach · 中国网络环境 Git 推送技能

## 技能定位

**何时调用**：每次需要 `git push` 到 GitHub 时。不要手动猜代理，直接用此技能。

## 经实测验证的唯一稳定配置

```bash
git config --global http.proxy socks5://127.0.0.1:7897
git push origin main
git config --global --unset http.proxy
```

## 已测试失败的配置（不要再试）

| 配置 | 失败原因 |
|------|----------|
| `http://127.0.0.1:7890` | 端口未监听 HTTP 代理 |
| `http://127.0.0.1:7897` | TLS unexpected eof（协议不匹配） |
| 无代理直连 | TLS unexpected eof（GFW 干扰） |

## 一键命令

```bash
git config --global http.proxy socks5://127.0.0.1:7897 && git push origin main && git config --global --unset http.proxy && echo "✅ 推送成功，代理已清理"
```

详细排查流程见 `.claude/commands/git-push-cn.md`。

*实测于 2026-04-02，Clash 代理 socks5 端口 7897。*
