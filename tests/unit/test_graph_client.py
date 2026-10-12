import httpx
from frontend.streamlit.graph_client import GraphApiClient


def test_client_does_not_accept_cypher_or_identity_parameters():
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"items": []})
    with GraphApiClient("http://test") as client:
        client._client.close()
        client._client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle))
        client.query("p1", "entities")
    assert seen[0].url.path == "/api/v1/graph/query"
    assert dict(seen[0].url.params) == {"project_id": "p1", "query_type": "entities", "limit": "50"}
