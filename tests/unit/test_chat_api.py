from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from testmind.api.chat import ChatRequest, chat
from testmind.core.security import AuthContext
from testmind.main import app
from testmind.models.retrieval import RetrievalScope


class FakeGraph:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, payload, **kwargs):
        self.calls.append((payload, kwargs))
        return {
            "answer": "Rule [c1]",
            "intent": "dialogue",
            "answer_mode": "retrieval",
            "citations": [{"chunk_id": "c1"}],
            "status": "completed",
            "insufficient_evidence": False,
        }


def request(graph, scope=None):
    req = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(graph=graph))})
    if scope is not None:
        req.state.retrieval_scope = scope
    return req


class ScopeRepository:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def list_knowledge_bases(self, ids):
        self.calls.append(ids)
        if self.fail:
            raise RuntimeError("private-database-response")
        return [{"id": "kb1", "project_id": "p1"}, {"id": "kb2", "project_id": "p2"}]

    async def build_retrieval_scope(self, *, user_id, project_id, knowledge_base_ids):
        from testmind.models.retrieval import ActiveVersionScope

        self.calls.append((user_id, project_id, knowledge_base_ids))
        return RetrievalScope(
            user_id=user_id,
            project_id=project_id,
            knowledge_base_ids=knowledge_base_ids,
            active_versions={"d1": ActiveVersionScope(document_version="v2", index_version="i3")},
        )


def auth(user="u1", projects=("p1", "p2")):
    return AuthContext(user, frozenset(projects), frozenset())


@pytest.mark.asyncio
async def test_chat_passes_only_trusted_scope_and_returns_answer_mode_and_sources():
    graph = FakeGraph()
    scope = RetrievalScope(user_id="u1", project_id="p1", document_versions={"d1": "v1"})
    result = await chat(
        ChatRequest(query="Rule?", session_id="session", project_id="p1"),
        request(graph, scope),
        auth(),
    )
    payload, kwargs = graph.calls[0]
    assert payload["project_id"] == "p1" and payload["user_id"] == "u1"
    assert kwargs["context"].retrieval_scope is scope
    assert result["answer_mode"] == "retrieval" and result["citations"] == [{"chunk_id": "c1"}]
    assert "retrieval_scope" not in payload


@pytest.mark.asyncio
async def test_chat_isolates_same_session_by_authenticated_user_and_project():
    graph = FakeGraph()
    for user, project in (("u1", "p1"), ("u2", "p1"), ("u1", "p2")):
        scope = RetrievalScope(user_id=user, project_id=project, document_versions={"d": "v1"})
        await chat(
            ChatRequest(query="Hi", session_id="same", project_id=project),
            request(graph, scope),
            auth(user),
        )
    ids = [kwargs["config"]["configurable"]["thread_id"] for _, kwargs in graph.calls]
    assert len(set(ids)) == 3
    assert len({payload["trace_id"] for payload, _ in graph.calls}) == 3


@pytest.mark.asyncio
async def test_cannot_use_an_authorized_scope_to_query_a_different_project():
    scope = RetrievalScope(user_id="u1", project_id="p1", document_versions={"d": "v1"})
    with pytest.raises(HTTPException) as raised:
        await chat(
            ChatRequest(query="Rule", session_id="s", project_id="p2"),
            request(FakeGraph(), scope),
            auth(),
        )
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_retrieval_scope_must_belong_to_authenticated_user():
    graph = FakeGraph()
    scope = RetrievalScope(user_id="u1", project_id="p1", document_versions={"d": "v1"})
    with pytest.raises(HTTPException) as raised:
        await chat(
            ChatRequest(query="Rule", session_id="s", project_id="p1"),
            request(graph, scope),
            auth(user="u2"),
        )
    assert raised.value.status_code == 403
    assert graph.calls == []


def test_client_cannot_supply_its_own_scope_or_user_identity():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ChatRequest(query="Rule", session_id="s", user_id="admin", retrieval_scope={"d1": "v1"})


@pytest.mark.asyncio
async def test_authenticated_request_without_project_is_user_partitioned():
    graph = FakeGraph()
    await chat(ChatRequest(query="Rule", session_id="s"), request(graph), auth())
    payload, kwargs = graph.calls[0]
    assert payload["user_id"] == "u1" and kwargs["context"].retrieval_scope is None


