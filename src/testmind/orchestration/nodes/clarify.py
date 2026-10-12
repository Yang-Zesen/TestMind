"""Fallback node for clarification and not-yet-wired capabilities."""

from typing import Any

from langchain_core.messages import AIMessage

from testmind.orchestration.nodes.base import BaseNode
from testmind.orchestration.state import GraphState
from testmind.tools.update_time import _now


class ClarifyNode(BaseNode):
    """Ask for missing details or explain that a route is not wired yet."""

    name = "clarify_node"

    async def process(self, state: GraphState) -> dict[str, Any]:
        intent = state.get("intent", "clarify")
        if intent == "clarify":
            answer = "请补充你希望完成的具体任务，例如知识查询、用例生成、脚本生成或进度查询。"
        else:
            labels = {
                "rag_query": "知识库查询",
                "test_case_generation": "测试用例生成",
                "script_generation": "自动化脚本生成",
                "graph_query": "项目进度查询",
            }
            answer = f"当前已识别为“{labels.get(intent, intent)}”，对应能力尚未接入，请稍后重试。"

        return {
            "answer": answer,
            "clarification_question": answer if intent == "clarify" else "",
            "current_stage": "clarify",
            "status": "needs_input",
            "citations": [],
            "insufficient_evidence": False,
            "updated_at": _now(),
            "messages": [AIMessage(content=answer)],
        }
