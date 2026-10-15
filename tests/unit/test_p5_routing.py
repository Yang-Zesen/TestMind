import pytest
from langgraph.checkpoint.memory import InMemorySaver

from testmind.demo.runtime import create_demo_graph
from testmind.models.task import TERMINAL, allowed_transition


@pytest.mark.asyncio
async def test_unsupported_intent_is_terminal_and_does_not_answer_normally():
    graph = create_demo_graph(InMemorySaver())
    result = await graph.ainvoke({"raw_query": "生成自动化脚本", "messages": []},
                                {"configurable": {"thread_id": "p5-unsupported"}})
    assert result["status"] == "not_enabled"
    assert result["last_error"]["code"] == "capability_not_enabled"
    assert result["citations"] == []
    assert "not_enabled" in TERMINAL
    assert allowed_transition("running", "not_enabled")
