"""HTTP authorization and replay against the real demo PostgreSQL store."""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from testmind.api.approvals import router as approvals_router
from testmind.api.tasks import router
from testmind.core.authentication import DemoAuthorizationProvider
from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
from testmind.infrastructure.postgres.task_repository import TaskRepository
from testmind.worker.task_worker import TaskWorker

pytestmark = pytest.mark.integration


async def test_authentication_isolation_revocation_and_event_replay():
    dsn = os.getenv("DEMO_TEST_DSN")
    if not dsn:
        pytest.skip("DEMO_TEST_DSN is required")
    async with AsyncConnectionPool(dsn, open=False, kwargs={"row_factory": dict_row, "autocommit": True}) as pool:
        await pool.open()
        repo = TaskRepository(pool)
        app = FastAPI()
        app.include_router(router)
        app.include_router(approvals_router)
        app.state.task_repository = repo
        app.state.authorization_provider = DemoAuthorizationProvider(repo)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            payload = {"query": "What is pytest?", "project_id": "demo-project", "session_id": str(uuid4())}
            headers = {"Idempotency-Key": str(uuid4()), "X-Demo-User": "demo-user"}
            assert (await client.post("/api/v1/tasks", json=payload)).status_code == 401
            assert (await client.post("/api/v1/tasks", json=payload, headers=headers | {"X-Demo-User": "unknown"})).status_code == 401
            assert (await client.post("/api/v1/tasks", json=payload | {"project_id": "other-project"}, headers=headers)).status_code == 403
            accepted = await client.post("/api/v1/tasks", json=payload, headers=headers)
            assert accepted.status_code == 202
            task_id = accepted.json()["task_id"]
            assert (await client.post("/api/v1/tasks", json=payload, headers=headers)).json()["task_id"] == task_id
            assert (await client.get(f"/api/v1/tasks/{task_id}", headers={"X-Demo-User": "demo-other"})).status_code == 404
            class Graph:
                calls = 0
                async def ainvoke(self, state, **kwargs):
                    self.calls += 1
                    return {"status": "completed", "answer": "Pytest is a testing framework.", "intent": "dialogue"}
            graph = Graph()
            await TaskWorker(repo, graph, KnowledgeRepository(pool)).run_once(task_id=task_id)
            assert (await client.get(f"/api/v1/tasks/{task_id}", headers=headers)).json()["result"]["answer"].startswith("Pytest")
            stream = await client.get(f"/api/v1/tasks/{task_id}/events", headers=headers | {"Last-Event-ID": "1"})
            assert "id: 1\n" not in stream.text and "id: 2\n" in stream.text
            assert graph.calls == 1
            approval = await repo.create_approval(task_id, asset_version=1, content_hash="abc")
            decision = {"decision": "approved", "asset_version": 1, "content_hash": "abc"}
            path = f'/api/v1/approvals/{approval["id"]}/decision'
            assert (await client.post(path, json=decision, headers={"X-Demo-User": "demo-other"})).status_code == 404
            assert (await client.post(path, json=decision | {"asset_version": 2}, headers=headers)).status_code == 409
            assert (await client.post(path, json=decision, headers=headers)).status_code == 200
            assert (await client.post(path, json=decision, headers=headers)).status_code == 409
            async with pool.connection() as connection:
                await connection.execute("UPDATE business.demo_grants SET revoked=true WHERE user_id='demo-user'")
            try:
                assert (await client.get(f"/api/v1/tasks/{task_id}", headers=headers)).status_code == 403
            finally:
                async with pool.connection() as connection:
                    await connection.execute("UPDATE business.demo_grants SET revoked=false WHERE user_id='demo-user'")


async def test_worker_timeout_retry_and_running_cancel():
    dsn = os.getenv("DEMO_TEST_DSN")
    if not dsn:
        pytest.skip("DEMO_TEST_DSN is required")
    async with AsyncConnectionPool(dsn, open=False, kwargs={"row_factory": dict_row, "autocommit": True}) as pool:
        await pool.open()
        repo = TaskRepository(pool)
        values = {"user_id": "demo-user", "project_id": "demo-project", "session_id": str(uuid4()), "query": "hello", "idempotency_key": str(uuid4())}
        task = await repo.submit(**values)
        class SlowGraph:
            cancelled = False
            async def ainvoke(self, *args, **kwargs):
                try:
                    await asyncio.sleep(10)
                finally:
                    self.cancelled = True
        graph = SlowGraph()
        await TaskWorker(repo, graph, KnowledgeRepository(pool), timeout_seconds=0.5).run_once(task_id=task["id"])
        assert (await repo.get(task["id"]))["error_code"] == "task_timeout"
        assert graph.cancelled
        task = await repo.submit(**(values | {"idempotency_key": str(uuid4())}))
        running = asyncio.create_task(TaskWorker(repo, graph, KnowledgeRepository(pool)).run_once(task_id=task["id"]))
        for _ in range(100):
            if (await repo.get(task["id"]))["status"] == "running":
                break
            await asyncio.sleep(0.01)
        await repo.cancel(task["id"])
        await running
        assert (await repo.get(task["id"]))["status"] == "cancelled"
