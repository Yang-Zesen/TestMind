import pytest
from fastapi.testclient import TestClient

from testmind.core.security import AuthContext
from testmind.main import app


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/knowledge-bases"),
        ("GET", "/api/v1/knowledge-bases/kb1/documents"),
        ("GET", "/api/v1/knowledge-bases/kb1/jobs"),
        ("GET", "/api/v1/documents/doc1/versions"),
        ("GET", "/api/v1/versions/ver1/quality-report"),
        ("GET", "/api/v1/jobs/job1"),
        ("POST", "/api/v1/jobs/job1/retry"),
        ("POST", "/api/v1/documents/doc1/reindex"),
        ("DELETE", "/api/v1/documents/doc1"),
        ("POST", "/api/v1/documents/doc1/versions/ver1/activate"),
        ("POST", "/api/v1/knowledge-bases/kb1/documents"),
    ],
)
def test_all_knowledge_routes_fail_closed_without_provider(method, path):
    app.state.authorization_provider = None
    app.state.knowledge_repository = None
    app.state.source_storage = None

    response = TestClient(app).request(method, path)

    assert response.status_code == 503


class FakeProvider:
    async def get_context(self, request):
        return AuthContext(
            user_id="user1",
            authorized_project_ids=frozenset({"project1"}),
            authorized_knowledge_base_ids=frozenset({"kb1"}),
        )


class FakeRepository:
    async def get_knowledge_base(self, kb_id):
        return {"id": kb_id, "project_id": "project1"}

    async def create_upload(self, **kwargs):
        return {
            "job_id": "job1",
            "document_id": "doc1",
            "version_id": "ver1",
            "replayed": False,
        }


class FakeStorage:
    def __init__(self):
        self.deleted = []

    def save(self, stream, *, suffix):
        from testmind.infrastructure.knowledge.source_storage import StoredObject

        return StoredObject("private/path.txt", "a" * 64, len(stream.read()))

    def delete(self, key):
        self.deleted.append(key)


def test_upload_returns_accepted_ids_without_storage_key(monkeypatch):
    monkeypatch.setattr(app.state, "authorization_provider", FakeProvider(), raising=False)
    monkeypatch.setattr(app.state, "knowledge_repository", FakeRepository(), raising=False)
    monkeypatch.setattr(app.state, "source_storage", FakeStorage(), raising=False)

    response = TestClient(app).post(
        "/api/v1/knowledge-bases/kb1/documents",
        headers={"Idempotency-Key": "request-1"},
        files={"file": ("requirements.txt", b"login rules", "text/plain")},
        data={"title": "Requirements", "metadata": "{}"},
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job1"
    assert "private/path.txt" not in response.text


def test_upload_rejects_unauthorized_knowledge_base_before_storage(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(app.state, "authorization_provider", provider, raising=False)
    storage = FakeStorage()
    monkeypatch.setattr(app.state, "knowledge_repository", FakeRepository(), raising=False)
    monkeypatch.setattr(app.state, "source_storage", storage, raising=False)

    response = TestClient(app).post(
        "/api/v1/knowledge-bases/other/documents",
        headers={"Idempotency-Key": "request-1"},
        files={"file": ("requirements.txt", b"login rules", "text/plain")},
    )

    assert response.status_code == 403
    assert storage.deleted == []
