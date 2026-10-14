import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from testmind.infrastructure.postgres.task_repository import TaskRepository

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def cases():
    if not os.getenv("DEMO_TEST_DSN"):
        pytest.skip("DEMO_TEST_DSN required")
    from testmind.infrastructure.postgres.case_repository import CaseRepository

    async with AsyncConnectionPool(os.environ["DEMO_TEST_DSN"], open=False, kwargs={"row_factory":dict_row,"autocommit":True}) as pool:
        await pool.open()
        yield CaseRepository(pool)


async def make_asset(cases):
    from testmind.models.test_case import CaseSourceInput, design_cases

    payload = CaseSourceInput(project_id="demo-project", kind="requirement", title="登录", content="连续失败5次锁定账号。")
    source = await cases.create_source(payload, "demo-user")
    tasks = TaskRepository(cases.pool)
    task = await tasks.submit(user_id="demo-user", project_id="demo-project", session_id=str(uuid4()), query="case design", idempotency_key=str(uuid4()))
    claim = await tasks.claim(task_id=task["id"])
    assets = await cases.save_drafts(claim, [source], design_cases(payload), [])
    await tasks.finish(claim, status="waiting_review", result={"answer":"drafts"})
    return source, assets[0]


async def test_edit_invalidates_approval_and_old_version_cannot_publish(cases):
    from testmind.infrastructure.postgres.case_repository import CaseConflict
    from testmind.models.test_case import CaseBody

    _source, asset = await make_asset(cases)
    await cases.decide(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"], decision="approved")
    modified = await cases.edit(asset["id"], "demo-user", version=1, body=CaseBody.model_validate({**asset["body"], "title":"修改后的用例"}))
    assert modified["version"] == 2 and modified["decision"] == "pending"
    with pytest.raises(CaseConflict):
        await cases.prepare_publish(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"])
    assert await cases.demo_count(asset["id"]) == 0


async def test_unapproved_or_changed_source_never_writes(cases):
    from testmind.infrastructure.postgres.case_repository import CaseConflict

    source, asset = await make_asset(cases)
    with pytest.raises(CaseConflict):
        await cases.prepare_publish(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"])
    await cases.decide(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"], decision="approved")
    await cases.update_source(source["id"], "demo-user", version=1, content="规则改为连续失败3次锁定。")
    with pytest.raises(CaseConflict, match="source"):
        await cases.prepare_publish(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"])
    assert await cases.demo_count(asset["id"]) == 0


async def test_concurrent_publish_is_idempotent_and_returns_persisted_demo_id(cases):
    from testmind.services.case_publication import CasePublicationService, DemoCaseWriter

    _, asset = await make_asset(cases)
    await cases.decide(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"], decision="approved")
    service = CasePublicationService(cases, DemoCaseWriter(cases))
    results = await asyncio.gather(*(service.publish(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"]) for _ in range(2)))
    assert all(result["status"] in {"completed","in_flight"} for result in results)
    done = await service.publish(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"])
    assert done["external_id"].startswith("DEMO-")
    assert await cases.demo_count(asset["id"]) == 1


async def test_timeout_requires_reconciliation_without_blind_resend(cases):
    from testmind.services.case_publication import CasePublicationService, DemoCaseWriter

    _, asset = await make_asset(cases)
    await cases.decide(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"], decision="approved")
    class LostResponse(DemoCaseWriter):
        calls = 0
        async def create(self, operation):
            self.calls += 1
            await super().create(operation)
            raise TimeoutError()
        async def lookup(self, operation):
            return None
    writer = LostResponse(cases)
    service = CasePublicationService(cases, writer)
    result = await service.publish(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"])
    assert result["status"] == "needs_reconciliation"
    again = await service.publish(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"])
    assert again["status"] == "needs_reconciliation" and writer.calls == 1
    repaired = await CasePublicationService(cases, DemoCaseWriter(cases)).reconcile(result["id"], "demo-user")
    assert repaired["status"] == "completed" and await cases.demo_count(asset["id"]) == 1


async def test_prepared_operation_rechecks_source_before_dispatch(cases):
    from testmind.infrastructure.postgres.case_repository import CaseConflict

    source, asset = await make_asset(cases)
    await cases.decide(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"], decision="approved")
    operation = await cases.prepare_publish(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"])
    await cases.update_source(source["id"], "demo-user", version=1, content="来源已变化")
    with pytest.raises(CaseConflict, match="source"):
        await cases.mark_in_flight(operation["id"])
    assert await cases.demo_count(asset["id"]) == 0


async def test_cancelled_waiting_review_task_cannot_dispatch(cases):
    from testmind.infrastructure.postgres.case_repository import CaseConflict

    _, asset = await make_asset(cases)
    await cases.decide(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"], decision="approved")
    await TaskRepository(cases.pool).cancel(asset["task_id"])
    with pytest.raises(CaseConflict, match="task"):
        await cases.prepare_publish(asset["id"], "demo-user", version=1, content_hash=asset["content_hash"])


async def test_evidence_without_current_kb_access_cannot_publish(cases):
    from testmind.infrastructure.postgres.case_repository import CaseConflict

    _, asset = await make_asset(cases)
    # An unknown KB must be denied before consulting document metadata.
    asset["evidence"] = [{"knowledge_base_id":"ungranted-kb", "document_id":str(uuid4()),
                          "document_version":"v1", "index_version":"i1"}]
    async with cases.pool.connection() as connection:
        with pytest.raises(CaseConflict, match="evidence_access"):
            await cases._validate_sources(connection, asset)
