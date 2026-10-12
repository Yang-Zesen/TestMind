import pytest
from langchain_core.messages import AIMessage, HumanMessage

from testmind.agents import general_chat as module
from testmind.core.errors import ValidationError


class FakeLLM:
    def __init__(self, content="可以使用 pytest fixture。"): 
        self.content = content
        self.seen = None

    async def ainvoke(self, messages):
        self.seen = messages
        return AIMessage(content=self.content)


@pytest.mark.asyncio
async def test_chat_uses_resolved_question_once_and_preserves_history(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(module, "get_llm_client", lambda **kwargs: llm)
    original = [
        HumanMessage(content="介绍 pytest fixture"),
        AIMessage(content="它用于管理测试依赖。"),
        HumanMessage(content="那它有什么用？"),
    ]
    state = {
        "raw_query": "那它有什么用？",
        "corrected_query": "那它有什么用？",
        "resolved_query": "pytest fixture 有什么用？",
        "messages": original.copy(),
    }

    result = await module.GeneralChatNode().process(state)

    assert [message.content for message in llm.seen if message.type == "human"] == [
        "介绍 pytest fixture",
        "pytest fixture 有什么用？",
    ]
    assert result["answer"] == "可以使用 pytest fixture。"
    assert len(result["messages"]) == 1
    assert result["messages"][0].content == result["answer"]
    assert state["messages"] == original
    assert len(state["messages"]) == 3
    assert "updated_at" in result


@pytest.mark.asyncio
async def test_empty_model_response_raises_instead_of_succeeding(monkeypatch):
    monkeypatch.setattr(module, "get_llm_client", lambda **kwargs: FakeLLM(""))
    with pytest.raises(ValidationError, match="空"):
        await module.GeneralChatNode().process(
            {"resolved_query": "你好", "messages": [HumanMessage(content="你好")]}
        )
