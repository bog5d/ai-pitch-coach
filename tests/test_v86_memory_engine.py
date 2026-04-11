"""
V8.6 高管错题本（Executive Memory）— memory_engine 单元测试。

覆盖：company 分桶、空路径、读写往返、覆写、目录自动创建、损坏 JSON 降级、部分非法条目跳过。
零外部 API：仅用 tmp_path 与本地 JSON。

运行：pytest tests/test_v86_memory_engine.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import ExecutiveMemory  # noqa: E402
from memory_engine import (  # noqa: E402
    default_store_dir,
    get_memory_store_file,
    load_executive_memories,
    save_executive_memories,
)

_CO = "acme_corp"


class TestLoadSaveRoundtrip:
    def test_load_missing_file_returns_empty_list(self, tmp_path):
        assert load_executive_memories(_CO, "zhang_zong", store_dir=tmp_path) == []

    def test_save_then_load_roundtrip(self, tmp_path):
        items = [
            ExecutiveMemory(
                tag="zhang_zong",
                raw_text="我们明年一定上市",
                correction="用里程碑与假设条件表述，避免绝对化承诺",
                weight=1.5,
            )
        ]
        save_executive_memories(_CO, "zhang_zong", items, store_dir=tmp_path)
        out = load_executive_memories(_CO, "zhang_zong", store_dir=tmp_path)
        assert len(out) == 1
        assert out[0].tag == "zhang_zong"
        assert out[0].raw_text == items[0].raw_text
        assert out[0].correction == items[0].correction
        assert out[0].weight == 1.5
        assert out[0].uuid == items[0].uuid

    def test_multiple_tags_isolated_files(self, tmp_path):
        save_executive_memories(
            _CO,
            "zhang",
            [ExecutiveMemory(tag="zhang", raw_text="a", correction="A", weight=1.0)],
            store_dir=tmp_path,
        )
        save_executive_memories(
            _CO,
            "li",
            [ExecutiveMemory(tag="li", raw_text="b", correction="B", weight=2.0)],
            store_dir=tmp_path,
        )
        z = load_executive_memories(_CO, "zhang", store_dir=tmp_path)
        l = load_executive_memories(_CO, "li", store_dir=tmp_path)
        assert len(z) == 1 and z[0].raw_text == "a"
        assert len(l) == 1 and l[0].raw_text == "b"

    def test_overwrite_same_tag_replaces_list(self, tmp_path):
        save_executive_memories(
            _CO,
            "x",
            [ExecutiveMemory(tag="x", raw_text="old", correction="O", weight=1.0)],
            store_dir=tmp_path,
        )
        save_executive_memories(
            _CO,
            "x",
            [ExecutiveMemory(tag="x", raw_text="new", correction="N", weight=3.0)],
            store_dir=tmp_path,
        )
        out = load_executive_memories(_CO, "x", store_dir=tmp_path)
        assert len(out) == 1
        assert out[0].raw_text == "new"
        assert out[0].weight == 3.0

    def test_save_creates_nested_store_dir(self, tmp_path):
        nested = tmp_path / "deep" / ".executive_memory"
        save_executive_memories(
            _CO,
            "u1",
            [ExecutiveMemory(tag="u1", raw_text="r", correction="c", weight=1.0)],
            store_dir=nested,
        )
        from memory_engine import get_company_memory_dir  # noqa: E402

        assert nested.is_dir()
        assert get_company_memory_dir(_CO, nested).is_dir()

    def test_saved_payload_is_valid_json_with_items_key(self, tmp_path):
        save_executive_memories(
            _CO,
            "t1",
            [ExecutiveMemory(tag="t1", raw_text="x", correction="y", weight=1.0)],
            store_dir=tmp_path,
        )
        files = list(tmp_path.glob("**/*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) == 1

    def test_corrupted_json_returns_empty_list(self, tmp_path):
        save_executive_memories(_CO, "bad", [], store_dir=tmp_path)
        p = get_memory_store_file(_CO, "bad", tmp_path)
        p.write_text("{not json", encoding="utf-8")
        assert load_executive_memories(_CO, "bad", store_dir=tmp_path) == []

    def test_skip_invalid_items_loads_valid_ones(self, tmp_path):
        p = get_memory_store_file(_CO, "mix", tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "uuid": "00000000-0000-0000-0000-000000000001",
                            "tag": "mix",
                            "raw_text": "ok",
                            "correction": "ok2",
                            "weight": 1.0,
                        },
                        {"broken": True},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        out = load_executive_memories(_CO, "mix", store_dir=tmp_path)
        assert len(out) == 1
        assert out[0].raw_text == "ok"

    def test_unicode_fields_roundtrip(self, tmp_path):
        raw = "投资人问：估值倍数怎么定？"
        cor = "建议回答：按可比公司与增长假设分层说明。"
        save_executive_memories(
            _CO,
            "李总",
            [ExecutiveMemory(tag="李总", raw_text=raw, correction=cor, weight=0.5)],
            store_dir=tmp_path,
        )
        out = load_executive_memories(_CO, "李总", store_dir=tmp_path)
        assert len(out) == 1
        assert out[0].raw_text == raw
        assert out[0].correction == cor

    def test_legacy_flat_file_for_default_company(self, tmp_path):
        """_default 公司可读 Task1 扁平遗留文件。"""
        leg = tmp_path / "legacy_tag.json"
        leg.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "uuid": "00000000-0000-0000-0000-000000000099",
                            "tag": "legacy_tag",
                            "raw_text": "L",
                            "correction": "C",
                            "weight": 1.0,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        out = load_executive_memories("_default", "legacy_tag", store_dir=tmp_path)
        assert len(out) == 1 and out[0].raw_text == "L"


class TestDefaultStoreDir:
    def test_default_store_dir_under_writable_root(self, tmp_path, monkeypatch):
        # V10.0：default_store_dir() 现在通过 get_memory_root() 中转，
        # mock 目标从 get_writable_app_root 改为 get_memory_root
        monkeypatch.setattr("memory_engine.get_memory_root", lambda: tmp_path / ".executive_memory")
        d = default_store_dir()
        assert d == tmp_path / ".executive_memory"
