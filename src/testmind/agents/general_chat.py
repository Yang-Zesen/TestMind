"""General conversation node."""

import json
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from testmind.core.errors import ValidationError
from testmind.core.model_recovery import ModelOutputError, invoke_with_recovery
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

    async def process(self, state: GraphState) -> GraphState:
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

        async def invoke(feedback: dict[str, Any] | None) -> Any:
            messages = (
                prompt
                if feedback is None
                else [*prompt, SystemMessage(content=json.dumps(feedback))]
            )
            return await self._llm.ainvoke(messages)

        def validate(response: Any) -> str:
            content = getattr(response, "content", None)
            if not isinstance(content, str) or not content.strip() or len(content) > 12000:
                raise ModelOutputError("invalid_answer", "content")
            return content.strip()

        try:
            answer = await invoke_with_recovery(
                invoke,
                validate,
                timeout_seconds=self._config.ai_node_timeout_seconds,
                audit={
                    "node": self.name,
                    "task_id": state.get("task_id"),
                    "trace_id": state.get("trace_id"),
                },
            )
        except ModelOutputError:
            raise ValidationError(
                message="模型回答为空或超出输出契约", node_name=self.name
            ) from None
        return {
            "answer": answer,
            "messages": [AIMessage(content=answer)],
            "current_stage": "general_chat",
            "updated_at": _now(),
            "last_error": {},
        }
