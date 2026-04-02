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
