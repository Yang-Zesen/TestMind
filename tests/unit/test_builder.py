import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from testmind.orchestration.builder import create_main_graph


class FakeJevClient:
    pass


def test_main_graph_routes_classified_intents_to_answer_nodes():
    graph = create_main_graph(InMemorySaver(), FakeJevClient())

    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
    assert ("spell_correction", "intent_recognition") in edges
    assert ("intent_recognition", "coreference_resolution") in edges
    assert ("coreference_resolution", "dialogue") in edges
    assert ("intent_recognition", "clarify") in edges
    assert ("spell_correction", "coreference_resolution") not in edges
    assert ("dialogue", "__end__") in edges
    assert ("clarify", "__end__") in edges
    assert ("intent_recognition", "__end__") not in edges


@pytest.mark.asyncio
async def test_parent_subgraph_uses_one_classification_and_keeps_private_evidence_out(monkeypatch):
    from testmind.models.retrieval import GroundedAnswer, KnowledgeChunk, RetrievalScope
    from testmind.orchestration import builder as module
    from testmind.orchestration.context import RequestContext

    async def normalize(state):
        return {"corrected_query": state["raw_query"], "resolved_query": state["raw_query"]}

    monkeypatch.setattr(module, "SpellCorrectionNode", lambda: normalize)
    monkeypatch.setattr(module, "CoreferenceResolutionNode", lambda: normalize)

    class Jev:
        calls = 0

        async def classify(self, **kwargs):
            self.calls += 1
            mode = "retrieval" if self.calls == 1 else "direct"
            return {
                "answers": {
                    "intent": {"choice": "dialogue", "confidence": 0.96},
                    "answer_mode": {"choice": mode, "confidence": 0.95},
                }
            }

    class Model:
        def with_structured_output(self, schema):
            class Grounded:
                async def ainvoke(self, messages):
                    return GroundedAnswer(answer="Rule from [c1].", cited_chunk_ids=["c1"])

            return Grounded()

        async def ainvoke(self, messages):
            return AIMessage(content="Hello")

    class Retriever:
        calls = 0

        async def retrieve(self, query, scope, **kwargs):
            self.calls += 1
            assert scope.user_id == "u1" and scope.project_id == "p1"
            return [
                KnowledgeChunk(
                    chunk_id="c1",
                    document_id="d1",
                    document_version="v1",
                    project_id="p1",
                    content="Rule",
                    source_locator="rules.md",
                    relevance=0.99,
                )
            ]

    jev, retriever = Jev(), Retriever()
    graph = module.create_main_graph(InMemorySaver(), jev, retriever=retriever, llm=Model())
    scope = RetrievalScope(user_id="u1", project_id="p1", document_versions={"d1": "v1"})
    config = {"configurable": {"thread_id": "u1:p1:session"}}
    first = await graph.ainvoke(
        {
            "raw_query": "Project rule?",
            "user_id": "u1",
            "project_id": "p1",
            "messages": [HumanMessage(content="Project rule?")],
        },
        config,
        context=RequestContext(scope),
    )
    assert first["intent"] == "dialogue" and first["answer_mode"] == "retrieval"
    assert first["citations"][0]["chunk_id"] == "c1"
    assert jev.calls == 1 and retriever.calls == 1
    assert len(first["messages"]) == 2
    snapshot = (await graph.aget_state(config)).values
    assert "retrieved_chunks" not in snapshot and "retrieval_scope" not in snapshot
    second = await graph.ainvoke(
        {
            "raw_query": "Hello",
            "user_id": "u1",
            "project_id": "p1",
            "messages": [HumanMessage(content="Hello")],
        },
        config,
    )
    assert jev.calls == 2 and retriever.calls == 1
    assert second["answer_mode"] == "direct" and second["citations"] == []
    assert len(second["messages"]) == 4
    assert second["messages"][-1].content == "Hello"
