"""Pydantic 数据契约层 — 仓库发版 V7.5（与根目录 build_release.py → CURRENT_VERSION 对齐）。"""

from pydantic import BaseModel, Field
from typing import List, Literal
from uuid import uuid4


class TranscriptionWord(BaseModel):
    word_index: int = Field(..., description="全局唯一索引")
    text: str = Field(..., description="词汇")
    start_time: float = Field(..., description="开始时间")
    end_time: float = Field(..., description="结束时间")
    speaker_id: str = Field(..., description="说话人")


class SceneAnalysis(BaseModel):
    scene_type: str = Field(..., description="推断的沟通场景，如：首次VC路演、尽调答疑等")
    speaker_roles: str = Field(..., description="推断的双方身份背景及现场氛围")


class RiskTargetCandidate(BaseModel):
    """V9.6 阶段一：全文扫描得到的风险靶点（不含完整 Tier 与话术）。"""

    start_word_index: int = Field(..., description="靶点起始词索引（含）")
    end_word_index: int = Field(..., description="靶点结束词索引（含）")
    problem_description: str = Field(..., description="该靶点问题摘要，供阶段二深评使用")
    risk_type: str = Field(
        ...,
        description="风险类型标签，如：口径偏离、逻辑断裂、数据含糊、狙击清单命中等",
    )


class RiskScanResult(BaseModel):
    """V9.6 阶段一输出：场景速写 + 风险靶点列表。"""

    scene_analysis: SceneAnalysis = Field(..., description="全局场景推断")
    targets: List[RiskTargetCandidate] = Field(
        default_factory=list,
        description="待阶段二逐个点名深评的靶点",
    )


class MagicRefinementResult(BaseModel):
    """V9.6「魔法对话框」后端：按用户指令重写单条改进建议的出参。"""

    risk_point_id: str = Field(..., description="与前端/会话稳定绑定的风险点标识")
    improvement_suggestion: str = Field(..., description="重写后的 improvement_suggestion 正文")


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
            "该片段在逐字稿中对应范围的实录占位。"
            "严禁书面化润色，严禁混入 QA，必须 100% 忠实于底层转写。"
            "此字段在后端将进行严格的索引一致性校验并物理覆写！"
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
    needs_refinement: bool = Field(
        default=False,
        description="主理人在审查台标记需 AI 精炼的条目；LLM 输出时必须为 false",
    )
    refinement_note: str = Field(
        default="",
        description="主理人给精炼 LLM 的批示意见；LLM 输出时必须为空字符串",
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


class CompanyProfile(BaseModel):
    company_id: str = Field(..., description="公司唯一标识，建议用拼音/英文slug")
    display_name: str = Field(..., description="展示名称，如「ABC 资本」")
    uuid: str = Field(default_factory=lambda: str(uuid4()), description="系统级唯一标识符，用于跨系统追踪")
    background: str = Field(default="", description="公司常态化背景，注入 LLM Prompt")
    created_at: str = Field(default="", description="ISO 8601 创建时间")
    updated_at: str = Field(default="", description="ISO 8601 最后更新时间")


class ExecutiveMemory(BaseModel):
    """V8.6 高管「错题本」单条记忆：原始表述与纠正口径，供动态长记忆引擎检索注入。"""

    uuid: str = Field(default_factory=lambda: str(uuid4()), description="条目唯一标识")
    tag: str = Field(
        ...,
        description="高管/场景标签，用于 JSON 分桶（如 zhang_zong、李总）；与落盘文件名安全化规则无关字段值本身",
    )
    raw_text: str = Field(..., description="原始表述（易踩坑实录）")
    correction: str = Field(..., description="纠正建议或标准口径")
    weight: float = Field(
        default=1.0,
        ge=0.0,
        description="记忆强度或召回权重，供后续排序/衰减使用",
    )
    risk_type: str = Field(
        default="",
        description="来源风险点等级（严重/一般/轻微），供看板「高频雷区」聚合；空表示未标注",
    )
    updated_at: str = Field(
        default="",
        description="ISO 8601 最后触发时间：创建时写入，被主评 Prompt 命中时刷新",
    )
    hit_count: int = Field(
        default=0,
        ge=0,
        description="被注入主评 Prompt 的累计命中次数，供时间衰减与僵尸清理",
    )
