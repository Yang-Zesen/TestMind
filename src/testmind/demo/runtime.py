"""Offline adapters reuse the real parent graph and direct-answer subgraph."""

import re
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph

from testmind.infrastructure.postgres.task_repository import TaskRepository
from testmind.infrastructure.zentao.mcp import RESOURCES, ZenTaoService
from testmind.orchestration.builder import create_main_graph
from testmind.orchestration.nodes.base import BaseNode
from testmind.orchestration.state import GraphState
from testmind.services.readonly_tools import ReadonlyToolNode, ToolProposal


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
        unclear = query in {"?", "help", "帮我", "这个"}
        if "zentao" in query or "禅道" in query:
            return {"intent": "readonly_query", "answer_mode": None, "last_error": {}}
        return {
            "intent": "clarify" if unclear else "dialogue",
            "answer_mode": None if unclear else "direct",
            "intent_confidence": 0.0 if unclear else 1.0,
            "route_reason": "offline_demo_rule",
            "last_error": {},
        }


class DemoModel:
    async def ainvoke(self, messages: list[Any]) -> AIMessage:
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
    checkpointer: Any, repository: TaskRepository | None = None
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
        llm=DemoModel(),
        readonly_node=readonly,
        node_overrides={
            "spell_correction": DemoInputNode(),
            "coreference_resolution": DemoReferenceNode(),
            "intent_recognition": DemoIntentNode(),
        },
    )
