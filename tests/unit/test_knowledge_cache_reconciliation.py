import pytest

from testmind.infrastructure.knowledge.cache_invalidation import (
    InvalidationEvent,
    InvalidationPublisher,
    ReconciliationService,
)


class FakePublisher:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class FakeRepository:
    async def pending_invalidations(self):
        return [InvalidationEvent("event-1", "activate:p1:kb1:d1", {"project_id": "p1"})]

    async def mark_invalidation_published(self, event_id):
        return event_id == "event-1"

    async def active_milvus_targets(self):
        return [{"project_id": "p1", "kb_id": "kb1", "document_id": "d1", "document_version": "v1", "index_version": "i1"}]

    async def tombstoned_documents(self):
        return []


@pytest.mark.asyncio
async def test_invalidation_publisher_is_idempotent_and_reconciliation_uses_pg_targets():
    repository = FakeRepository()
    publisher = FakePublisher()
    service = InvalidationPublisher(repository, publisher)
    assert await service.flush() == 1
    assert await service.flush() == 1
    assert len(publisher.events) == 2

    reconciler = ReconciliationService(repository, FakePublisher())
    assert await reconciler.desired_state() == await repository.active_milvus_targets()
