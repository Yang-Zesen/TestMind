"""Offline adapters reuse the real parent graph and direct-answer subgraph."""

import re
import time
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph

from testmind.infrastructure.milvus.retrieval import Retriever
from testmind.infrastructure.postgres.case_repository import CaseRepository
from testmind.infrastructure.postgres.task_repository import TaskRepository
from testmind.infrastructure.zentao.mcp import RESOURCES, ZenTaoService
from testmind.orchestration.builder import create_main_graph
from testmind.orchestration.nodes.base import BaseNode
from testmind.orchestration.state import GraphState
from testmind.services.readonly_tools import ReadonlyToolNode, ToolProposal
from testmind.workflows.retrieval import SharedRetrievalService
from testmind.workflows.test_case_generation import PREFIX, CaseDesignNode


class DemoInputNode(BaseNode):
    name = "demo_normalize"

    async def process(self, state: GraphState) -> GraphState:
        return {"corrected_query": state["raw_query"].strip()}


class DemoReferenceNode(BaseNode):
    name = "demo_reference"

    async def process(self, state: GraphState) -> GraphState:
        return {"resolved_query": state["corrected_query"]}


class DemoIntentNode(BaseNode):
    name = "demo_intent"

    async def process(self, state: GraphState) -> GraphState:
        query = state["corrected_query"].lower()
        # Keep capabilities explicit in the demo: an unknown route must stop
        # with a durable refusal instead of being answered as ordinary chat.
        if any(word in query for word in ("脚本", "script", "自动化代码", "写代码")):
            return {"intent": "script_generation", "status": "not_enabled",
                    "answer": "该演示版本未启用脚本生成能力。", "answer_mode": None,
                    "last_error": {"code": "capability_not_enabled"},
                    "route_reason": "capability_gate"}
        if query.startswith("graph-query:") or any(word in query for word in ("图谱", "关系查询", "graph query")):
            return {"intent":"graph_query", "answer_mode":None, "last_error":{}}
        unclear = query in {"?", "help", "帮我", "这个"}
        if query.startswith(PREFIX) or "生成用例" in query or "设计用例" in query:
            return {"intent":"test_case_generation", "answer_mode":None, "last_error":{}}
        if "zentao" in query or "禅道" in query:
            return {"intent": "readonly_query", "answer_mode": None, "last_error": {}}
        return {
            "intent": "clarify" if unclear else "dialogue",
            "answer_mode": None if unclear else "retrieval" if any(word in query for word in ("知识", "资料", "文档", "knowledge", "登录", "锁定", "密码")) else "direct",
            "intent_confidence": 0.0 if unclear else 1.0,
            "route_reason": "offline_demo_rule",
            "last_error": {},
        }


class DemoModel:
    extractive_only = True
    def __init__(self, repository: TaskRepository | None = None) -> None:
        self.repository = repository

    async def _observe(self, started: float, failed: bool = False) -> None:
        if self.repository is not None:
            try:
                await self.repository.record_metric("model", "answer", time.monotonic() - started, failed=failed)
            except Exception:  # noqa: BLE001 - telemetry must not change answer behavior.
                from testmind.core.logging import get_logger

                get_logger(__name__).warning("demo_model_metric_write_failed")

    def with_structured_output(self, schema: Any) -> Any:
        from testmind.models.retrieval import GroundedAnswer
        class GroundedDemoModel:
            async def ainvoke(self, messages: list[Any]) -> Any:
                import json
                started = time.monotonic()
                payload = json.loads(str(messages[1].content))
                evidence = payload["evidence"]
                await self_model._observe(started)
                return GroundedAnswer(answer=evidence[0]["content"] if evidence else "", cited_chunk_ids=[evidence[0]["chunk_id"]] if evidence else [], insufficient_evidence=not bool(evidence))
        self_model = self
        return GroundedDemoModel()

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        started = time.monotonic()
        query = str(messages[-1].content).lower()
        if "pytest" in query:
            answer = "Pytest discovers test_ files and uses assertions to compare actual and expected behavior."
        elif "idempot" in query or "幂等" in query:
            answer = "An idempotency key replays the existing task. Reusing it with different input returns a conflict."
        elif "disconnect" in query or "断线" in query:
            answer = (
                "Disconnecting the event stream ends the subscription; the durable task continues."
            )
        else:
            answer = "This offline demonstration completed a persisted direct-answer task. Ask about pytest, idempotency or event-stream reconnects."
        await self._observe(started)
        return AIMessage(content=answer)


class DemoCaller:
    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_projects":
            return {
                "projects": [
                    {"id": 1, "name": "Offline demo project", "status": "doing"},
                    {"id": 2, "name": "Offline other project", "status": "doing"},
                ]
            }
        for resource, (list_name, detail_name, parameter, key) in RESOURCES.items():
            project = int(arguments.get("projectID", arguments.get(parameter, 1)))
            record = {"id": project, "project": project, "title": f"Offline demo {resource}", "status": "active"}
            if name == list_name:
                return {resource: [record]}
            if name == detail_name:
                return {key: record}
        raise ValueError("No demo records for this tool")


async def demo_proposal(query: str) -> ToolProposal:
    resource = next(
        (value for value in ("stories", "testcases", "bugs") if value in query.lower()), None
    )
    if resource is None:
        return ToolProposal(tool="zentao_project")
    identifier = re.search(r"\b[1-9][0-9]{0,18}\b", query)
    return ToolProposal(
        tool="zentao_detail" if identifier else "zentao_list",
        resource=cast(Literal["stories", "testcases", "bugs"], resource),
        identifier=identifier.group() if identifier else None,
    )


def create_demo_graph(
    checkpointer: Any, repository: TaskRepository | None = None, retriever: Retriever | None = None,
    graph_service: Any | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    readonly = (
        ReadonlyToolNode(
            ZenTaoService(DemoCaller()),
            repository.context,
            demo_proposal,
            {"demo-project": "1", "other-project": "2"},
        )
        if repository is not None
        else None
    )
    return create_main_graph(
        checkpointer,
        None,
        llm=DemoModel(repository),
        readonly_node=readonly,
        retriever=retriever,
        graph_query_node=_graph_node(repository, graph_service),
        case_design_node=CaseDesignNode(CaseRepository(repository.pool), retriever if isinstance(retriever, SharedRetrievalService) else None) if repository is not None else None,
        node_overrides={
            "spell_correction": DemoInputNode(),
            "coreference_resolution": DemoReferenceNode(),
            "intent_recognition": DemoIntentNode(),
        },
    )


def _graph_node(repository: Any, service: Any) -> Any:
    if repository is None or service is None:
        return None
    from testmind.workflows.graph_query import GraphQueryNode
    return GraphQueryNode(repository, service)
