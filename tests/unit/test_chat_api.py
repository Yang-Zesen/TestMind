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


def auth(user="u1", projects=("p1", "p2")):
    return AuthContext(user, frozenset(projects), frozenset())


@pytest.mark.asyncio
async def test_chat_passes_only_trusted_scope_and_returns_answer_mode_and_sources():
    graph = FakeGraph()
    scope = RetrievalScope(user_id="u1", project_id="p1", document_versions={"d1": "v1"})
    result = await chat(
        ChatRequest(query="Rule?", session_id="session", project_id="p1"), request(graph, scope), auth()
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
            ChatRequest(query="Hi", session_id="same", project_id=project), request(graph, scope), auth(user)
        )
    ids = [kwargs["config"]["configurable"]["thread_id"] for _, kwargs in graph.calls]
    assert len(set(ids)) == 3
    assert len({payload["trace_id"] for payload, _ in graph.calls}) == 3


@pytest.mark.asyncio
async def test_cannot_use_an_authorized_scope_to_query_a_different_project():
    scope = RetrievalScope(user_id="u1", project_id="p1", document_versions={"d": "v1"})
    with pytest.raises(HTTPException) as raised:
        await chat(
            ChatRequest(query="Rule", session_id="s", project_id="p2"), request(FakeGraph(), scope), auth()
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
async def test_authenticated_request_without_retrieval_scope_is_user_partitioned():
    graph = FakeGraph()
    await chat(ChatRequest(query="Rule", session_id="s", project_id="p1"), request(graph), auth())
    payload, kwargs = graph.calls[0]
    assert payload["user_id"] == "u1" and kwargs["context"].retrieval_scope is None


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
