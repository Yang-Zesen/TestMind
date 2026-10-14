import os
from uuid import uuid4

import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

pytestmark = pytest.mark.integration


async def test_source_polling_watermark_replay_and_delete_are_durable():
    if not os.getenv("DEMO_TEST_DSN"):
        pytest.skip("DEMO_TEST_DSN required")
    from testmind.infrastructure.knowledge.demo_source import DemoSourcePoller
    from testmind.infrastructure.postgres.case_repository import CaseRepository
    from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
    from testmind.models.test_case import CaseSourceInput

    async with AsyncConnectionPool(os.environ["DEMO_TEST_DSN"], open=False, kwargs={"row_factory":dict_row,"autocommit":True}) as pool:
        await pool.open()
        cases = CaseRepository(pool)
        source = await cases.create_source(CaseSourceInput(project_id="demo-project", kind="requirement", title=str(uuid4()), content="密码错误5次锁定"), "demo-user")
        poller = DemoSourcePoller(cases, KnowledgeRepository(pool))
        await poller.poll()
        await poller.poll()
        async with pool.connection() as connection:
            cursor = await connection.execute("SELECT count(*) AS n FROM knowledge.change_receipts WHERE source_event_id=%s", (f"{source['id']}:1",))
            assert (await cursor.fetchone())["n"] == 1
        await cases.update_source(source["id"], "demo-user", version=1, content="密码错误3次锁定")
        await poller.poll()
        async with pool.connection() as connection:
            cursor = await connection.execute("SELECT version FROM business.source_watermarks WHERE source_id=%s", (source["id"],))
            assert (await cursor.fetchone())["version"] == 2
