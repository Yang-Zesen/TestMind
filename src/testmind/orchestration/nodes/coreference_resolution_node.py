"""Resolve references in the current question using this conversation's recent history."""

from __future__ import annotations

from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from testmind.core.errors import ValidationError
from testmind.infrastructure.llm.llm_client import get_llm_client
from testmind.models.coreference_resolution_model import CoreferenceResult
from testmind.orchestration.nodes.base import BaseNode
from testmind.orchestration.state import GraphState
from testmind.prompts.load_prompts import load_template

_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", load_template("coreference_resolution.txt")),
        MessagesPlaceholder("history"),
        ("human", "当前问题：{query}"),
    ]
)
_MIN_CONFIDENCE = 0.5


class CoreferenceResolutionNode(BaseNode):
    """Rewrite an ambiguous follow-up question without changing the original."""

    name = "coreference_resolution_node"

    def __init__(self) -> None:
        super().__init__()
        self._model = get_llm_client(response_format=False).with_structured_output(
            CoreferenceResult
        )

    @staticmethod
    def _fallback(query: str, reason: str) -> dict:
        detail = CoreferenceResult(
            resolved_query=query,
            has_coreference=False,
            references=[],
            confidence=0.0,
            explanation=reason,
        )
        return {"resolved_query": query, "coreference_detail": detail.model_dump()}

    async def process(self, state: GraphState) -> dict:
        query = state.get("corrected_query", "").strip()
        if not query:
            raise ValidationError(message="待消解问题不能为空", node_name=self.name)

        history: list[AnyMessage] = list(state.get("messages", []))
        # The request layer may already have appended this turn to messages.
        if history and isinstance(history[-1], HumanMessage) and history[-1].content in (
            state.get("raw_query"),
            query,
        ):
            history.pop()
        history = history[-10:]
        if not history:
            return self._fallback(query, "没有可用于消解的历史对话")

        prompt = _PROMPT.format_messages(history=history, query=query)
        try:
            result: CoreferenceResult = await self._model.ainvoke(prompt)
            if result is None:
                return self._fallback(query, "模型未返回可用结果")
        except Exception:
            self._logger.exception("指代消解模型调用失败，保留原问题")
            return self._fallback(query, "模型调用失败，已保留原问题")

        if not result.has_coreference:
            return self._fallback(query, "当前问题不需要指代消解")
        resolved_query = result.resolved_query.strip()
        if result.confidence < _MIN_CONFIDENCE or not resolved_query or not result.references:
            return self._fallback(query, "指代关系不明确，已保留原问题")

        return {
            "resolved_query": resolved_query,
            "coreference_detail": result.model_dump(),
        }
