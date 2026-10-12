"""General conversation node."""

from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from testmind.core.errors import ValidationError
from testmind.infrastructure.llm.llm_client import get_llm_client
from testmind.orchestration.nodes.base import BaseNode
from testmind.orchestration.state import GraphState
from testmind.prompts.load_prompts import load_template
from testmind.tools.update_time import _now

_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", load_template("general_chat.txt")),
        MessagesPlaceholder("history"),
        ("human", "{query}"),
    ]
)


class GeneralChatNode(BaseNode):
    """普通对话节点
    Answer ordinary Python, pytest and testing questions.
    """

    name = "general_chat_node"

    def __init__(self, *, llm: Any | None = None) -> None:
        super().__init__()
        self._llm = llm if llm is not None else get_llm_client(response_format=False)

    @staticmethod
    def _history_without_current_turn(state: GraphState) -> list[AnyMessage]:
        messages = list(state.get("messages", []))
        current_values = {
            value.strip()
            for value in (
                state.get("raw_query", ""),
                state.get("corrected_query", ""),
                state.get("resolved_query", ""),
            )
            if isinstance(value, str) and value.strip()
        }
        if messages and isinstance(messages[-1], HumanMessage):
            content = messages[-1].content
            if isinstance(content, str) and content.strip() in current_values:
                messages.pop()
        return messages[-6:]

    async def process(self, state: GraphState) -> dict[str, Any]:
        query = (
            state.get("resolved_query")
            or state.get("corrected_query")
            or state.get("raw_query")
            or ""
        ).strip()
        if not query:
            raise ValidationError(message="普通聊天问题不能为空", node_name=self.name)

        prompt = _PROMPT.format_messages(
            history=self._history_without_current_turn(state),
            query=query,
        )
        response = await self._llm.ainvoke(prompt)
        content = response.content
        if not isinstance(content, str) or not content.strip():
            raise ValidationError(message="普通聊天模型返回内容为空", node_name=self.name)

        answer = content.strip()
        return {
            "answer": answer,
            "messages": [AIMessage(content=answer)],
            "current_stage": "general_chat",
            "updated_at": _now(),
            "last_error": {},
        }
