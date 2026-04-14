"""
pipeline_tracker.py — 融资过程 CRM v1.0 (Sprint 4)

追踪每家投资机构的接触进度，支持完整的融资漏斗管理。

状态机：
  初步接触 → NDA签署 → 材料发送 → 尽调启动 → 访谈进行 → TS谈判 → 关单成功/关单失败

设计原则：
  - JSON 文件持久化（每条记录一个文件，方便 git 追踪）
  - 原子写入（先写 .tmp，再 os.replace）
  - 纯标准库，零依赖
  - 不抛异常，失败静默降级
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────
# 状态枚举
# ─────────────────────────────────────────────

class PipelineStatus(str, Enum):
    INITIAL_CONTACT = "初步接触"
    NDA_SIGNED      = "NDA签署"
    MATERIALS_SENT  = "材料发送"
    DD_IN_PROGRESS  = "尽调启动"
    INTERVIEW_STAGE = "访谈进行"
    TS_NEGOTIATION  = "TS谈判"
    CLOSED_WON      = "关单成功"
    CLOSED_LOST     = "关单失败/放弃"


FUNNEL_STAGES: list[PipelineStatus] = [
    PipelineStatus.INITIAL_CONTACT,
    PipelineStatus.NDA_SIGNED,
    PipelineStatus.MATERIALS_SENT,
    PipelineStatus.DD_IN_PROGRESS,
    PipelineStatus.INTERVIEW_STAGE,
    PipelineStatus.TS_NEGOTIATION,
]
_FUNNEL_STAGE_INDEX = {status: idx for idx, status in enumerate(FUNNEL_STAGES)}


# 合法状态转换图（允许向后跳级，但不允许从关单倒退）
VALID_STATUS_TRANSITIONS: dict[PipelineStatus, list[PipelineStatus]] = {
    PipelineStatus.INITIAL_CONTACT: [
        PipelineStatus.NDA_SIGNED,
        PipelineStatus.MATERIALS_SENT,
        PipelineStatus.CLOSED_LOST,
    ],
    PipelineStatus.NDA_SIGNED: [
        PipelineStatus.MATERIALS_SENT,
        PipelineStatus.DD_IN_PROGRESS,
        PipelineStatus.CLOSED_LOST,
    ],
    PipelineStatus.MATERIALS_SENT: [
        PipelineStatus.DD_IN_PROGRESS,
        PipelineStatus.CLOSED_LOST,
    ],
    PipelineStatus.DD_IN_PROGRESS: [
        PipelineStatus.INTERVIEW_STAGE,
        PipelineStatus.TS_NEGOTIATION,
        PipelineStatus.CLOSED_LOST,
    ],
    PipelineStatus.INTERVIEW_STAGE: [
        PipelineStatus.TS_NEGOTIATION,
        PipelineStatus.CLOSED_LOST,
    ],
    PipelineStatus.TS_NEGOTIATION: [
        PipelineStatus.CLOSED_WON,
        PipelineStatus.CLOSED_LOST,
    ],
    PipelineStatus.CLOSED_WON: [],   # 终态
    PipelineStatus.CLOSED_LOST: [],  # 终态
}


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class TimelineEntry:
    """时间线上的单个事件。"""
    date: str       # YYYY-MM-DD
    action: str     # 事件类型，如 "初次路演"
    note: str = ""  # 备注


@dataclass
class PipelineRecord:
    """一条机构-公司的 Pipeline 记录。"""
    record_id: str               # 唯一标识，建议用 "{institution_id}_{company_id}_{yymmdd}"
    institution_id: str
    institution_name: str
    company_id: str
    company_name: str
    status: PipelineStatus
    contacts: list[dict] = field(default_factory=list)         # [{"name": "李志新", "title": "合伙人"}]
    timeline: list[TimelineEntry] = field(default_factory=list)
    next_action: str = ""
    linked_interviews: list[str] = field(default_factory=list) # AI Coach 访谈文件名
    notes: str = ""

    def add_event(self, note: str, action: str = "") -> None:
        """追加时间线事件。"""
        today = date.today().isoformat()
        if not action:
            action = self.status.value
        self.timeline.append(TimelineEntry(date=today, action=action, note=note))

    def update_status(self, new_status: PipelineStatus, note: str = "") -> None:
        """更新状态，并自动写入时间线。"""
        old = self.status.value
        self.status = new_status
        msg = note or f"状态变更：{old} → {new_status.value}"
        self.add_event(msg, action=new_status.value)

    def link_interview(self, stem: str) -> None:
        """关联 AI Coach 访谈记录（文件 stem）。"""
        if stem not in self.linked_interviews:
            self.linked_interviews.append(stem)

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "institution_id": self.institution_id,
            "institution_name": self.institution_name,
            "company_id": self.company_id,
            "company_name": self.company_name,
            "status": self.status.value,
            "contacts": self.contacts,
            "timeline": [
                {"date": e.date, "action": e.action, "note": e.note}
                for e in self.timeline
            ],
            "next_action": self.next_action,
            "linked_interviews": self.linked_interviews,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineRecord":
        timeline = [
            TimelineEntry(date=e["date"], action=e.get("action", ""), note=e.get("note", ""))
            for e in (data.get("timeline") or [])
        ]
        return cls(
            record_id=data["record_id"],
            institution_id=data.get("institution_id", ""),
            institution_name=data.get("institution_name", ""),
            company_id=data.get("company_id", ""),
            company_name=data.get("company_name", ""),
            status=PipelineStatus(data.get("status", PipelineStatus.INITIAL_CONTACT.value)),
            contacts=data.get("contacts") or [],
            timeline=timeline,
            next_action=data.get("next_action", ""),
            linked_interviews=data.get("linked_interviews") or [],
            notes=data.get("notes", ""),
        )


# ─────────────────────────────────────────────
# 持久化存储
# ─────────────────────────────────────────────

class PipelineStore:
    """
    Pipeline 记录的 JSON 文件存储。
    每条记录存为 {pipeline_dir}/{record_id}.json。
    使用原子写入，防止数据损坏。
    """

    def __init__(self, pipeline_dir: str) -> None:
        self.pipeline_dir = Path(pipeline_dir)

    def _ensure_dir(self) -> None:
        self.pipeline_dir.mkdir(parents=True, exist_ok=True)

    def _record_path(self, record_id: str) -> Path:
        # 安全化 record_id，防止路径穿越
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in record_id)
        return self.pipeline_dir / f"{safe_id}.json"

    def save(self, record: PipelineRecord) -> bool:
        """原子写入记录。失败返回 False，不抛异常。"""
        try:
            self._ensure_dir()
            target = self._record_path(record.record_id)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(str(tmp), str(target))
            return True
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    def load(self, record_id: str) -> Optional[PipelineRecord]:
        """读取记录。不存在或解析失败时返回 None。"""
        try:
            path = self._record_path(record_id)
            data = json.loads(path.read_text(encoding="utf-8"))
            return PipelineRecord.from_dict(data)
        except (FileNotFoundError, json.JSONDecodeError, OSError, KeyError, ValueError):
            return None

    def list_records(
        self,
        company_id: Optional[str] = None,
        institution_id: Optional[str] = None,
        status: Optional[PipelineStatus] = None,
    ) -> list[PipelineRecord]:
        """
        列出所有记录，支持按公司/机构/状态过滤。
        解析失败的文件静默跳过。
        """
        if not self.pipeline_dir.exists():
            return []

        records: list[PipelineRecord] = []
        for p in self.pipeline_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                r = PipelineRecord.from_dict(data)
                if company_id and r.company_id != company_id:
                    continue
                if institution_id and r.institution_id != institution_id:
                    continue
                if status and r.status != status:
                    continue
                records.append(r)
            except Exception:
                continue

        records.sort(key=lambda r: r.timeline[-1].date if r.timeline else "", reverse=True)
        return records

    def delete(self, record_id: str) -> bool:
        """删除记录。文件不存在时返回 False，不抛异常。"""
        try:
            path = self._record_path(record_id)
            if path.exists():
                path.unlink()
                return True
            return False
        except OSError:
            return False

    def get_summary(
        self, company_id: Optional[str] = None
    ) -> dict[PipelineStatus, int]:
        """统计各状态的记录数量。"""
        records = self.list_records(company_id=company_id)
        summary: dict[PipelineStatus, int] = {s: 0 for s in PipelineStatus}
        for r in records:
            summary[r.status] = summary.get(r.status, 0) + 1
        return summary

    def _funnel_max_stage_index(self, record: PipelineRecord) -> int:
        """
        返回该记录在漏斗中历史到达的最高阶段索引。
        - CLOSED_WON 视为通过全部阶段
        - CLOSED_LOST 尝试从 timeline 回溯最高阶段
        - 缺失可识别历史时返回 -1（不计入漏斗）
        """
        if record.status == PipelineStatus.CLOSED_WON:
            return len(FUNNEL_STAGES) - 1

        direct_idx = _FUNNEL_STAGE_INDEX.get(record.status)
        if direct_idx is not None:
            return direct_idx

        if record.status != PipelineStatus.CLOSED_LOST:
            return -1

        max_idx = -1
        for event in record.timeline:
            action = (event.action or "").strip()
            if not action:
                continue
            try:
                event_status = PipelineStatus(action)
            except ValueError:
                continue

            if event_status == PipelineStatus.CLOSED_WON:
                return len(FUNNEL_STAGES) - 1
            idx = _FUNNEL_STAGE_INDEX.get(event_status)
            if idx is not None and idx > max_idx:
                max_idx = idx
        return max_idx

    def get_funnel_summary(
        self, company_id: Optional[str] = None
    ) -> dict[PipelineStatus, int]:
        """
        漏斗统计：统计「历史上曾到达过该阶段」的记录数量。
        例如：当前在「材料发送」的项目，会同时计入「初步接触/NDA签署/材料发送」。
        """
        records = self.list_records(company_id=company_id)
        summary: dict[PipelineStatus, int] = {s: 0 for s in FUNNEL_STAGES}
        for record in records:
            max_idx = self._funnel_max_stage_index(record)
            if max_idx < 0:
                continue
            for status in FUNNEL_STAGES[: max_idx + 1]:
                summary[status] += 1
        return summary


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────

def get_default_store(workspace_root: str) -> PipelineStore:
    """返回默认的 PipelineStore（{workspace_root}/.pipeline/）。"""
    return PipelineStore(pipeline_dir=str(Path(workspace_root) / ".pipeline"))


def format_pipeline_overview(records: list[PipelineRecord]) -> str:
    """格式化 Pipeline 总览为易读文本。"""
    if not records:
        return "暂无 Pipeline 记录。"

    status_emoji = {
        PipelineStatus.INITIAL_CONTACT: "📞",
        PipelineStatus.NDA_SIGNED:      "📝",
        PipelineStatus.MATERIALS_SENT:  "📦",
        PipelineStatus.DD_IN_PROGRESS:  "🔍",
        PipelineStatus.INTERVIEW_STAGE: "🎙️",
        PipelineStatus.TS_NEGOTIATION:  "💼",
        PipelineStatus.CLOSED_WON:      "🏆",
        PipelineStatus.CLOSED_LOST:     "❌",
    }

    lines = [
        "=" * 54,
        "   融资 Pipeline 总览",
        "=" * 54,
        f"共 {len(records)} 条记录",
        "",
    ]
    for r in records:
        emoji = status_emoji.get(r.status, "•")
        last_date = r.timeline[-1].date if r.timeline else "—"
        lines.append(
            f"{emoji} {r.institution_name:12s}  [{r.status.value:8s}]  "
            f"最近动态：{last_date}"
        )
        if r.next_action:
            lines.append(f"   下一步：{r.next_action}")
    lines.append("=" * 54)
    return "\n".join(lines)
