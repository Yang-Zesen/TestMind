import asyncio

import pytest

from testmind.worker.knowledge_worker import KnowledgeWorker, RetryableJobError


class FakeRepository:
    def __init__(self):
        self.job = {"id": "job-1", "operation": "ingest", "correlation_id": "corr-1"}
        self.retries = []
        self.completed = []

    async def claim_next_job(self, **kwargs):
        job, self.job = self.job, None
        return job

    async def heartbeat(self, **kwargs):
        return True

    async def schedule_retry(self, **kwargs):
        self.retries.append(kwargs)
        return "failed_retryable"

    async def fail_permanently(self, **kwargs):
        return True

    async def complete_job(self, **kwargs):
        self.completed.append(kwargs)
        return True

    async def finalize_expired_dead_letters(self):
        return 0


@pytest.mark.asyncio
async def test_worker_completes_claimed_job_and_stops_heartbeat():
    repository = FakeRepository()
    handled = []

    async def handle(job):
        handled.append(job["id"])

    worker = KnowledgeWorker(
        repository,
        handlers={"ingest": handle},
        worker_id="worker-1",
        lease_seconds=30,
        heartbeat_seconds=10,
    )

    assert await worker.run_once()
    assert handled == ["job-1"]
    assert repository.completed == [{"job_id": "job-1", "worker_id": "worker-1"}]


@pytest.mark.asyncio
async def test_worker_schedules_retryable_infrastructure_failure():
    repository = FakeRepository()

    async def fail(job):
        raise RetryableJobError("milvus_unavailable")

    worker = KnowledgeWorker(
        repository,
        handlers={"ingest": fail},
        worker_id="worker-1",
        lease_seconds=30,
        heartbeat_seconds=10,
    )

    assert await worker.run_once()
    assert repository.retries[0]["error_code"] == "milvus_unavailable"
