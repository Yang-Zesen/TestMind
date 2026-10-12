"""One dialogue subgraph; mode selection never calls another classifier."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError

from testmind.agents.general_chat import GeneralChatNode
from testmind.core.config import get_config
from testmind.core.logging import get_logger
from testmind.infrastructure.llm.llm_client import get_llm_client
from testmind.infrastructure.milvus.retrieval import MilvusRetrievalService, Retriever
from testmind.models.retrieval import GroundedAnswer, KnowledgeChunk
from testmind.orchestration.context import RequestContext
from testmind.prompts.load_prompts import load_template
from testmind.tools.update_time import _now


class DialogueInput(TypedDict, total=False):
    raw_query: str
    corrected_query: str
    resolved_query: str
    user_id: str
    project_id: str
    messages: list[AnyMessage]
    answer_mode: Literal["direct", "retrieval"] | None


class DialogueOutput(TypedDict, total=False):
    answer: str
    citations: list[dict]
    insufficient_evidence: bool
    status: str
    current_stage: str
    last_error: dict
    messages: list[AnyMessage]
    updated_at: str


class DialogueState(DialogueInput, DialogueOutput, total=False):
    retrieved_chunks: list[dict]


_LOGGER = get_logger(__name__)
_GROUNDED_SYSTEM = load_template("grounded_answer.txt")
_INSUFFICIENT = "没有找到当前权限和版本范围内足以支持回答的证据，请补充资料或缩小问题范围。"


def _query(state: DialogueState) -> str:
    return (
        state.get("resolved_query") or state.get("corrected_query") or state.get("raw_query") or ""
    ).strip()


def _result(answer: str, status: str, stage: str, *, code: str | None = None) -> dict[str, Any]:
    return {
        "answer": answer,
        "citations": [],
        "insufficient_evidence": status == "insufficient_evidence",
        "status": status,
        "current_stage": stage,
        "last_error": {"code": code} if code else {},
    }


def create_dialogue_graph(
    retriever: Retriever | None = None,
    *,
    llm: Any | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    timeout_seconds: float | None = None,
) -> CompiledStateGraph:
    config = get_config()
    retriever = retriever if retriever is not None else MilvusRetrievalService(config)
    llm = llm if llm is not None else get_llm_client(response_format=False)
    direct_node = GeneralChatNode(llm=llm)
    retrieval_timeout = (
        timeout_seconds if timeout_seconds is not None else config.retrieval_timeout_seconds
    )
    model_timeout = (
        timeout_seconds if timeout_seconds is not None else config.dialogue_timeout_seconds
    )
    if retrieval_timeout <= 0 or model_timeout <= 0:
        raise ValueError("Dialogue timeouts must be positive")

    def initialize(state: DialogueState) -> dict[str, Any]:
        # Each request starts fresh, even when this subgraph has checkpoints.
        return {**_result("", "running", "dialogue"), "retrieved_chunks": []}

    def route_mode(state: DialogueState) -> str:
        if not _query(state):
            return "needs_input"
        mode = state.get("answer_mode")
        return mode if mode in ("direct", "retrieval") else "needs_input"

    async def direct_answer(state: DialogueState) -> dict[str, Any]:
        try:
            updates = await asyncio.wait_for(direct_node.process(state), timeout=model_timeout)
            return _result(updates["answer"], "completed", "dialogue_direct")
        except Exception as exc:  # noqa: BLE001 - sanitize provider errors at the workflow boundary.
            _LOGGER.warning("Dialogue direct answer failed (%s)", type(exc).__name__)
            return _result(
                "暂时无法完成回答，请稍后重试。",
                "failed",
                "dialogue_direct",
                code="answer_unavailable",
            )

    async def retrieve(state: DialogueState, runtime: Runtime[RequestContext]) -> dict[str, Any]:
        scope = runtime.context.retrieval_scope if runtime.context else None
        if (
            scope is None
            or not scope.document_versions
            or scope.user_id != state.get("user_id")
            or scope.project_id != state.get("project_id")
        ):
            return _result(
                "缺少经过服务端授权的项目知识范围，请先选择有权限的项目与资料。",
                "needs_input",
                "dialogue_retrieval",
                code="retrieval_scope_required",
            )
        try:
            chunks = await asyncio.wait_for(
                retriever.retrieve(_query(state), scope, limit=5), timeout=retrieval_timeout
            )
            verified: dict[str, dict] = {}
            for raw_chunk in chunks:
                try:
                    chunk = KnowledgeChunk.model_validate(raw_chunk)
                except ValidationError:
                    continue
                if (
                    chunk.project_id == scope.project_id
                    and scope.document_versions.get(chunk.document_id) == chunk.document_version
                    and chunk.relevance >= config.retrieval_min_relevance
                    and chunk.content.strip()
                    and chunk.source_locator.strip()
                ):
                    verified.setdefault(
                        chunk.chunk_id, {**chunk.model_dump(), "content": chunk.content[:3500]}
                    )
                if len(verified) >= 5:
                    break
            return {"retrieved_chunks": list(verified.values()), "status": "retrieved"}
        except Exception as exc:  # noqa: BLE001 - sanitize backend errors at the workflow boundary.
            _LOGGER.warning("Dialogue retrieval failed (%s)", type(exc).__name__)
            return _result(
                "项目知识检索暂不可用，请检查检索服务和本地模型配置后重试。",
                "failed",
                "dialogue_retrieval",
                code="retrieval_unavailable",
            )

    def route_evidence(state: DialogueState) -> str:
        if state.get("status") != "retrieved":
            return "publish"
        return "grounded_answer" if state.get("retrieved_chunks") else "insufficient"

    async def grounded_answer(state: DialogueState) -> dict[str, Any]:
        chunks = [KnowledgeChunk.model_validate(item) for item in state.get("retrieved_chunks", [])]
        evidence = [
            {"chunk_id": c.chunk_id, "title": c.title, "content": c.content} for c in chunks
        ]
        try:
            grounded = llm.with_structured_output(GroundedAnswer)
            response = await asyncio.wait_for(
                grounded.ainvoke(
                    [
                        SystemMessage(content=_GROUNDED_SYSTEM),
                        HumanMessage(
                            content=json.dumps(
                                {"question": _query(state), "evidence": evidence},
                                ensure_ascii=False,
                            )
                        ),
                    ]
                ),
                timeout=model_timeout,
            )
            result = GroundedAnswer.model_validate(response)
            cited_ids = list(dict.fromkeys(result.cited_chunk_ids))
            by_id = {chunk.chunk_id: chunk for chunk in chunks}
            if (
                result.insufficient_evidence
                or not result.answer.strip()
                or not cited_ids
                or any(ident not in by_id for ident in cited_ids)
            ):
                return insufficient(state)
            return {
                **_result(result.answer.strip(), "completed", "dialogue_grounded"),
                "citations": [
                    by_id[ident].model_dump(exclude={"content", "relevance"}) for ident in cited_ids
                ],
            }
        except Exception as exc:  # noqa: BLE001 - sanitize provider errors at the workflow boundary.
            _LOGGER.warning("Grounded answer failed (%s)", type(exc).__name__)
            return _result(
                "暂时无法完成项目知识问答，请稍后重试。",
                "failed",
                "dialogue_grounded",
                code="answer_unavailable",
            )

    def insufficient(state: DialogueState) -> dict[str, Any]:
        return _result(_INSUFFICIENT, "insufficient_evidence", "dialogue_retrieval")

    def needs_input(state: DialogueState) -> dict[str, Any]:
        return _result(
            "请补充问题或明确需要普通问答还是项目知识问答。",
            "needs_input",
            "dialogue",
            code="dialogue_context_required",
        )

    def publish(state: DialogueState) -> dict[str, Any]:
        return {"messages": [AIMessage(content=state["answer"])], "updated_at": _now()}

    workflow = StateGraph(
        DialogueState,
        context_schema=RequestContext,
        input_schema=DialogueInput,
        output_schema=DialogueOutput,
    )
    for name, node in {
        "select_mode": initialize,
        "direct_answer": direct_answer,
        "retrieve": retrieve,
        "grounded_answer": grounded_answer,
        "insufficient": insufficient,
        "needs_input": needs_input,
        "publish": publish,
    }.items():
        workflow.add_node(name, node)
    workflow.add_edge(START, "select_mode")
    workflow.add_conditional_edges(
        "select_mode",
        route_mode,
        {
            "direct": "direct_answer",
            "retrieval": "retrieve",
            "needs_input": "needs_input",
        },
    )
    workflow.add_conditional_edges(
        "retrieve",
        route_evidence,
        {
            "grounded_answer": "grounded_answer",
            "insufficient": "insufficient",
            "publish": "publish",
        },
    )
    for node in ("direct_answer", "grounded_answer", "insufficient", "needs_input"):
        workflow.add_edge(node, "publish")
    workflow.add_edge("publish", END)
    return workflow.compile(checkpointer=checkpointer, name="dialogue")
