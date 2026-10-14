"""Shared evidence graph, independent of intent and answer generation."""

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from testmind.core.config import get_config
from testmind.infrastructure.milvus.retrieval import Retriever
from testmind.models.retrieval import EvidenceBundle, KnowledgeChunk, RetrievalScope

ScopeLoader = Callable[[RetrievalScope], Awaitable[RetrievalScope]]


class RetrievalState(TypedDict, total=False):
    query: str
    caller_id: str
    scope: RetrievalScope
    limit: int
    bundle: EvidenceBundle


class SharedRetrievalService:
    def __init__(self, backend: Retriever, *, refresh_scope: ScopeLoader | None = None):
        self.backend = backend
        self.refresh_scope = refresh_scope
        graph = StateGraph(RetrievalState)
        graph.add_node("retrieve_evidence", self._retrieve)
        graph.add_edge(START, "retrieve_evidence")
        graph.add_edge("retrieve_evidence", END)
        self.graph = graph.compile()

    async def current_scope(self, scope: RetrievalScope) -> RetrievalScope:
        current = await self.refresh_scope(scope) if self.refresh_scope else scope
        if current.user_id != scope.user_id or current.project_id != scope.project_id:
            raise PermissionError("retrieval_scope_changed")
        return current

    @staticmethod
    def allowed(chunk: KnowledgeChunk, scope: RetrievalScope) -> bool:
        active = scope.active_versions.get(chunk.document_id)
        matches = (
            active is not None and active.document_version == chunk.document_version
            and active.index_version == chunk.index_version
            if scope.active_versions else scope.document_versions.get(chunk.document_id) == chunk.document_version
        )
        return bool(matches and chunk.project_id == scope.project_id and (
            not scope.knowledge_base_ids or chunk.knowledge_base_id in scope.knowledge_base_ids
        ) and chunk.source_locator.strip() and chunk.content.strip())

    async def _retrieve(self, state: RetrievalState) -> dict[str, EvidenceBundle]:
        scope = await self.current_scope(state["scope"])
        chunks = await self.backend.retrieve(state["query"], scope, limit=state["limit"])
        current = await self.current_scope(scope)
        verified: dict[str, KnowledgeChunk] = {}
        for raw in chunks:
            chunk = KnowledgeChunk.model_validate(raw)
            if self.allowed(chunk, current) and chunk.relevance >= get_config().retrieval_min_relevance:
                verified.setdefault(chunk.chunk_id, chunk.model_copy(update={"content": chunk.content[:3500]}))
        selected = list(verified.values())[:state["limit"]]
        return {"bundle": EvidenceBundle(caller_id=state["caller_id"], user_id=current.user_id, project_id=current.project_id, status="retrieved" if selected else "insufficient_evidence", chunks=selected)}

    async def evidence(self, query: str, scope: RetrievalScope, *, caller_id: str, limit: int = 5) -> EvidenceBundle:
        if not query.strip() or len(query) > 6000 or not 1 <= limit <= 20:
            raise ValueError("invalid_retrieval_request")
        state: RetrievalState = {"query": query, "scope": scope, "caller_id": caller_id, "limit": limit}
        result: dict[str, Any] = await self.graph.ainvoke(state)
        return EvidenceBundle.model_validate(result["bundle"])

    async def retrieve(self, query: str, scope: RetrievalScope, *, limit: int = 5) -> list[KnowledgeChunk]:
        return (await self.evidence(query, scope, caller_id=scope.user_id, limit=limit)).chunks
