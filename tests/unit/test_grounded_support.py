from test_dialogue_workflow import FakeModel, FakeRetriever, chunk, context, state

from testmind.models.retrieval import GroundedAnswer
from testmind.workflows.dialogue import create_dialogue_graph


async def test_demo_extractive_answer_must_be_supported_by_cited_content():
    model = FakeModel(GroundedAnswer(answer="Account locks after ninety failures.", cited_chunk_ids=["c1"]))
    model.extractive_only = True
    graph = create_dialogue_graph(FakeRetriever([chunk()]), llm=model)
    result = await graph.ainvoke(state(), context=context())
    assert result["status"] == "insufficient_evidence"
    assert result["citations"] == []
