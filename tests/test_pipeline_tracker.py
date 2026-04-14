"""
test_pipeline_tracker.py — Sprint 4 融资过程CRM单元测试

设计原则：
  - 全部使用 tmp_path 隔离，不写真实 .pipeline/ 目录
  - 不调用任何外部API
  - 验证：状态机流转 / 持久化 / 时间线追加 / 边界case
"""
import json
import sys
from pathlib import Path
from datetime import date

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline_tracker import (
    PipelineStatus,
    PipelineRecord,
    TimelineEntry,
    PipelineStore,
    VALID_STATUS_TRANSITIONS,
)


# ─────────────────────────────────────────────
# 测试夹具
# ─────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    """使用 tmp_path 隔离的 PipelineStore。"""
    return PipelineStore(pipeline_dir=str(tmp_path / ".pipeline"))


@pytest.fixture
def sample_record():
    return PipelineRecord(
        record_id="dikce_zetian_001",
        institution_id="dikce_capital",
        institution_name="迪策资本",
        company_id="zetian_zhihang",
        company_name="泽天智航",
        status=PipelineStatus.INITIAL_CONTACT,
        contacts=[{"name": "李志新", "title": "合伙人"}],
    )


# ─────────────────────────────────────────────
# 1. PipelineRecord 基本操作
# ─────────────────────────────────────────────

class TestPipelineRecord:
    def test_create_record(self, sample_record):
        assert sample_record.institution_name == "迪策资本"
        assert sample_record.status == PipelineStatus.INITIAL_CONTACT
        assert sample_record.timeline == []

    def test_add_timeline_entry(self, sample_record):
        sample_record.add_event("初次路演，对方表示兴趣浓厚")
        assert len(sample_record.timeline) == 1
        assert sample_record.timeline[0].note == "初次路演，对方表示兴趣浓厚"

    def test_timeline_entry_has_date(self, sample_record):
        sample_record.add_event("测试事件")
        entry = sample_record.timeline[0]
        assert entry.date  # 不为空
        # date 格式为 YYYY-MM-DD
        assert len(entry.date) == 10

    def test_update_status(self, sample_record):
        sample_record.update_status(PipelineStatus.NDA_SIGNED, note="NDA已签署")
        assert sample_record.status == PipelineStatus.NDA_SIGNED
        # 应该自动添加时间线事件
        assert len(sample_record.timeline) == 1
        assert "NDA" in sample_record.timeline[0].note or "已签署" in sample_record.timeline[0].note

    def test_link_interview(self, sample_record):
        sample_record.link_interview("迪策资本-李志新_前1-5测试")
        assert "迪策资本-李志新_前1-5测试" in sample_record.linked_interviews

    def test_serialize_deserialize(self, sample_record):
        sample_record.add_event("测试事件")
        data = sample_record.to_dict()
        restored = PipelineRecord.from_dict(data)
        assert restored.institution_name == sample_record.institution_name
        assert restored.status == sample_record.status
        assert len(restored.timeline) == 1


# ─────────────────────────────────────────────
# 2. 状态机约束测试
# ─────────────────────────────────────────────

class TestStatusTransitions:
    def test_valid_transition(self, sample_record):
        """初步接触 → NDA签署 是合法转换。"""
        # 检查转换规则存在
        allowed = VALID_STATUS_TRANSITIONS.get(PipelineStatus.INITIAL_CONTACT, [])
        assert PipelineStatus.NDA_SIGNED in allowed

    def test_status_values(self):
        """确认所有状态值可正确序列化/反序列化。"""
        for status in PipelineStatus:
            assert PipelineStatus(status.value) == status


# ─────────────────────────────────────────────
# 3. PipelineStore CRUD 测试
# ─────────────────────────────────────────────

