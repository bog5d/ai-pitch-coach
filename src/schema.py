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
    improvement_suggestion: str = Field(..., description="综合给出的金句级话术建议")
    start_word_index: int = Field(..., description="翻车片段开始的词汇索引")
    end_word_index: int = Field(..., description="翻车片段结束的词汇索引")

class AnalysisReport(BaseModel):
    scene_analysis: SceneAnalysis = Field(..., description="对全局场景的深度剖析")
    total_score: int = Field(..., description="综合打分 (0-100)")
    risk_points: List[RiskPoint] = Field(default_factory=list, description="所有踩坑点列表")
