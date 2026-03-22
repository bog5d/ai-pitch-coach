"""
轻量级 API 连通性探针：极小 Token，验证 .env 中硅基流动 + DeepSeek 是否可用。
运行：python test_apis.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Windows 控制台尽量用 UTF-8，避免中文/符号 print 报错
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv
from openai import APIError, OpenAI

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")

# Windows 终端常见支持 ANSI；若无色可忽略
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def _ping(name: str, client: OpenAI, model: str) -> tuple[bool, str]:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "ping"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=5,
            temperature=0,
        )
        if not r.choices:
            return False, "响应无 choices"
        c = r.choices[0].message.content
        return True, f"reply={c!r}"
    except APIError as e:
        return False, f"APIError: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    ok_all = True

    # 硅基流动 — Qwen2.5-7B-Instruct（与 llm_judge 一致）
    key_sf = os.getenv("SILICONFLOW_API_KEY")
    if not key_sf:
        print(f"{RED}{BOLD}[FAIL] 未设置 SILICONFLOW_API_KEY{RESET}")
        ok_all = False
    else:
        client = OpenAI(
            base_url="https://api.siliconflow.cn/v1",
            api_key=key_sf,
        )
        ok, detail = _ping("siliconflow", client, "Qwen/Qwen2.5-7B-Instruct")
        if ok:
            print(
                f"{GREEN}{BOLD}OK [硅基流动 Qwen2.5-7B] 连通性测试通过！{RESET} ({detail})"
            )
        else:
            print(f"{RED}{BOLD}FAIL [硅基流动 Qwen2.5-7B] {detail}{RESET}")
            ok_all = False

    key_ds = os.getenv("DEEPSEEK_API_KEY")
    if not key_ds:
        print(f"{RED}{BOLD}[FAIL] 未设置 DEEPSEEK_API_KEY{RESET}")
        ok_all = False
    else:
        client = OpenAI(
            base_url="https://api.deepseek.com",
            api_key=key_ds,
        )
        ok, detail = _ping("deepseek", client, "deepseek-chat")
        if ok:
            print(
                f"{GREEN}{BOLD}OK [DeepSeek deepseek-chat] 连通性测试通过！{RESET} ({detail})"
            )
        else:
            print(f"{RED}{BOLD}FAIL [DeepSeek] {detail}{RESET}")
            ok_all = False

    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
