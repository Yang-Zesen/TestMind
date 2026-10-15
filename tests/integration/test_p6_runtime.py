import os
from uuid import uuid4

import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from testmind.infrastructure.postgres.task_repository import (
    TaskCapacityError,
    TaskConflict,
    TaskRepository,
    TaskTransitionError,
)
from testmind.services.runtime_status import component_heartbeat, runtime_snapshot

pytestmark = pytest.mark.integration


async def test_queue_capacity_replay_retry_budget_and_component_health():
    if not os.getenv("DEMO_TEST_DSN"):
        pytest.skip("DEMO_TEST_DSN required")
    async with AsyncConnectionPool(os.environ["DEMO_TEST_DSN"], open=False, kwargs={"row_factory": dict_row, "autocommit": True}) as pool:
        await pool.open()
        project = "p6-" + uuid4().hex
        async with pool.connection() as connection:
            await connection.execute("INSERT INTO business.demo_grants(user_id,project_id,kb_id) VALUES ('demo-user',%s,'demo-kb')", (project,))
        repo = TaskRepository(pool, queue_limit=1)
        before = await runtime_snapshot(pool, project)
        previous_failures = sum(row["failures"] for row in before["metrics_last_hour"] if row["component"] == "rag" and row["metric"] == "retrieval")
        await repo.record_metric("rag", "retrieval", 0.25, failed=True)
        values = {"user_id": "demo-user", "project_id": project, "session_id": str(uuid4()), "query": "test", "idempotency_key": str(uuid4())}
        task = await repo.submit(**values)
        assert (await repo.submit(**values))["replayed"]
        with pytest.raises(TaskCapacityError):
            await repo.submit(**(values | {"idempotency_key": str(uuid4())}))
        claim = await repo.claim(task_id=task["id"])
        await repo.finish(claim, status="failed", result={}, error_code="task_timeout")
        task = await repo.get(task["id"])
        with pytest.raises(TaskConflict):
            await repo.retry(task["id"], user_id="demo-user", expected_version=1)
        for _ in range(3):
            await repo.retry(task["id"], user_id="demo-user", expected_version=task["version"])
            claim = await repo.claim(task_id=task["id"])
            await repo.finish(claim, status="failed", result={}, error_code="task_timeout")
            task = await repo.get(task["id"])
        with pytest.raises(TaskTransitionError):
            await repo.retry(task["id"], user_id="demo-user", expected_version=task["version"])
        async with component_heartbeat(pool, "worker", model_ready=True):
            snapshot = await runtime_snapshot(pool, project)
            assert next(item for item in snapshot["components"] if item["component"] == "worker")["healthy"]
            assert snapshot["failures"][0]["error_code"] == "task_timeout"
            assert next(row for row in snapshot["metrics_last_hour"] if row["component"] == "rag" and row["metric"] == "retrieval")["failures"] >= previous_failures + 1
            assert all("query" not in item and "result" not in item for item in snapshot["failures"])
        snapshot = await runtime_snapshot(pool, project)
        assert any(alert["component"] == "worker" and alert["code"] == "component_unavailable" for alert in snapshot["alerts"])
