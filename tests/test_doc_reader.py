"""
document_reader 集成测试：Token 截断与损坏文件容错。
仓库发版 V6.2（与 build_release.CURRENT_VERSION 对齐）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from document_reader import extract_text_from_files  # noqa: E402


class _FakeUpload:
    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


def test_token_cap_exactly_15000() -> None:
    """50,000 字中文 → 输出长度必须严格等于 15000。"""
    big = "测" * 50_000
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        encoding="utf-8",
        delete=False,
    ) as f:
        f.write(big)
        path = f.name
    try:
        data = Path(path).read_bytes()
        out = extract_text_from_files([_FakeUpload("huge.txt", data)], max_chars=15000)
        assert len(out) == 15000, f"期望 15000，实际 {len(out)}"
    finally:
        Path(path).unlink(missing_ok=True)


def test_corrupt_pdf_no_crash() -> None:
    """损坏 PDF 不抛异常，返回空串或仍可处理其他文件。"""
    garbage = b"%PDF-1.4\n" + b"\x00\xff" * 200 + b"not valid pdf content"
    bad = _FakeUpload("broken.pdf", garbage)
    out = extract_text_from_files([bad], max_chars=15000)
    assert isinstance(out, str)
    assert out == ""

    good = _FakeUpload("ok.txt", "你好世界".encode("utf-8"))
    out2 = extract_text_from_files([bad, good], max_chars=15000)
    assert isinstance(out2, str)
    assert "你好世界" in out2


def test_separate_upload_lists_independent_text() -> None:
    """不同文件列表应得到不同抽取结果（支撑批量「每录音独立 QA」）。"""
    u1 = _FakeUpload("x.txt", b"direction_a_qa")
    u2 = _FakeUpload("y.txt", b"direction_b_qa")
    o1 = extract_text_from_files([u1], max_chars=15000)
    o2 = extract_text_from_files([u2], max_chars=15000)
    assert "direction_a" in o1 and "direction_b" not in o1
    assert "direction_b" in o2 and "direction_a" not in o2


if __name__ == "__main__":
    test_token_cap_exactly_15000()
    test_corrupt_pdf_no_crash()
    test_separate_upload_lists_independent_text()
    print("OK: test_doc_reader 全部通过")
