"""Real PostgreSQL checkpoints, independent workers and bounded recovery."""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest

from testmind.demo.runtime import create_demo_graph
from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
from testmind.infrastructure.postgres.postgresql_client import open_postgresql_client
from testmind.infrastructure.postgres.task_repository import TaskRepository
from testmind.worker.task_worker import TaskWorker

pytestmark = pytest.mark.integration


async def test_checkpoint_clarification_recovery_and_history_isolation(monkeypatch):
    dsn = os.getenv("DEMO_TEST_DSN")
    if not dsn:
        pytest.skip("DEMO_TEST_DSN is required")
    monkeypatch.setenv("POSTGRES_DSN", dsn)
    monkeypatch.setattr("testmind.core.config._CONFIG", None)
    async with open_postgresql_client() as postgres:
        repo = TaskRepository(postgres.pool)
        values = {
            "user_id": "demo-user",
            "project_id": "demo-project",
            "session_id": str(uuid4()),
            "query": "?",
            "idempotency_key": str(uuid4()),
        }
        task = await repo.submit(**values)
        worker = TaskWorker(
            repo, create_demo_graph(postgres.checkpointer), KnowledgeRepository(postgres.pool)
        )
        await worker.run_once(task_id=task["id"])
        waiting = await repo.get(task["id"])
        assert waiting["status"] == "waiting_input"
        await repo.resume(task["id"], expected_version=waiting["version"], query="What is pytest?")
        # New graph and worker instances load the same durable execution thread.
        restarted = TaskWorker(
            repo, create_demo_graph(postgres.checkpointer), KnowledgeRepository(postgres.pool)
        )
        await restarted.run_once(task_id=task["id"])
        done = await repo.get(task["id"])
        assert done["status"] == "completed"
        assert "Pytest" in done["result"]["answer"]
        assert len(await repo.history(values | {"id": "another-task"})) == 1
        for change in (
            {"session_id": "another-session"},
            {"user_id": "demo-other"},
            {"project_id": "other-project"},
        ):
            assert await repo.history(values | {"id": "another-task"} | change) == []
        assert await restarted.run_once(task_id=task["id"]) is False
        checkpoint = await restarted.graph.aget_state({"configurable": {"thread_id": task["execution_thread_id"]}})
        assert len(checkpoint.values["messages"]) <= 3
        tool_task = await repo.submit(**(values | {"query": "zentao stories 1", "idempotency_key": str(uuid4())}))
        tools_worker = TaskWorker(repo, create_demo_graph(postgres.checkpointer, repo), KnowledgeRepository(postgres.pool))
        await tools_worker.run_once(task_id=tool_task["id"])
        tool_done = await repo.get(tool_task["id"])
        assert tool_done["status"] == "completed"
        assert "Offline demo stories" in tool_done["result"]["answer"]


async def test_transient_retry_budget_and_revocation_during_execution(monkeypatch):
    dsn = os.getenv("DEMO_TEST_DSN")
    if not dsn:
        pytest.skip("DEMO_TEST_DSN is required")
    monkeypatch.setenv("POSTGRES_DSN", dsn)
    monkeypatch.setattr("testmind.core.config._CONFIG", None)
    async with open_postgresql_client() as postgres:
        repo = TaskRepository(postgres.pool)
        values = {
            "user_id": "demo-user",
            "project_id": "demo-project",
            "session_id": str(uuid4()),
            "query": "pytest",
            "idempotency_key": str(uuid4()),
        }
        task = await repo.submit(**values)

        class Unavailable:
            async def ainvoke(self, *args, **kwargs):
                raise httpx.ConnectError("sensitive upstream must not be logged")

        worker = TaskWorker(repo, Unavailable(), KnowledgeRepository(postgres.pool))
        for attempt in range(1, 4):
            assert await worker.run_once(task_id=task["id"])
            current = await repo.get(task["id"])
            assert current["attempt_count"] == attempt
            assert current["status"] == ("queued" if attempt < 3 else "failed")
            async with postgres.pool.connection() as connection:
                await connection.execute(
                    "UPDATE business.tasks SET next_attempt_at=now() WHERE id=%s", (task["id"],)
                )
        assert not await worker.run_once(task_id=task["id"])
        started = asyncio.Event()

        class Slow:
            async def ainvoke(self, *args, **kwargs):
                started.set()
                await asyncio.sleep(30)

        task = await repo.submit(**(values | {"idempotency_key": str(uuid4())}))
        execution = asyncio.create_task(
            TaskWorker(repo, Slow(), KnowledgeRepository(postgres.pool)).run_once(
                task_id=task["id"]
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        try:
            async with postgres.pool.connection() as connection:
                await connection.execute(
                    "UPDATE business.demo_grants SET revoked=true WHERE user_id='demo-user'"
                )
            await asyncio.wait_for(execution, timeout=5)
            assert (await repo.get(task["id"]))["status"] == "denied"
        finally:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            async with postgres.pool.connection() as connection:
                await connection.execute(
                    "UPDATE business.demo_grants SET revoked=false WHERE user_id='demo-user'"
                )
