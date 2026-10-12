"""Adapter-driven full/incremental pulls with atomic page watermarks."""

from dataclasses import dataclass
from typing import Any, Protocol

from testmind.models.graph_sync import GraphEvent


@dataclass(frozen=True)
class PollPage:
    events: tuple[GraphEvent, ...]
    next_cursor: Any
    has_more: bool


class GraphSourceAdapter(Protocol):
    async def fetch_page(self, project_id: str, resource_type: str, cursor: Any) -> PollPage: ...


class GraphPoller:
    def __init__(self, repository: Any, source: GraphSourceAdapter, *, source_instance: str) -> None:
        self.repository = repository
        self.source = source
        self.source_instance = source_instance

    async def run(self, project_id: str, resource_type: str) -> int:
        stream = {"source_instance": self.source_instance, "project_id": project_id,
                  "resource_type": resource_type}
        cursor = await self.repository.watermark(**stream)
        count = 0
        for _ in range(10000):
            page = await self.source.fetch_page(project_id, resource_type, cursor)
            if any(event.project_id != project_id or event.source_instance != self.source_instance
                   or event.entity_type != resource_type for event in page.events):
                raise PermissionError("Pull adapter returned data outside the configured stream")
            if page.has_more and page.next_cursor == cursor:
                raise ValueError("Pull adapter did not advance its cursor")
            await self.repository.register_page(
                **stream, events=page.events, expected_cursor=cursor, next_cursor=page.next_cursor,
            )
            count += len(page.events)
            cursor = page.next_cursor
            if not page.has_more:
                return count
        raise RuntimeError("Pull exceeded maximum page count; committed cursor permits resume")
