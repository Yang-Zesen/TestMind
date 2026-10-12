"""Signed normalized event contract, not a guessed native ZenTao API."""

import hashlib
import hmac
import json
import time

import httpx

from testmind.infrastructure.graph_sync.polling import PollPage
from testmind.models.graph_sync import GraphEvent


class SignedGraphSource:
    def __init__(self, source_instance: str, *, secret: str, project_mapping: dict[str, str]) -> None:
        if not secret or not source_instance:
            raise ValueError("Source identity and signature secret are required")
        self.source_instance = source_instance
        self.secret = secret.encode()
        self.project_mapping = dict(project_mapping)

    def normalize(
        self, body: bytes, *, timestamp: int, signature: str, now: float | None = None
    ) -> GraphEvent:
        if abs((time.time() if now is None else now) - timestamp) > 300:
            raise PermissionError("Expired event signature")
        expected = hmac.new(
            self.secret, str(timestamp).encode()+b"."+body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise PermissionError("Invalid event signature")
        payload = json.loads(body)
        event = GraphEvent.model_validate(payload)
        if event.source_instance != self.source_instance:
            raise PermissionError("Source identity mismatch")
        project_id = self.project_mapping.get(event.project_id)
        if not project_id:
            raise PermissionError("Source project is not configured")
        return event.model_copy(update={"project_id": project_id})


class SnapshotGraphSource:
    """Configured gateway contract: events, next_cursor, has_more."""

    def __init__(self, client: httpx.AsyncClient, *, url: str, token: str,
                 source_instance: str, project_mapping: dict[str, str]) -> None:
        self.client = client
        self.url = url
        self.token = token
        self.source_instance = source_instance
        self.project_mapping = dict(project_mapping)

    async def fetch_page(self, project_id: str, resource_type: str, cursor: object) -> PollPage:
        sources = [key for key, value in self.project_mapping.items() if value == project_id]
        if len(sources) != 1:
            raise PermissionError("Project mapping must have exactly one configured source project")
        response = await self.client.get(
            self.url, params={"project_id": sources[0], "resource_type": resource_type,
                              "cursor": json.dumps(cursor)},
            headers={"Authorization": f"Bearer {self.token}"}, follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise TypeError("Invalid snapshot page")
        if len(payload["events"]) > 1000 or not isinstance(payload.get("has_more"), bool):
            raise ValueError("Invalid or excessive snapshot page")
        events = []
        for raw in payload["events"]:
            event = GraphEvent.model_validate(raw)
            if event.source_instance != self.source_instance or event.project_id != sources[0]:
                raise PermissionError("Snapshot page source/project mismatch")
            events.append(event.model_copy(update={"project_id": project_id}))
        return PollPage(tuple(events), payload["next_cursor"], payload["has_more"])
