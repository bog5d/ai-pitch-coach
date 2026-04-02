"""V8.4 公司档案模块 TDD 测试套件。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── Task 1: CompanyProfile 模型 ──────────────────────────────────────────────

def test_company_profile_defaults():
    """CompanyProfile 可用最少字段构造，可选字段有默认值。"""
    from schema import CompanyProfile
    p = CompanyProfile(company_id="test_co", display_name="测试公司")
    assert p.company_id == "test_co"
    assert p.display_name == "测试公司"
    assert p.background == ""
    assert p.created_at == ""
    assert p.updated_at == ""


def test_company_profile_full():
    """CompanyProfile 接受完整字段并正确存储。"""
    from schema import CompanyProfile
    p = CompanyProfile(
        company_id="abc",
        display_name="ABC 资本",
        background="成立于2015年",
        created_at="2026-04-02T10:00:00",
        updated_at="2026-04-02T10:00:00",
    )
    assert p.background == "成立于2015年"
    assert p.created_at == "2026-04-02T10:00:00"


def test_company_profile_uuid_auto_generated():
    """uuid 字段在未提供时自动生成，非空且为合法 UUID 格式。"""
    import uuid as uuid_mod
    from schema import CompanyProfile
    p = CompanyProfile(company_id="x", display_name="X")
    assert p.uuid != ""
    # 验证是合法 UUID
    parsed = uuid_mod.UUID(p.uuid)
    assert str(parsed) == p.uuid


def test_company_profile_uuid_unique_per_instance():
    """每个实例的 uuid 都不同。"""
    from schema import CompanyProfile
    p1 = CompanyProfile(company_id="a", display_name="A")
    p2 = CompanyProfile(company_id="b", display_name="B")
    assert p1.uuid != p2.uuid


def test_company_profile_uuid_can_be_provided():
    """可以手动传入 uuid（如从 JSON 反序列化时）。"""
    from schema import CompanyProfile
    fixed_uuid = "12345678-1234-5678-1234-567812345678"
    p = CompanyProfile(company_id="x", display_name="X", uuid=fixed_uuid)
    assert p.uuid == fixed_uuid


# ── Task 2: CRUD 函数 ────────────────────────────────────────────────────────

def test_save_and_load_company(tmp_path):
    """保存后读取结果一致。"""
    from schema import CompanyProfile
    import company_profile as cp

    p = CompanyProfile(
        company_id="abc_capital",
        display_name="ABC 资本",
        background="成立于2015年",
        created_at="2026-04-02T10:00:00",
        updated_at="2026-04-02T10:00:00",
    )
    cp.save_company(p, profiles_dir=tmp_path)
    loaded = cp.load_company("abc_capital", profiles_dir=tmp_path)
    assert loaded is not None
    assert loaded.display_name == "ABC 资本"
    assert loaded.background == "成立于2015年"


def test_load_nonexistent_returns_none(tmp_path):
    """加载不存在的公司返回 None，不抛异常。"""
    import company_profile as cp
    result = cp.load_company("does_not_exist", profiles_dir=tmp_path)
    assert result is None


def test_list_companies(tmp_path):
    """正确枚举目录中的所有公司档案。"""
    from schema import CompanyProfile
    import company_profile as cp

    cp.save_company(CompanyProfile(company_id="co_a", display_name="A公司"), profiles_dir=tmp_path)
    cp.save_company(CompanyProfile(company_id="co_b", display_name="B公司"), profiles_dir=tmp_path)
    companies = cp.list_companies(profiles_dir=tmp_path)
    ids = {c.company_id for c in companies}
    assert ids == {"co_a", "co_b"}


def test_list_companies_empty_dir(tmp_path):
    """目录为空时返回空列表，不抛异常。"""
    import company_profile as cp
    result = cp.list_companies(profiles_dir=tmp_path)
    assert result == []


def test_atomic_write(tmp_path):
    """save_company 使用原子写入（先写 .tmp 再 rename），最终文件内容正确，无 .tmp 残留。"""
    import json
    from schema import CompanyProfile
    import company_profile as cp

    p = CompanyProfile(company_id="x", display_name="X")
    cp.save_company(p, profiles_dir=tmp_path)
    # 验证 .tmp 文件不残留
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []
    # 验证正式文件存在且合法 JSON
    json_file = tmp_path / "x.json"
    assert json_file.exists()
    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data["company_id"] == "x"


def test_delete_company(tmp_path):
    """删除公司档案后 load 返回 None。"""
    from schema import CompanyProfile
    import company_profile as cp

    cp.save_company(CompanyProfile(company_id="del_me", display_name="删我"), profiles_dir=tmp_path)
    cp.delete_company("del_me", profiles_dir=tmp_path)
    assert cp.load_company("del_me", profiles_dir=tmp_path) is None


def test_delete_nonexistent_no_error(tmp_path):
    """删除不存在的公司不抛异常。"""
    import company_profile as cp
    cp.delete_company("ghost", profiles_dir=tmp_path)  # 不应抛出


def test_save_preserves_uuid(tmp_path):
    """保存后读取时，uuid 字段与原始一致（不被覆盖）。"""
    from schema import CompanyProfile
    import company_profile as cp

    fixed_uuid = "aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb"
    p = CompanyProfile(company_id="u_test", display_name="UUID测试", uuid=fixed_uuid)
    cp.save_company(p, profiles_dir=tmp_path)
    loaded = cp.load_company("u_test", profiles_dir=tmp_path)
    assert loaded is not None
    assert loaded.uuid == fixed_uuid
