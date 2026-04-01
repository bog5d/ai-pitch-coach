"""
V7.5+ 一键验收脚本（草稿 + QA 分池；与当前 CURRENT_VERSION 可并存运行）。
python test_v7_acceptance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from draft_manager import load_draft, save_draft  # noqa: E402
from llm_judge import MAX_QA_CHARS, MIDDLE_OMIT_MARK, truncate_qa_text  # noqa: E402
from runtime_paths import get_writable_app_root  # noqa: E402


def test_drafts_dir_and_atomic_write() -> None:
    sid = "v7_accept_atomic_test"
    payload = {"version": 7, "msg": "验收草稿", "items": [1, 2, 3]}
    drafts_root = get_writable_app_root() / ".drafts"
    save_draft(sid, payload)
    assert drafts_root.is_dir(), "应在可写根目录创建 .drafts"
    final = drafts_root / f"draft_{sid}.json"
    temp = drafts_root / f"temp_{sid}.json"
    assert final.is_file(), "原子写入后应存在 draft_{session_id}.json"
    assert not temp.exists(), "原子写入成功后不应残留 temp_ 文件"
    loaded = load_draft(sid)
    assert loaded == payload, "落盘 JSON 应完整可读、与内存一致"
    final.unlink(missing_ok=True)


def test_qa_truncation_50k() -> None:
    head = "UNIQUE_HEAD_V7_" + "H" * 500
    tail = "T" * 500 + "_UNIQUE_TAIL_V7"
    mid_len = 50_000 - len(head) - len(tail)
    assert mid_len > 0
    qa = head + ("M" * mid_len) + tail
    assert len(qa) == 50_000
    out, truncated = truncate_qa_text(qa, MAX_QA_CHARS)
    assert truncated, "5 万字应触发截断"
    assert len(out) <= MAX_QA_CHARS, f"截断后长度应 ≤ {MAX_QA_CHARS}，实际 {len(out)}"
    assert head[:32] in out or "UNIQUE_HEAD_V7" in out, "应保留首部关键内容"
    assert "UNIQUE_TAIL_V7" in out, "应保留尾部关键内容"
    assert MIDDLE_OMIT_MARK in out, "超长时应包含中间省略标记"


def main() -> None:
    test_drafts_dir_and_atomic_write()
    test_qa_truncation_50k()
    print("V7.x 验收（test_v7_acceptance）：全部断言通过。")


if __name__ == "__main__":
    main()
