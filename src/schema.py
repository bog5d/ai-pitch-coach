"""Pydantic 数据契约层 — 仓库发版 V7.0（与根目录 build_release.py → CURRENT_VERSION 对齐）。"""

from pydantic import BaseModel, Field
from typing import List, Literal


class TranscriptionWord(BaseModel):
    word_index: int = Field(..., description="全局唯一索引")
    text: str = Field(..., description="词汇")
    start_time: float = Field(..., description="开始时间")
    end_time: float = Field(..., description="结束时间")
    speaker_id: str = Field(..., description="说话人")


class SceneAnalysis(BaseModel):
    scene_type: str = Field(..., description="推断的沟通场景，如：首次VC路演、尽调答疑等")
    speaker_roles: str = Field(..., description="推断的双方身份背景及现场氛围")


class RiskPoint(BaseModel):
    risk_level: Literal["严重", "一般", "轻微"] = Field(..., description="踩坑严重程度")
    tier1_general_critique: str = Field(..., description="第一层(顶尖视角): 商业逻辑致命伤和隐患")
    tier2_qa_alignment: str = Field(..., description="第二层(QA对齐): 是否违背公司口径或QA需更新")
    improvement_suggestion: str = Field(
        ...,
        description="针对该翻车片段，给【发言人】的具体话术改进建议，指导其如何更好地应对此类问题。",
    )
    original_text: str = Field(
        default="",
        description=(
            "提取该片段对应的原文。必须进行去口语化的轻度书面化润色（剔除嗯/啊等废话，保留原意）。"
            "严禁在此字段加入任何点评或转述，保持对话格式纯净。"
        ),
    )
    start_word_index: int = Field(..., description="翻车片段开始的词汇索引")
    end_word_index: int = Field(..., description="翻车片段结束的词汇索引")
    score_deduction: int = Field(
        default=0,
        description="该风险点的扣分值 (例如 2, 5, 10)",
    )
    deduction_reason: str = Field(
        default="",
        description="扣分原因：须结合参考QA说明偏离了哪些核心口径；得分低时必填要点",
    )
    is_manual_entry: bool = Field(
        default=False,
        description="人工在审查台增补的条目，可无词级音频切片",
    )


class AnalysisReport(BaseModel):
    scene_analysis: SceneAnalysis = Field(..., description="对全局场景的深度剖析")
    total_score: int = Field(
        ...,
        description=(
            "综合打分 (0-100)。必须基于 100 分满分，减去所有 risk_points 的扣分总和得出。请严格计算！"
        ),
    )
    total_score_deduction_reason: str = Field(
        default="",
        description="总分层面的扣分说明：结合QA与整体表现简述为何不是满分",
    )
    risk_points: List[RiskPoint] = Field(default_factory=list, description="所有踩坑点列表")