class TestPipelineStore:
    def test_save_and_load(self, store, sample_record):
        store.save(sample_record)
        loaded = store.load(sample_record.record_id)
        assert loaded is not None
        assert loaded.institution_name == "迪策资本"

    def test_load_nonexistent_returns_none(self, store):
        result = store.load("nonexistent_id")
        assert result is None

    def test_list_records_empty(self, store):
        records = store.list_records()
        assert records == []

    def test_list_records_after_save(self, store, sample_record):
        store.save(sample_record)
        records = store.list_records()
        assert len(records) == 1
        assert records[0].record_id == sample_record.record_id

    def test_list_filter_by_company(self, store):
        r1 = PipelineRecord(
            record_id="r1", institution_id="dikce", institution_name="迪策",
            company_id="zetian", company_name="泽天智航",
            status=PipelineStatus.INITIAL_CONTACT,
        )
        r2 = PipelineRecord(
            record_id="r2", institution_id="fuchuang", institution_name="福创投",
            company_id="zetian", company_name="泽天智航",
            status=PipelineStatus.NDA_SIGNED,
        )
        r3 = PipelineRecord(
            record_id="r3", institution_id="dikce", institution_name="迪策",
            company_id="other_company", company_name="其他公司",
            status=PipelineStatus.INITIAL_CONTACT,
        )
        for r in [r1, r2, r3]:
            store.save(r)

        zetian_records = store.list_records(company_id="zetian")
        assert len(zetian_records) == 2
        ids = {r.record_id for r in zetian_records}
        assert "r1" in ids
        assert "r2" in ids
        assert "r3" not in ids

    def test_delete_record(self, store, sample_record):
        store.save(sample_record)
        store.delete(sample_record.record_id)
        assert store.load(sample_record.record_id) is None

    def test_atomic_write_prevents_corruption(self, store, sample_record):
        """多次写入同一记录不应损坏数据。"""
        for i in range(5):
            sample_record.add_event(f"事件{i}")
            store.save(sample_record)
        loaded = store.load(sample_record.record_id)
        assert len(loaded.timeline) == 5

    def test_pipeline_dir_created_automatically(self, tmp_path):
        new_dir = str(tmp_path / "new" / "pipeline")
        store = PipelineStore(pipeline_dir=new_dir)
        record = PipelineRecord(
            record_id="x1", institution_id="a", institution_name="A",
            company_id="b", company_name="B",
            status=PipelineStatus.INITIAL_CONTACT,
        )
        store.save(record)
        assert Path(new_dir).exists()


# ─────────────────────────────────────────────
# 4. Pipeline 统计测试
# ─────────────────────────────────────────────

class TestPipelineStats:
    def test_get_summary(self, store):
        """统计各状态的记录数量。"""
        statuses = [
            PipelineStatus.INITIAL_CONTACT,
            PipelineStatus.INITIAL_CONTACT,
            PipelineStatus.NDA_SIGNED,
            PipelineStatus.DD_IN_PROGRESS,
            PipelineStatus.CLOSED_WON,
        ]
        for i, s in enumerate(statuses):
            r = PipelineRecord(
                record_id=f"r{i}", institution_id=f"fund{i}", institution_name=f"基金{i}",
                company_id="zetian", company_name="泽天智航",
                status=s,
            )
            store.save(r)

        summary = store.get_summary(company_id="zetian")
        assert summary[PipelineStatus.INITIAL_CONTACT] == 2
        assert summary[PipelineStatus.NDA_SIGNED] == 1
        assert summary[PipelineStatus.CLOSED_WON] == 1

    def test_get_funnel_summary_accumulates_historical_stage(self, store):
        """
        漏斗统计应表示「历史上到达过该阶段」而非「当前停留阶段」。
        """
        nda_only = PipelineRecord(
            record_id="f1",
            institution_id="fund_1",
            institution_name="基金1",
            company_id="zetian",
            company_name="泽天智航",
            status=PipelineStatus.NDA_SIGNED,
        )
        materials = PipelineRecord(
            record_id="f2",
            institution_id="fund_2",
            institution_name="基金2",
            company_id="zetian",
            company_name="泽天智航",
            status=PipelineStatus.MATERIALS_SENT,
        )
        won = PipelineRecord(
            record_id="f3",
            institution_id="fund_3",
            institution_name="基金3",
            company_id="zetian",
            company_name="泽天智航",
            status=PipelineStatus.CLOSED_WON,
        )
        lost_after_interview = PipelineRecord(
            record_id="f4",
            institution_id="fund_4",
            institution_name="基金4",
            company_id="zetian",
            company_name="泽天智航",
            status=PipelineStatus.CLOSED_LOST,
            timeline=[
                TimelineEntry(date="2026-04-01", action=PipelineStatus.NDA_SIGNED.value, note="签NDA"),
                TimelineEntry(date="2026-04-02", action=PipelineStatus.MATERIALS_SENT.value, note="发材料"),
                TimelineEntry(date="2026-04-03", action=PipelineStatus.DD_IN_PROGRESS.value, note="尽调"),
                TimelineEntry(date="2026-04-04", action=PipelineStatus.INTERVIEW_STAGE.value, note="访谈"),
                TimelineEntry(date="2026-04-05", action=PipelineStatus.CLOSED_LOST.value, note="放弃"),
            ],
        )
        lost_without_timeline = PipelineRecord(
            record_id="f5",
            institution_id="fund_5",
            institution_name="基金5",
            company_id="zetian",
            company_name="泽天智航",
            status=PipelineStatus.CLOSED_LOST,
        )
        for r in [nda_only, materials, won, lost_after_interview, lost_without_timeline]:
            store.save(r)

        funnel = store.get_funnel_summary(company_id="zetian")
        assert funnel[PipelineStatus.INITIAL_CONTACT] == 4
        assert funnel[PipelineStatus.NDA_SIGNED] == 4
        assert funnel[PipelineStatus.MATERIALS_SENT] == 3
        assert funnel[PipelineStatus.DD_IN_PROGRESS] == 2
        assert funnel[PipelineStatus.INTERVIEW_STAGE] == 2
        assert funnel[PipelineStatus.TS_NEGOTIATION] == 1
