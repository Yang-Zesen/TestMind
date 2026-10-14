"""Parent state: routing, conversation, and outputs, not subgraph working data."""

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

Intent = Literal["dialogue", "test_case_generation", "script_generation", "graph_query", "clarify", "readonly_query"]


class GraphState(TypedDict, total=False):
    task_id: str
    attempt_id: str
    user_id: str
    project_id: str
    session_id: str
    trace_id: str
    created_at: str
    updated_at: str
    status: str
    current_stage: str
    retry_count: int
    raw_query: str
    corrected_query: str
    resolved_query: str
    attachment_refs: list[dict[str, Any]]
    messages: Annotated[list[AnyMessage], add_messages]
    correction_detail: dict[str, Any]
    coreference_detail: dict[str, Any]
    intent: Intent
    answer_mode: Literal["direct", "retrieval"] | None
    intent_confidence: float
    entities: dict[str, Any]
    route_reason: str
    missing_fields: list[str]
    clarification_question: str
    resume_payload: str
    answer: str
    citations: list[dict[str, Any]]
    insufficient_evidence: bool
    last_error: dict[str, Any]
