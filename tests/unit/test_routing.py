import pytest
from langchain_core.messages import AIMessage, HumanMessage

from testmind.orchestration.nodes.clarify import ClarifyNode
from testmind.orchestration.routing import route_by_intent


@pytest.mark.parametrize(
    ("intent", "destination"),
    [
        ("dialogue", "coreference_resolution"),
        ("general_chat", "clarify"),
        ("clarify", "clarify"),
        ("rag_query", "clarify"),
        ("test_case_generation", "clarify"),
        ("script_generation", "clarify"),
        ("graph_query", "clarify"),
        ("unknown", "clarify"),
    ],
)
def test_route_by_intent_is_safe_until_agents_are_implemented(intent, destination):
    assert route_by_intent({"intent": intent}) == destination


@pytest.mark.asyncio
async def test_clarify_reports_unavailable_capability_without_misleading_chat():
    state = {"intent": "rag_query", "messages": [HumanMessage(content="查历史缺陷")]}
    updates = await ClarifyNode().process(state)

    assert "知识库" in updates["answer"]
    assert "尚未" in updates["answer"]
    assert isinstance(updates["messages"][0], AIMessage)
    assert state["messages"] == [HumanMessage(content="查历史缺陷")]


@pytest.mark.asyncio
async def test_clarify_requests_more_information_for_ambiguous_intent():
    updates = await ClarifyNode().process({"intent": "clarify", "messages": []})

    assert updates["clarification_question"] == updates["answer"]
    assert "请" in updates["answer"]
