import httpx
from frontend.streamlit.api_client import KnowledgeApiClient, KnowledgeApiError


def test_client_uses_server_returned_scope_and_uploads_idempotently():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["idempotency"] = request.headers["Idempotency-Key"]
        return httpx.Response(202, json={"job_id": "job1"})

    with KnowledgeApiClient("http://test", token="token") as client:
        client._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
        result = client.upload_document("p1", "kb1", "a.txt", b"hello", idempotency_key="key1")
    assert result == {"job_id": "job1"}
    assert seen == {"method": "POST", "path": "/api/v1/knowledge-bases/kb1/documents", "idempotency": "key1"}


def test_client_sanitizes_service_errors():
    transport = httpx.MockTransport(lambda _: httpx.Response(403, json={"detail": "denied"}))
    with KnowledgeApiClient("http://test") as client:
        client._client = httpx.Client(transport=transport, base_url="http://test")
        try:
            client.list_knowledge_bases()
        except KnowledgeApiError as exc:
            assert exc.status_code == 403
            assert str(exc) == "denied"
        else:
            raise AssertionError("expected KnowledgeApiError")
