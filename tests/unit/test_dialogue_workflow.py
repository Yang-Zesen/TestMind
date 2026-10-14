import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from testmind.models.retrieval import (
    ActiveVersionScope,
    GroundedAnswer,
    KnowledgeChunk,
    RetrievalScope,
)
from testmind.orchestration.context import RequestContext
from testmind.workflows.dialogue import create_dialogue_graph


class FakeModel:
    def __init__(self, answer=None, error=None):
        self.answer = answer or GroundedAnswer(
            answer="Account locks after five failures.", cited_chunk_ids=["c1"]
        )
        self.error = error
        self.calls = []
        self.structured_calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return AIMessage(content="Use a pytest fixture.")

    def with_structured_output(self, schema):
        assert schema is GroundedAnswer
        parent = self

        class GroundedModel:
            async def ainvoke(self, messages):
                parent.structured_calls.append(messages)
                if parent.error:
                    raise parent.error
                return parent.answer

        return GroundedModel()


class FakeRetriever:
    def __init__(self, chunks=(), error=None, delay=0):
        self.chunks = list(chunks)
        self.error = error
        self.delay = delay
        self.calls = []

    async def retrieve(self, query, scope, *, limit=5):
        self.calls.append((query, scope, limit))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.chunks


def chunk(**changes):
    values = {
        "chunk_id": "c1",
        "document_id": "d1",
        "document_version": "v2",
        "project_id": "p1",
        "title": "Login rules",
        "content": "Account locks after five failures.",
        "source_locator": "docs/login.md#lockout",
        "relevance": 0.94,
    }
    values.update(changes)
    return KnowledgeChunk(**values)


def context(**changes):
    values = {"user_id": "u1", "project_id": "p1", "document_versions": {"d1": "v2"}}
    values.update(changes)
    return RequestContext(retrieval_scope=RetrievalScope(**values))


def state(mode="retrieval", **changes):
    values = {
        "raw_query": "What is the login rule?",
        "resolved_query": "What is the login rule?",
        "user_id": "u1",
        "project_id": "p1",
        "answer_mode": mode,
        "messages": [HumanMessage(content="What is the login rule?")],
    }
    values.update(changes)
    return values


@pytest.mark.asyncio
async def test_direct_path_never_retrieves_and_uses_question_once():
    retriever, model = FakeRetriever(), FakeModel()
    graph = create_dialogue_graph(retriever, llm=model)
    result = await graph.ainvoke(state("direct"))
    assert result["answer"] == "Use a pytest fixture."
    assert result["citations"] == []
    assert result["status"] == "completed"
    assert retriever.calls == []
    assert len(model.calls) == 1 and not model.structured_calls
    humans = [m.content for m in model.calls[0] if m.type == "human"]
    assert humans == ["What is the login rule?"]


@pytest.mark.asyncio
async def test_retrieval_returns_only_verified_citations_and_no_private_state():
    retriever, model = FakeRetriever([chunk()]), FakeModel()
    graph = create_dialogue_graph(retriever, llm=model)
    result = await graph.ainvoke(state(), context=context())
    assert result["answer"] == model.answer.answer
    assert result["citations"][0]["chunk_id"] == "c1"
    assert result["citations"][0]["document_version"] == "v2"
    assert result["status"] == "completed"
    assert "retrieved_chunks" not in result and "retrieval_scope" not in result
    assert len(model.structured_calls) == 1 and not model.calls
    assert len(retriever.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ctx", [None, context(user_id="other"), context(project_id="other")])
async def test_missing_or_mismatched_authority_never_retrieves(ctx):
    retriever, model = FakeRetriever([chunk()]), FakeModel()
    result = await create_dialogue_graph(retriever, llm=model).ainvoke(state(), context=ctx)
    assert result["status"] == "needs_input"
    assert result["citations"] == []
    assert retriever.calls == []
    assert not model.calls and not model.structured_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks",
    [[], [chunk(project_id="other")], [chunk(document_version="v1")], [chunk(relevance=0.1)]],
)
async def test_empty_irrelevant_foreign_or_stale_evidence_does_not_call_model(chunks):
    model = FakeModel()
    result = await create_dialogue_graph(FakeRetriever(chunks), llm=model).ainvoke(
        state(), context=context()
    )
    assert result["insufficient_evidence"] is True
    assert result["citations"] == []
    assert result["status"] == "insufficient_evidence"
    assert not model.structured_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [[], ["unknown"], ["c1", "invented"]])
