"""Private bounded graph-query workflow; proposals never contain Cypher."""

import json
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from testmind.api.graph_query import GraphQuery
from testmind.orchestration.nodes.base import BaseNode
from testmind.orchestration.state import GraphState

PREFIX = "graph-query:"


class GraphQueryState(TypedDict):
    query: GraphQuery
    auth: Any
    result: dict[str, Any]


def propose_query(query: str, project_id: str) -> GraphQuery:
    if len(query) > 6000 or re.search(r"\b(match|merge|detach|delete|create|call|cypher)\b", query, re.IGNORECASE):
        raise ValueError("unsupported_graph_request")
    if query.startswith(PREFIX):
        payload = json.loads(query[len(PREFIX):])
        return GraphQuery.model_validate({**payload, "project_id":project_id})
    if any(word in query for word in ("趋势", "历史", "trend", "history")):
        raise ValueError("graph_history_unavailable")
    mode = "metrics" if any(word in query.lower() for word in ("统计", "指标", "数量", "metrics", "count")) else "relationships" if any(word in query for word in ("关系", "关联", "relationship")) else "entities"
    types = (("requirement", ("需求", "requirement")), ("test_case", ("用例", "case")),
             ("bug", ("缺陷", "bug")), ("script", ("脚本", "script")),
             ("test_run", ("执行", "run")), ("member", ("成员", "member")))
    selected = next((kind for kind, words in types if any(word in query.lower() for word in words)), None)
    return GraphQuery.model_validate({"project_id": project_id, "query_type": mode, "entity_type": selected, "limit": 20})


class GraphQueryNode(BaseNode):
    name = "graph_query"

    def __init__(self, tasks: Any, service: Any):
        super().__init__()
        self.tasks, self.service = tasks, service
        graph = StateGraph(GraphQueryState)
        graph.add_node("authorized_template", self._query)
        graph.add_edge(START, "authorized_template")
        graph.add_edge("authorized_template", END)
        self.graph = graph.compile()

    async def _query(self, state: GraphQueryState) -> dict[str, Any]:
        if state["query"].query_type == "metrics":
            result = await self.service.metrics(state["query"].project_id, state["auth"])
        else:
            result = await self.service.query(state["query"], state["auth"])
        if len(json.dumps(result, ensure_ascii=False, default=str)) > 22000:
            raise ValueError("graph_result_too_large")
        return {"result": result}

    async def process(self, state: GraphState) -> GraphState:
        try:
            auth = await self.tasks.context(state["user_id"])
            if auth is None or state["project_id"] not in auth.authorized_project_ids:
                raise PermissionError("graph_access_denied")
            query = propose_query(state.get("corrected_query") or state["raw_query"], state["project_id"])
            result = (await self.graph.ainvoke({"query":query, "auth":auth, "result":{}}))["result"]
            fresh = await self.tasks.context(state["user_id"])
            if fresh is None or state["project_id"] not in fresh.authorized_project_ids:
                raise PermissionError("graph_access_revoked")
            answer = "已查询当前授权资产的统计；没有历史数据，不生成趋势结论。" if query.query_type == "metrics" else f"查询到 {len(result['items'])} 条当前授权图谱记录。" 
            return {"status":"completed", "current_stage":"graph_query", "answer":answer,
                    "graph_result":result, "citations":[], "last_error":{}}
        except PermissionError:
            return {"status":"denied", "answer":"图谱权限已撤销或不足。", "graph_result":{}, "last_error":{"code":"graph_access_denied"}}
        except (ValueError, TypeError):
            return {"status":"needs_input", "answer":"仅支持当前资产、关系和指标查询；不执行 Cypher 或推断历史趋势。", "graph_result":{}, "last_error":{"code":"graph_query_invalid"}}
