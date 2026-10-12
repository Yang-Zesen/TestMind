"""Real service checks; use a migrated disposable PostgreSQL/Neo4j test environment."""

import asyncio
import os
from uuid import uuid4

import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from testmind.infrastructure.graph_sync.projection import Neo4jProjection
from testmind.infrastructure.postgres.graph_sync_repository import GraphSyncRepository
from testmind.models.graph_sync import GraphEvent

pytestmark = pytest.mark.integration


async def test_real_postgres_concurrent_receipts_and_atomic_pull_watermark():
    dsn = os.getenv("GRAPH_TEST_DSN")
    if not dsn:
        pytest.skip("GRAPH_TEST_DSN is required (apply graph_sync migrations first)")
    project = f"integration-{uuid4()}"
    event = GraphEvent(event_id=str(uuid4()), source_instance=project, project_id=project,
                       entity_type="requirement", source_id="1", revision=1, operation="upsert")
    async with AsyncConnectionPool(dsn, open=False, kwargs={"autocommit": True, "row_factory": dict_row}) as pool:
        repository = GraphSyncRepository(pool)
        try:
            results = await asyncio.gather(*(repository.register_event(event, actor_id="test") for _ in range(3)))
            assert len({result["job_id"] for result in results}) == 1
            assert sum(not result["replayed"] for result in results) == 1
            await repository.register_page(source_instance=project, project_id=project,
                resource_type="requirement", events=(event,), expected_cursor=None, next_cursor="next")
            assert await repository.watermark(source_instance=project, project_id=project, resource_type="requirement") == "next"
            changed = event.model_copy(update={"properties": {"conflict": True}, "event_id": str(uuid4())})
            with pytest.raises(ValueError):
                await repository.register_page(source_instance=project, project_id=project,
                    resource_type="requirement", events=(changed,), expected_cursor="next", next_cursor="bad")
            assert await repository.watermark(source_instance=project, project_id=project, resource_type="requirement") == "next"
        finally:
            async with pool.connection() as connection, connection.transaction():
                for table in ("event_receipts", "projection_outbox", "entities", "audit_events", "sync_watermarks"):
                    await connection.execute(f"DELETE FROM graph_sync.{table} WHERE project_id=%s", (project,))


async def test_real_neo4j_revision_fence_and_delete_prevents_resurrection():
    uri = os.getenv("GRAPH_TEST_NEO4J_URI")
    user = os.getenv("GRAPH_TEST_NEO4J_USER")
    password = os.getenv("GRAPH_TEST_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        pytest.skip("GRAPH_TEST_NEO4J_URI/USER/PASSWORD are required")
    from neo4j import AsyncGraphDatabase

    project = f"integration-{uuid4()}"
    generation = str(uuid4())
    event = GraphEvent(event_id="1", source_instance="testmind", project_id=project,
                       entity_type="bug", source_id="1", revision=2, operation="upsert")
    async with AsyncGraphDatabase.driver(uri, auth=(user, password)) as driver:
        projection = Neo4jProjection(driver, generation=generation)
        try:
            await projection.ensure_constraints()
            assert await projection.apply(event)
            assert not await projection.apply(event.model_copy(update={"revision": 1}))
            assert await projection.apply(event.model_copy(update={"revision": 3, "operation": "delete"}))
            assert not await projection.apply(event)
            async with driver.session() as session:
                record = await (await session.run(
                    "MATCH (n:GraphEntity {entity_key:$key,generation:$generation}) RETURN n.revision AS revision,n.tombstoned AS deleted",
                    key=event.entity_key, generation=generation)).single()
                assert record["revision"] == 3 and record["deleted"] is True
        finally:
            async with driver.session() as session:
                result = await session.run("MATCH (n:GraphEntity {generation:$generation}) WHERE n.project_id=$project DETACH DELETE n",
                                          generation=generation, project=project)
                await result.consume()


async def test_real_rebuild_cannot_switch_after_lease_expires_waiting_for_project_lock():
    dsn = os.getenv("GRAPH_TEST_DSN")
    if not dsn:
        pytest.skip("GRAPH_TEST_DSN is required (migrated disposable test database)")
    project = f"integration-{uuid4()}"
    async with AsyncConnectionPool(dsn, open=False, kwargs={"autocommit": True, "row_factory": dict_row}) as pool:
        repository = GraphSyncRepository(pool)
        task = None
        try:
            await repository.enqueue_maintenance(project_id=project, operation="rebuild", actor_id="test")
            job = await repository.claim(lease_seconds=1, project_id=project)
            assert job is not None
            async with pool.connection() as blocker, blocker.transaction():
                await blocker.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"project:{project}",))
                task = asyncio.create_task(repository.switch_generation(project, "new", {},
                    job_id=job["id"], claim_token=job["claim_token"]))
                await asyncio.sleep(1.4)
            with pytest.raises(PermissionError):
                await task
            assert await repository.generation(project) == "initial"
            replacement = await repository.claim(project_id=project)
            assert replacement["claim_token"] != job["claim_token"]
            assert not await repository.finish(job_id=job["id"], claim_token=job["claim_token"])
            assert await repository.finish(job_id=replacement["id"], claim_token=replacement["claim_token"])
        finally:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            async with pool.connection() as connection, connection.transaction():
                for table in ("projection_outbox", "audit_events", "project_generations"):
                    await connection.execute(f"DELETE FROM graph_sync.{table} WHERE project_id=%s", (project,))
