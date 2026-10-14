import pytest
from langgraph.checkpoint.memory import InMemorySaver

from testmind.core.config import Config
from testmind.demo.runtime import create_demo_graph


def test_demo_auth_and_models_cannot_be_enabled_in_production():
    with pytest.raises(ValueError):
        Config(app_env="production", demo_mode=True)
    with pytest.raises(ValueError):
        Config(app_env="production", knowledge_auth_mode="demo")


async def test_demo_uses_actual_parent_and_direct_subgraph():
    graph = create_demo_graph(InMemorySaver())
    result = await graph.ainvoke({"raw_query": "What is pytest?", "user_id": "demo-user", "project_id": "demo-project", "messages": [], "trace_id": "trace"}, config={"configurable": {"thread_id": "demo-test"}})
    assert result["status"] == "completed"
    assert result["answer_mode"] == "direct"
    assert "pytest" in result["answer"].lower()
    assert "dialogue" in {node.name for node in graph.get_graph().nodes.values()}
