import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from testmind.infrastructure.postgres.task_repository import (
    TaskConflict,
    TaskRepository,
    TaskTransitionError,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def repository():
    dsn = os.getenv("DEMO_TEST_DSN")
    if not dsn:
        pytest.skip("DEMO_TEST_DSN is required")
    async with AsyncConnectionPool(dsn, open=False, kwargs={"row_factory": dict_row, "autocommit": True}) as pool:
        await pool.open()
        yield TaskRepository(pool)


def submission():
    return {"user_id": "demo-user", "project_id": "demo-project", "session_id": str(uuid4()), "query": "hello", "idempotency_key": str(uuid4())}


async def test_concurrent_idempotency_and_transactional_events(repository):
    values = submission()
    first, second = await asyncio.gather(repository.submit(**values), repository.submit(**values))
    assert first["id"] == second["id"]
    assert len(await repository.events(first["id"], after=0)) == 1
    with pytest.raises(TaskConflict):
        await repository.submit(**(values | {"query": "different"}))


async def test_lease_reclaim_fences_old_worker_and_persists_result(repository):
    task = await repository.submit(**submission())
    old = await repository.claim(task_id=task["id"], lease_seconds=0.02)
    await asyncio.sleep(0.04)
    current = await repository.claim(task_id=task["id"], lease_seconds=60)
    assert old["claim_token"] != current["claim_token"]
    assert not await repository.finish(old, status="completed", result={"answer": "stale"})
    assert await repository.finish(current, status="completed", result={"answer": "persisted"})
    reopened = TaskRepository(repository.pool)
    assert (await reopened.get(task["id"]))["result"]["answer"] == "persisted"
    events = await reopened.events(task["id"], after=1)
    assert [e["seq"] for e in events] == [2, 3, 4]
    assert await reopened.claim(task_id=task["id"]) is None


async def test_resume_version_and_cancel_terminal_fencing(repository):
    task = await repository.submit(**submission())
    claim = await repository.claim(task_id=task["id"])
    await repository.finish(claim, status="waiting_input", result={"clarification_question": "Which project?"})
    waiting = await repository.get(task["id"])
    with pytest.raises(TaskConflict):
        await repository.resume(task["id"], expected_version=0, query="answer")
    await repository.resume(task["id"], expected_version=waiting["version"], query="answer")
    await repository.cancel(task["id"])
    with pytest.raises(TaskTransitionError):
        await repository.resume(task["id"], expected_version=waiting["version"], query="answer")
    assert (await repository.get(task["id"]))["status"] == "cancelled"


async def test_approval_is_bound_to_asset_version_and_hash(repository):
    task = await repository.submit(**submission())
    approval = await repository.create_approval(task["id"], asset_version=1, content_hash="abc")
    with pytest.raises(TaskConflict):
        await repository.decide_approval(approval["id"], reviewer="demo-user", decision="approved", asset_version=2, content_hash="abc")
    decided = await repository.decide_approval(approval["id"], reviewer="demo-user", decision="approved", asset_version=1, content_hash="abc")
    assert decided["decision"] == "approved"
    await repository.invalidate_approval(approval["id"])
    with pytest.raises(TaskConflict):
        await repository.decide_approval(approval["id"], reviewer="demo-user", decision="approved", asset_version=1, content_hash="abc")


async def test_persisted_execution_deadline_stops_reclaimed_task(repository):
    task = await repository.submit(**submission())
    await repository.claim(task_id=task["id"], lease_seconds=60)
    async with repository.pool.connection() as connection:
        await connection.execute("UPDATE business.tasks SET lease_expires_at=now()-interval '1 second',execution_deadline_at=now()-interval '1 second' WHERE id=%s", (task["id"],))
    assert await repository.claim(task_id=task["id"]) is None
    current = await repository.get(task["id"])
    assert current["status"] == "failed"
    assert current["error_code"] == "task_deadline_exhausted"
