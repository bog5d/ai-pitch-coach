# -*- coding: utf-8 -*-
"""批量将复盘报告 HTML 重命名为外发脱敏文件名（一次性脚本）。仓库发版 V6.2（与根目录 build_release.CURRENT_VERSION 对齐）。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TARGET = Path(r"D:\AI_Workspaces\AI_Pitch_Coach\01_机构路演\未命名批次")

# 姓名 -> 外发代号
NAME_TO_CODE: dict[str, str] = {
    "邓勇": "DY",
    "黄含": "HH",
    "李志新": "LZX",
    "孙艳科": "SYK",
    "覃基绍": "QJS",
    "赵治鹏": "ZZP",
}

PAT = re.compile(r"^迪策资本-(.+?)20260108_复盘报告\.html$")


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else TARGET
    if not root.is_dir():
        print(f"目录不存在: {root}", file=sys.stderr)
        return 1
    n = 0
    for f in sorted(root.glob("*.html")):
        m = PAT.match(f.name)
        if not m:
            print(f"跳过（不匹配规则）: {f.name}")
            continue
        cn_name = m.group(1)
        code = NAME_TO_CODE.get(cn_name)
        if not code:
            print(f"跳过（未配置代号）: {f.name} 姓名={cn_name!r}")
            continue
        new_name = f"DC资本-{code}20260108_复盘报告.html"
        dest = root / new_name
        if dest.exists() and dest.resolve() != f.resolve():
            print(f"已存在目标，跳过: {new_name}")
            continue
        f.rename(dest)
        print(f"OK: {f.name} -> {new_name}")
        n += 1
    print(f"共重命名 {n} 个文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
