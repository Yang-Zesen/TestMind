"""One dialogue subgraph; mode selection never calls another classifier."""

from __future__ import annotations

import asyncio
import inspect
import json
from time import monotonic
from typing import Any, Literal, TypedDict, cast

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError

from testmind.agents.general_chat import GeneralChatNode
from testmind.core.config import get_config
from testmind.core.decision_audit import emit_decision_event
from testmind.core.logging import get_logger
from testmind.core.model_recovery import ModelOutputError, invoke_with_recovery
from testmind.infrastructure.llm.llm_client import get_llm_client
from testmind.infrastructure.milvus.retrieval import MilvusRetrievalService, Retriever
from testmind.models.retrieval import GroundedAnswer, KnowledgeChunk
from testmind.orchestration.context import RequestContext
from testmind.orchestration.state import GraphState
from testmind.prompts.load_prompts import load_template
from testmind.tools.update_time import _now


class DialogueInput(TypedDict, total=False):
    task_id: str
    trace_id: str
    attempt_id: str
    raw_query: str
    corrected_query: str
    resolved_query: str
    user_id: str
    project_id: str
    messages: list[AnyMessage]
    answer_mode: Literal["direct", "retrieval"] | None


class DialogueOutput(TypedDict, total=False):
    answer: str
    citations: list[dict[str, Any]]
    insufficient_evidence: bool
    status: str
    current_stage: str
    last_error: dict[str, Any]
    messages: list[AnyMessage]
    updated_at: str


class DialogueState(DialogueInput, DialogueOutput, total=False):
    retrieved_chunks: list[dict[str, Any]]


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
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    timeout_seconds: float | None = None,
) -> CompiledStateGraph[DialogueState, RequestContext, DialogueInput, DialogueOutput]:
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
        if state.get("status") == "failed":
            return "publish"
        if not _query(state):
            return "needs_input"
        mode = state.get("answer_mode")
        return mode if mode in ("direct", "retrieval") else "needs_input"

    async def direct_answer(state: DialogueState) -> dict[str, Any]:
        try:
            updates = await asyncio.wait_for(direct_node(cast(GraphState, state)), timeout=model_timeout)
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
            or scope.user_id != state.get("user_id")
            or scope.project_id != state.get("project_id")
        ):
            return _result(
                "缺少经过服务端授权的项目知识范围，请先选择有权限的项目与资料。",
                "needs_input",
                "dialogue_retrieval",
                code="retrieval_scope_required",
            )
        if not (scope.active_versions or scope.document_versions):
            if scope.knowledge_base_ids:
                return insufficient(state)
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
            verified: dict[str, dict[str, Any]] = {}
            for raw_chunk in chunks:
                try:
                    chunk = KnowledgeChunk.model_validate(raw_chunk)
                except ValidationError:
                    continue
                active = scope.active_versions.get(chunk.document_id)
                version_matches = (
                    active is not None
                    and active.document_version == chunk.document_version
                    and active.index_version == chunk.index_version
                    if scope.active_versions
                    else scope.document_versions.get(chunk.document_id) == chunk.document_version
                )
                if (
                    chunk.project_id == scope.project_id
                    and version_matches
                    and (
                        not scope.knowledge_base_ids
                        or chunk.knowledge_base_id in scope.knowledge_base_ids
                    )
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
            prompt = [
                        SystemMessage(content=_GROUNDED_SYSTEM),
                        HumanMessage(
                            content=json.dumps(
                                {"question": _query(state), "evidence": evidence},
                                ensure_ascii=False,
                            )
                        ),
                    ]
            async def invoke(feedback: dict[str, Any] | None) -> Any:
                messages = prompt if feedback is None else [*prompt, SystemMessage(content=json.dumps(feedback))]
                return await grounded.ainvoke(messages)
            def validate(response: Any) -> GroundedAnswer:
                try:
                    result = GroundedAnswer.model_validate(response)
                except ValidationError:
                    raise ModelOutputError("invalid_answer_schema", "answer") from None
                if len(result.answer) > 12000:
                    raise ModelOutputError("answer_size_exceeded", "answer")
                return result
            result = await invoke_with_recovery(invoke, validate, timeout_seconds=model_timeout, audit={"task_id": state.get("task_id"), "trace_id": state.get("trace_id"), "node": "grounded_answer", "prompt_version": "grounded-v1", "schema_version": "grounded-v1"})
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
    def bounded_node(name: str, node: Any) -> Any:
        async def run_node(state: DialogueState, runtime: Runtime[RequestContext]) -> dict[str, Any]:
            started = monotonic()
            fields = {"task_id": state.get("task_id"), "trace_id": state.get("trace_id"), "attempt_id": state.get("attempt_id"), "node": name, "project_id": state.get("project_id"), "answer_mode": state.get("answer_mode")}
            emit_decision_event(_LOGGER, "dialogue_node_started", **fields, outcome="running")
            try:
                queries = [state.get("raw_query", ""), state.get("corrected_query", ""), state.get("resolved_query", "")]
                if any(not isinstance(value, str) or len(value) > 6000 for value in queries) or len(json.dumps(state, default=str, ensure_ascii=False)) > 64000:
                    result = _result("Input exceeds the dialogue budget.", "failed", name, code="dialogue_input_budget_exhausted")
                else:
                    async with asyncio.timeout(config.ai_node_timeout_seconds):
                        result = node(state, runtime) if name == "retrieve" else node(state)
                        if inspect.isawaitable(result):
                            result = await result
                    if not isinstance(result, dict) or len(json.dumps(result, default=str, ensure_ascii=False)) > 32000 or ("answer" in result and (not isinstance(result["answer"], str) or len(result["answer"]) > 12000)):
                        result = _result("Output exceeds the dialogue contract.", "failed", name, code="dialogue_output_contract")
            except asyncio.CancelledError:
                emit_decision_event(_LOGGER, "dialogue_node_stopped", **fields, outcome="cancelled", stop_reason="cancelled")
                raise
            except TimeoutError:
                result = _result("Dialogue timed out.", "failed", name, code="dialogue_node_timeout")
            except Exception:  # noqa: BLE001 - bounded workflow error without provider content.
                result = _result("Dialogue failed.", "failed", name, code="dialogue_node_failed")
            error = result.get("last_error", {})
            outcome = result.get("status", state.get("status", "completed"))
            emit_decision_event(_LOGGER, "dialogue_node_stopped", **fields, outcome=outcome, stop_reason=error.get("code") or outcome, error_code=error.get("code"), duration_ms=round((monotonic()-started)*1000))
            return result
        return run_node

    for name, node in {
        "select_mode": initialize,
        "direct_answer": direct_answer,
        "retrieve": retrieve,
        "grounded_answer": grounded_answer,
        "insufficient": insufficient,
        "needs_input": needs_input,
        "publish": publish,
    }.items():
        workflow.add_node(name, bounded_node(name, node))
    workflow.add_edge(START, "select_mode")
    workflow.add_conditional_edges(
        "select_mode",
        route_mode,
        {
            "direct": "direct_answer",
            "retrieval": "retrieve",
            "needs_input": "needs_input",
            "publish": "publish",
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
    for endpoint in ("direct_answer", "grounded_answer", "insufficient", "needs_input"):
        workflow.add_edge(endpoint, "publish")
    workflow.add_edge("publish", END)
    return workflow.compile(checkpointer=checkpointer, name="dialogue")
