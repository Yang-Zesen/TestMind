import asyncio

from testmind.worker.graph_sync_worker import GraphSyncWorker


class Repository:
    def __init__(self):
        self.completed = []
        self.failed = []

    async def claim(self, **kwargs):
        return {"id": "j1", "claim_token": "token1", "payload": {
            "event_id": "e1", "source_instance": "s1", "project_id": "p1",
            "entity_type": "requirement", "source_id": "1", "revision": 1,
            "operation": "upsert"}}

    async def renew(self, **kwargs):
        return True

    async def finish(self, **kwargs):
        self.completed.append(kwargs)
        return True

    async def fail(self, **kwargs):
        self.failed.append(kwargs)


class Projection:
    async def apply(self, event):
        return True


async def test_worker_acknowledges_only_after_projection_with_claim_token():
    repository = Repository()
    worker = GraphSyncWorker(repository, Projection())
    assert await worker.run_once()
    assert repository.completed == [{"job_id": "j1", "claim_token": "token1"}]


async def test_neo4j_failure_is_retryable_and_never_acknowledged():
    class BrokenProjection:
        async def apply(self, event):
            raise ConnectionError("unavailable")

    repository = Repository()
    await GraphSyncWorker(repository, BrokenProjection()).run_once()
    assert not repository.completed
    assert repository.failed[0]["permanent"] is False


async def test_lost_lease_cancels_projection_without_acknowledging():
    class LostLease(Repository):
        async def renew(self, **kwargs):
            return False

    class SlowProjection:
        async def apply(self, event):
            await asyncio.sleep(1)

    repository = LostLease()
    await GraphSyncWorker(repository, SlowProjection(), heartbeat_seconds=0.01).run_once()
    assert not repository.completed


async def test_queue_database_failure_does_not_terminate_worker_loop():
    worker = GraphSyncWorker(Repository(), Projection(), poll_seconds=0.01)
    attempts = []
    async def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("queue offline")
        worker.stop()
        return False
    worker.run_once = flaky
    await worker.run()
    assert len(attempts) == 3
