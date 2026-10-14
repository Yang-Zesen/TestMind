import hashlib
import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field

from testmind.core.config import get_config
from testmind.core.security import AuthContext, get_auth_context
from testmind.services.request_context import (
    ContextAccessDenied,
    ContextUnavailable,
    RequestContextService,
)

router = APIRouter()


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=6000)
    session_id: str = Field(min_length=1, max_length=200)
    project_id: str | None = Field(default=None, min_length=1, max_length=200)


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    request: Request,
    auth_context: AuthContext = Depends(get_auth_context),  # noqa: B008 - FastAPI dependency declaration
) -> dict[str, Any]:
    service = RequestContextService(
        getattr(request.app.state, "knowledge_repository", None),
        timeout_seconds=get_config().retrieval_timeout_seconds,
    )
    try:
        context = await service.build(
            auth_context,
            project_id=payload.project_id,
            injected_scope=getattr(request.state, "retrieval_scope", None),
        )
    except ContextAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except ContextUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    scope = context.retrieval_scope
    user_id = auth_context.user_id
    project_id = scope.project_id if scope else (payload.project_id or "")
    graph = request.app.state.graph
    thread_id = hashlib.sha256(
        json.dumps([user_id, project_id, payload.session_id], ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    result = await graph.ainvoke(
        {
            "raw_query": payload.query,
            "messages": [HumanMessage(content=payload.query)],
            "session_id": payload.session_id,
            "user_id": user_id,
            "project_id": project_id,
            "trace_id": str(uuid4()),
            "retry_count": 0,
            "answer": "",
            "answer_mode": None,
            "citations": [],
            "last_error": {},
            "clarification_question": "",
            "status": "running",
        },
        config={"configurable": {"thread_id": thread_id}},
        context=context,
    )

    return {
        "answer": result.get("answer", ""),
        "intent": result.get("intent", "clarify"),
        "answer_mode": result.get("answer_mode"),
        "citations": result.get("citations", []),
        "insufficient_evidence": result.get("insufficient_evidence", False),
        "status": result.get("status", "failed"),
        "intent_confidence": result.get("intent_confidence", 0.0),
        "resolved_query": result.get("resolved_query", payload.query),
        "current_stage": result.get("current_stage", ""),
    }
