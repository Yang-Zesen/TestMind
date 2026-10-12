from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ActiveVersionScope(BaseModel):
    """Document and index version selected by the PostgreSQL active mapping."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_version: str = Field(min_length=1)
    index_version: str = Field(min_length=1)


class RetrievalScope(BaseModel):
    """Server-derived scope for a single project knowledge query."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    knowledge_base_ids: frozenset[str] = Field(default_factory=frozenset)
    active_versions: dict[str, ActiveVersionScope] = Field(default_factory=dict)
    # Legacy server-supplied scope remains supported while middleware is migrated.
    document_versions: dict[str, str] = Field(default_factory=dict)


class KnowledgeChunk(BaseModel):
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    document_version: str = Field(min_length=1)
    index_version: str = ""
    knowledge_base_id: str = ""
    project_id: str = Field(min_length=1)
    title: str = ""
    content: str = Field(min_length=1)
    source_locator: str = ""
    page: int | None = None
    section: str | None = None
    relevance: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)


class GroundedAnswer(BaseModel):
    """LLM output for an answer that must cite returned chunk IDs."""

    answer: str = ""
    cited_chunk_ids: list[str] = Field(default_factory=list)
    insufficient_evidence: bool = False
    model_config = ConfigDict(frozen=True, extra="forbid")