@pytest.mark.asyncio
async def test_chat_assembles_database_scope_without_middleware_injection():
    graph, repository = FakeGraph(), ScopeRepository()
    req = request(graph)
    req.app.state.knowledge_repository = repository
    principal = AuthContext("u1", frozenset({"p1"}), frozenset({"kb1", "kb2"}))
    await chat(ChatRequest(query="Rule", session_id="s", project_id="p1"), req, principal)
    payload, kwargs = graph.calls[0]
    scope = kwargs["context"].retrieval_scope
    assert scope.knowledge_base_ids == frozenset({"kb1"})
    assert scope.active_versions["d1"].index_version == "i3"
    assert not scope.document_versions
    assert "retrieval_scope" not in payload


@pytest.mark.asyncio
async def test_database_scope_failure_returns_503_without_running_graph_or_leaking_details():
    graph = FakeGraph()
    req = request(graph)
    req.app.state.knowledge_repository = ScopeRepository(fail=True)
    principal = AuthContext("u1", frozenset({"p1"}), frozenset({"kb1"}))
    with pytest.raises(HTTPException) as raised:
        await chat(ChatRequest(query="Rule", session_id="s", project_id="p1"), req, principal)
    assert raised.value.status_code == 503
    assert "private-database-response" not in str(raised.value.detail)
    assert graph.calls == []


@pytest.mark.asyncio
async def test_direct_chat_without_project_does_not_read_knowledge_repository():
    graph, repository = FakeGraph(), ScopeRepository(fail=True)
    req = request(graph)
    req.app.state.knowledge_repository = repository
    principal = AuthContext("u1", frozenset({"p1"}), frozenset({"kb1"}))
    await chat(ChatRequest(query="Explain fixtures", session_id="s"), req, principal)
    assert repository.calls == []
    assert graph.calls[0][1]["context"].retrieval_scope is None


@pytest.mark.asyncio
async def test_database_scope_reaches_real_parent_and_dialogue_graph(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    from testmind.models.retrieval import GroundedAnswer, KnowledgeChunk
    from testmind.orchestration import builder

    async def normalize(state):
        return {"corrected_query": state["raw_query"], "resolved_query": state["raw_query"]}

    monkeypatch.setattr(builder, "SpellCorrectionNode", lambda: normalize)
    monkeypatch.setattr(builder, "CoreferenceResolutionNode", lambda: normalize)

    class Jev:
        async def classify(self, **kwargs):
            return {
                "answers": {
                    "intent": {"choice": "dialogue", "confidence": 0.96},
                    "answer_mode": {"choice": "retrieval", "confidence": 0.95},
                }
            }

    class Model:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            return GroundedAnswer(answer="Rule from [c1].", cited_chunk_ids=["c1"])

    class Retriever:
        async def retrieve(self, query, scope, **kwargs):
            assert scope.knowledge_base_ids == frozenset({"kb1"})
            assert scope.active_versions["d1"].index_version == "i3"
            return [
                KnowledgeChunk(
                    chunk_id="c1",
                    document_id="d1",
                    document_version="v2",
                    index_version="i3",
                    knowledge_base_id="kb1",
                    project_id="p1",
                    content="Rule",
                    source_locator="rules.md",
                    relevance=0.99,
                )
            ]

    graph = builder.create_main_graph(InMemorySaver(), Jev(), retriever=Retriever(), llm=Model())
    req = request(graph)
    req.app.state.knowledge_repository = ScopeRepository()
    principal = AuthContext("u1", frozenset({"p1"}), frozenset({"kb1", "kb2"}))
    result = await chat(
        ChatRequest(query="Project rule?", session_id="s", project_id="p1"), req, principal
    )
    assert result["status"] == "completed"
    assert result["citations"][0]["index_version"] == "i3"
    assert result["answer_mode"] == "retrieval"


def test_chat_fails_closed_without_authentication_provider(monkeypatch):
    monkeypatch.setattr(app.state, "authorization_provider", None, raising=False)
    response = TestClient(app).post("/chat", json={"query": "hello", "session_id": "s"})
    assert response.status_code == 503


def test_chat_rejects_project_outside_authenticated_membership(monkeypatch):
    class Provider:
        async def get_context(self, request):
            return auth(projects=("p1",))

    monkeypatch.setattr(app.state, "authorization_provider", Provider(), raising=False)
    response = TestClient(app).post(
        "/chat", json={"query": "hello", "session_id": "s", "project_id": "p2"}
    )
    assert response.status_code == 403
