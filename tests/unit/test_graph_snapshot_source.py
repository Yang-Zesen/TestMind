import httpx
import pytest

from testmind.infrastructure.graph_sync.source import SnapshotGraphSource


async def test_snapshot_adapter_passes_cursor_and_maps_configured_project():
    def handle(request):
        assert request.url.params["project_id"] == "external1"
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(200, json={"events": [{"event_id": "e1", "source_instance": "s1",
            "project_id": "external1", "entity_type": "requirement", "source_id": "1",
            "revision": 1, "operation": "upsert"}], "next_cursor": "c2", "has_more": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = SnapshotGraphSource(client, url="https://gateway.test/snapshot", token="secret",
                                      source_instance="s1", project_mapping={"external1": "p1"})
        page = await adapter.fetch_page("p1", "requirement", None)
    assert page.events[0].project_id == "p1"
    assert page.next_cursor == "c2"


async def test_snapshot_adapter_refuses_unknown_internal_project():
    async with httpx.AsyncClient() as client:
        adapter = SnapshotGraphSource(client, url="https://gateway.test/snapshot", token="secret",
                                      source_instance="s1", project_mapping={})
        with pytest.raises(PermissionError):
            await adapter.fetch_page("p2", "requirement", None)
