from fastapi.testclient import TestClient

from testmind.core.security import AuthContext
from testmind.main import app


class Provider:
    async def get_context(self, request):
        return AuthContext("user1", frozenset({"p1"}), frozenset())


def test_graph_read_and_write_fail_closed_without_auth(monkeypatch):
    monkeypatch.setattr(app.state, "authorization_provider", None, raising=False)
    client = TestClient(app)
    assert client.get("/api/v1/graph/query", params={"project_id": "p1"}).status_code == 503
    assert client.post("/api/v1/graph/events", json={}).status_code == 503


def test_graph_query_rejects_arbitrary_cypher(monkeypatch):
    monkeypatch.setattr(app.state, "authorization_provider", Provider(), raising=False)
    response = TestClient(app).get("/api/v1/graph/query", params={"project_id": "p1", "cypher": "MATCH (n) RETURN n"})
    assert response.status_code == 422


def test_graph_query_denies_unapproved_project(monkeypatch):
    monkeypatch.setattr(app.state, "authorization_provider", Provider(), raising=False)
    response = TestClient(app).get("/api/v1/graph/query", params={"project_id": "p2"})
    assert response.status_code == 403


def test_signed_ingress_returns_durable_receipt(monkeypatch):
    import hashlib
    import hmac
    import json
    import time

    from testmind.infrastructure.graph_sync.source import SignedGraphSource

    class Repository:
        async def register_event(self, event, *, actor_id):
            assert event.project_id == "p1"
            assert actor_id == "source:s1"
            return {"receipt_id": "receipt", "job_id": "job", "status": "accepted", "replayed": False}

    payload = json.dumps({"event_id": "e1", "source_instance": "s1", "project_id": "external",
                          "entity_type": "bug", "source_id": "b1", "revision": 1, "operation": "upsert"}).encode()
    timestamp = int(time.time())
    signature = hmac.new(b"secret", str(timestamp).encode()+b"."+payload, hashlib.sha256).hexdigest()
    monkeypatch.setattr(app.state, "graph_event_source", SignedGraphSource("s1", secret="secret", project_mapping={"external": "p1"}), raising=False)
    monkeypatch.setattr(app.state, "graph_sync_repository", Repository(), raising=False)
    response = TestClient(app).post("/api/v1/integrations/zentao/events", content=payload,
        headers={"X-Event-Timestamp": str(timestamp), "X-Event-Signature": signature})
    assert response.status_code == 202
    assert response.json()["job_id"] == "job"


def test_maintenance_does_not_accept_client_asserted_permissions(monkeypatch):
    class Repository:
        async def permission(self, project_id, user_id):
            return {"role": "employee", "can_maintain": False, "permission_version": 1}

    monkeypatch.setattr(app.state, "authorization_provider", Provider(), raising=False)
    monkeypatch.setattr(app.state, "graph_sync_repository", Repository(), raising=False)
    response = TestClient(app).post("/api/v1/graph/rebuild", json={"project_id": "p1"})
    assert response.status_code == 403


async def test_saved_graph_result_denied_after_role_version_change(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from starlette.requests import Request

    from testmind.api.tasks import require_graph_result_access

    class Repo:
        async def permission(self, project_id, user_id):
            return {"role":"employee", "can_maintain":False, "permission_version":2}

    monkeypatch.setattr(app.state, "graph_sync_repository", Repo(), raising=False)
    request = Request({"type":"http", "app":app})
    task = {"project_id":"p1", "result":{"graph_result":{"permission_version":1, "items":["old_team_result"]}}}
    with pytest.raises(HTTPException) as error:
        await require_graph_result_access(request,task,AuthContext("user1",frozenset({"p1"}),frozenset()))
    assert error.value.status_code == 403