async def test_model_cannot_publish_unverified_source_ids(ids):
    model = FakeModel(GroundedAnswer(answer="Unsupported answer.", cited_chunk_ids=ids))
    result = await create_dialogue_graph(FakeRetriever([chunk()]), llm=model).ainvoke(
        state(), context=context()
    )
    assert result["status"] == "insufficient_evidence"
    assert result["answer"] != "Unsupported answer."
    assert result["citations"] == []


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_does_not_fall_back_to_unverified_chat():
    model = FakeModel()
    result = await create_dialogue_graph(
        FakeRetriever(delay=1), llm=model, timeout_seconds=0.01
    ).ainvoke(state(), context=context())
    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "retrieval_unavailable"
    assert not model.calls and not model.structured_calls


@pytest.mark.asyncio
async def test_model_failure_is_sanitized():
    model = FakeModel(error=RuntimeError("secret-provider-response"))
    result = await create_dialogue_graph(FakeRetriever([chunk()]), llm=model).ainvoke(
        state(), context=context()
    )
    assert result["status"] == "failed"
    assert "secret-provider-response" not in str(result)


@pytest.mark.asyncio
async def test_new_invocation_clears_old_evidence_and_rechecks_authority():
    model = FakeModel()
    graph = create_dialogue_graph(FakeRetriever([chunk()]), llm=model, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "conversation"}}
    first = await graph.ainvoke(state(), config=config, context=context())
    assert first["citations"]
    second = await graph.ainvoke(state("direct"), config=config)
    assert second["citations"] == [] and not second["insufficient_evidence"]
    third = await graph.ainvoke(state(), config=config)
    assert third["status"] == "needs_input" and third["citations"] == []


@pytest.mark.asyncio
async def test_invalid_mode_cannot_silently_select_direct_chat():
    model = FakeModel()
    result = await create_dialogue_graph(FakeRetriever(), llm=model).ainvoke(state(None))
    assert result["status"] == "needs_input"
    assert not model.calls


@pytest.mark.asyncio
async def test_model_can_explicitly_abstain_without_publishing_an_unsupported_answer():
    model = FakeModel(
        GroundedAnswer(answer="Unverified", cited_chunk_ids=["c1"], insufficient_evidence=True)
    )
    result = await create_dialogue_graph(FakeRetriever([chunk()]), llm=model).ainvoke(
        state(), context=context()
    )
    assert result["status"] == "insufficient_evidence" and result["citations"] == []
    assert result["answer"] != "Unverified"


@pytest.mark.asyncio
async def test_active_document_and_index_versions_enable_grounded_answer():
    retriever, model = FakeRetriever([chunk(knowledge_base_id="kb1", index_version="i3")]), FakeModel()
    ctx = context(
        document_versions={},
        knowledge_base_ids=frozenset({"kb1"}),
        active_versions={"d1": ActiveVersionScope(document_version="v2", index_version="i3")},
    )
    result = await create_dialogue_graph(retriever, llm=model).ainvoke(state(), context=ctx)
    assert result["status"] == "completed"
    assert result["citations"][0]["index_version"] == "i3"
    assert len(retriever.calls) == 1 and len(model.structured_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"knowledge_base_id": "other"},
        {"index_version": "old"},
        {"index_version": ""},
        {"document_version": "old"},
        {"document_id": "d2"},
    ],
)
async def test_active_scope_rejects_foreign_or_stale_chunks_even_with_legacy_map(changes):
    model = FakeModel()
    ctx = context(
        document_versions={"d1": "v2", "d2": "v2"},
        knowledge_base_ids=frozenset({"kb1"}),
        active_versions={"d1": ActiveVersionScope(document_version="v2", index_version="i3")},
    )
    evidence = chunk(**({"knowledge_base_id": "kb1", "index_version": "i3"} | changes))
    result = await create_dialogue_graph(FakeRetriever([evidence]), llm=model).ainvoke(
        state(), context=ctx
    )
    assert result["status"] == "insufficient_evidence"
    assert result["citations"] == []
    assert not model.structured_calls


@pytest.mark.asyncio
async def test_authorized_base_without_active_versions_returns_empty_without_search():
    retriever, model = FakeRetriever([chunk()]), FakeModel()
    ctx = context(document_versions={}, knowledge_base_ids=frozenset({"kb1"}))
    result = await create_dialogue_graph(retriever, llm=model).ainvoke(state(), context=ctx)
    assert result["status"] == "insufficient_evidence"
    assert result["citations"] == []
    assert retriever.calls == []
    assert not model.structured_calls
