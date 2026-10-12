"""Durable invalidation delivery and PostgreSQL-driven reconciliation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class InvalidationEvent:
    event_id: str
    dedupe_key: str
    payload: dict[str, Any]


class InvalidationSink(Protocol):
    async def publish(self, event: InvalidationEvent) -> None: ...


class InvalidationRepository(Protocol):
    async def pending_invalidations(self) -> list[InvalidationEvent]: ...

    async def mark_invalidation_published(self, event_id: str) -> bool: ...

    async def active_milvus_targets(self) -> list[dict[str, str]]: ...

    async def tombstoned_documents(self) -> list[dict[str, str]]: ...


class InvalidationPublisher:
    def __init__(self, repository: InvalidationRepository, sink: InvalidationSink) -> None:
        self._repository = repository
        self._sink = sink

    async def flush(self) -> int:
        published = 0
        for event in await self._repository.pending_invalidations():
            await self._sink.publish(event)
            if await self._repository.mark_invalidation_published(event.event_id):
                published += 1
        return published


class ReconciliationService:
    def __init__(self, repository: InvalidationRepository, sink: InvalidationSink) -> None:
        self._repository = repository
        self._sink = sink

    async def desired_state(self) -> list[dict[str, str]]:
        return await self._repository.active_milvus_targets()

    async def tombstones(self) -> list[dict[str, str]]:
        return await self._repository.tombstoned_documents()
