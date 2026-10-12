from pydantic import BaseModel, Field


class ReferenceItem(BaseModel):
    expression: str = Field(description="当前问题中的指代表达, 例如: 它, 这个, 上面的脚本")
    referent: str = Field(description="根据历史对话推断出的明确对象, 例如: 脚本生成Agent")
    evidence: str = Field(description="支持该指代关系的历史对话片段")


class CoreferenceResult(BaseModel):
    resolved_query: str = Field(
        description="结合当前会话历史，将当前问题改写为指代明确、可独立理解的问题；若无需消解或无法确定指代对象，则保留原问题"
    )
    has_coreference: bool = Field(
        description="当前问题是否存在需要结合历史对话才能理解的指代或省略"
    )
    references: list[dict] = Field(
        default_factory=list,
        description="已消解的指代关系；每项记录原文中的指代表达及其对应的明确对象。无法确定时返回空列表"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="指代消解结果的自评置信度，范围为 0 到 1；无法可靠确定对象时应给低分"
    )
    explanation: str = Field(
        default="",
        description="简要说明依据哪段会话历史完成消解；未改写或无法确定时说明原因"
    )