import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from testmind.core.security import AuthContext
from testmind.main import app


class Provider:
    async def get_context(self, request):
        return AuthContext("u1", frozenset({"p1"}), frozenset({"kb1"}))


class Repository:
    async def register_source_event(self, event, *, actor_id, correlation_id):
        return {"receipt_id": "receipt1", "job_id": "job1", "replayed": False}


def test_zentao_event_route_is_disabled_without_adapter(monkeypatch):
    monkeypatch.setattr(app.state, "zentao_event_adapter", None, raising=False)
    response = TestClient(app).post("/api/v1/knowledge-events/zentao", content=b"{}")
    assert response.status_code == 503


def test_zentao_event_route_returns_receipt_and_job(monkeypatch):
    body = json.dumps({
        "event_id": "evt1", "project": "p1", "knowledge_base": "kb1",
        "resource_type": "requirement", "resource_id": "req1",
        "operation": "updated", "version": "2",
    }).encode()
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    from testmind.infrastructure.knowledge.source_events import ZenTaoEventAdapter

    monkeypatch.setattr(app.state, "zentao_event_adapter", ZenTaoEventAdapter(secret="secret", enabled=True), raising=False)
    monkeypatch.setattr(app.state, "knowledge_repository", Repository(), raising=False)
    monkeypatch.setattr(app.state, "authorization_provider", Provider(), raising=False)
    response = TestClient(app).post(
        "/api/v1/knowledge-events/zentao",
        content=body,
        headers={"X-ZenTao-Signature": signature},
    )
    assert response.status_code == 202
    assert response.json()["job_id"] == "job1"


def test_zentao_event_rejects_body_over_limit_before_adapter(monkeypatch):
    class Adapter:
        def verify_and_normalize(self, headers, body):
            raise AssertionError("oversized body must be rejected before signature verification")

    monkeypatch.setattr(app.state, "zentao_event_adapter", Adapter(), raising=False)
    monkeypatch.setattr(app.state, "authorization_provider", Provider(), raising=False)
    response = TestClient(app).post(
        "/api/v1/knowledge-events/zentao", content=b"x" * (1024 * 1024 + 1)
    )
    assert response.status_code == 413
