from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected", "modified"]
    asset_version: int = Field(ge=1)
    content_hash: str = Field(min_length=1, max_length=128)
