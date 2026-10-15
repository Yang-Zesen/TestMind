import os
from uuid import uuid4

import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from testmind.infrastructure.postgres.case_repository import CaseRepository
from testmind.infrastructure.postgres.task_repository import TaskRepository
from testmind.models.test_case import CaseSourceInput, design_cases
from testmind.services.case_publication import CasePublicationService, DemoCaseWriter

pytestmark = pytest.mark.integration


async def test_publication_outbox_is_atomic_and_replay_never_republishes():
    if not os.getenv("DEMO_TEST_DSN"):
        pytest.skip("DEMO_TEST_DSN required")
    async with AsyncConnectionPool(os.environ["DEMO_TEST_DSN"], open=False,
        kwargs={"row_factory": dict_row, "autocommit": True}) as pool:
        await pool.open()
        cases, tasks = CaseRepository(pool), TaskRepository(pool)
        source_input = CaseSourceInput(project_id="demo-project", kind="requirement", title="P5 atomic source", content="Login locks after five failed attempts.")
        async with pool.connection() as connection, connection.transaction():
            try:
                async with connection.transaction():
                    from testmind.infrastructure.postgres.graph_sync_repository import (
                        _ConnectionPool,
                    )
                    await CaseRepository(_ConnectionPool(connection)).create_source(source_input, "demo-user")
                    raise RuntimeError("rollback")
            except RuntimeError:
                pass
            cursor = await connection.execute("SELECT count(*) AS n FROM business.case_sources WHERE title=%s", (source_input.title,))
            before = (await cursor.fetchone())["n"]
        source = await cases.create_source(source_input, "demo-user")
        task = await tasks.submit(user_id="demo-user", project_id="demo-project", session_id=str(uuid4()), query="case design", idempotency_key=str(uuid4()))
        claim = await tasks.claim(task_id=task["id"])
        assets = await cases.save_drafts(claim, [source], design_cases(source_input), [])
        await tasks.finish(claim, status="waiting_review", result={"answer": "draft"})
        asset = assets[0]
        await cases.decide(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"], decision="approved")
        service = CasePublicationService(cases, DemoCaseWriter(cases))
        operation = await service.publish(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"])
        await cases.complete_operation(operation["id"], operation["external_id"])
        async with pool.connection() as connection:
            cursor = await connection.execute("SELECT payload FROM graph_sync.entities WHERE source_instance='testmind-case' AND source_id=%s", (asset["id"],))
            event = (await cursor.fetchone())["payload"]
            assert event["properties"]["external_id"] == operation["external_id"]
            assert event["properties"]["decision"] == "approved"
            assert event["relations"][0]["target_source_id"] == source["id"]
            cursor = await connection.execute("SELECT count(*) AS n FROM graph_sync.projection_outbox WHERE payload->>'source_id'=%s", (asset["id"],))
            assert (await cursor.fetchone())["n"] == 3
            cursor = await connection.execute("SELECT count(*) AS n FROM business.case_sources WHERE title=%s", (source_input.title,))
            assert (await cursor.fetchone())["n"] == before + 1
        assert await cases.demo_count(asset["id"]) == 1


async def test_history_omits_stale_or_ungranted_evidence():
    if not os.getenv("DEMO_TEST_DSN"):
        pytest.skip("DEMO_TEST_DSN required")
    async with AsyncConnectionPool(os.environ["DEMO_TEST_DSN"], open=False,
        kwargs={"row_factory": dict_row, "autocommit": True}) as pool:
        await pool.open()
        tasks = TaskRepository(pool)
        values = {"user_id": "demo-user", "project_id": "demo-project", "session_id": str(uuid4()), "query": "history", "idempotency_key": str(uuid4())}
        first = await tasks.submit(**values)
        claim = await tasks.claim(task_id=first["id"])
        await tasks.finish(claim, status="completed", result={"answer": "restricted old answer", "citations": [{"knowledge_base_id": "other-kb", "document_id": str(uuid4()), "document_version": "1", "index_version": "1"}]})
        next_task = await tasks.submit(**(values | {"idempotency_key": str(uuid4())}))
        assert await tasks.history(next_task) == []
        assert not await tasks.result_accessible(await tasks.get(first["id"]), await tasks.context("demo-user"))
