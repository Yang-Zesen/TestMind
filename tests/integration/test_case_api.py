import os
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from testmind.api.tasks import router as tasks_router
from testmind.api.test_cases import router
from testmind.core.authentication import DemoAuthorizationProvider
from testmind.demo.runtime import create_demo_graph
from testmind.infrastructure.postgres.case_repository import CaseRepository
from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
from testmind.infrastructure.postgres.task_repository import TaskRepository
from testmind.services.case_publication import CasePublicationService, DemoCaseWriter
from testmind.worker.task_worker import TaskWorker

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("kind,content", [("requirement", "连续失败5次锁定账号30分钟。"), ("openapi", '{"openapi":"3.0.3","paths":{"/orders":{"post":{"requestBody":{"content":{"application/json":{"schema":{"type":"object","required":["count"],"properties":{"count":{"type":"integer","minimum":1,"maximum":10}}}}}},"responses":{"201":{"description":"Created"},"400":{"description":"Invalid"}}}}}}')])
async def test_requirement_and_openapi_design_review_publish_api(kind, content):
    if not os.getenv("DEMO_TEST_DSN"):
        pytest.skip("DEMO_TEST_DSN required")
    async with AsyncConnectionPool(os.environ["DEMO_TEST_DSN"], open=False, kwargs={"row_factory":dict_row,"autocommit":True}) as pool:
        await pool.open()
        tasks, cases = TaskRepository(pool), CaseRepository(pool)
        app = FastAPI()
        app.include_router(router)
        app.include_router(tasks_router)
        app.state.task_repository = tasks
        app.state.case_repository = cases
        app.state.case_publication = CasePublicationService(cases, DemoCaseWriter(cases))
        app.state.authorization_provider = DemoAuthorizationProvider(tasks)
        worker = TaskWorker(tasks, create_demo_graph(InMemorySaver(), tasks), KnowledgeRepository(pool))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            headers = {"X-Demo-User":"demo-user"}
            payload = {"project_id":"demo-project", "kind":kind, "title":"登录安全", "content":content}
            assert (await client.post('/api/v1/case-sources', json=payload)).status_code == 401
            source = (await client.post('/api/v1/case-sources', json=payload, headers=headers)).json()
            request = {"project_id":"demo-project", "source_ids":[source['id']], "session_id":str(uuid4())}
            assert (await client.post('/api/v1/case-design',json=request,headers={"X-Demo-User":"demo-other","Idempotency-Key":str(uuid4())})).status_code == 403
            task = (await client.post('/api/v1/case-design',json=request,headers=headers|{"Idempotency-Key":str(uuid4())})).json()
            await worker.run_once(task_id=task['task_id'])
            assert (await tasks.get(task['task_id']))['status'] == 'waiting_review'
            assets = (await client.get(f"/api/v1/tasks/{task['task_id']}/cases",headers=headers)).json()
            assert {asset['body']['kind'] for asset in assets} == {'positive','negative','boundary'}
            asset = assets[0]
            version = {"version":asset['version'],"content_hash":asset['content_hash']}
            path = f"/api/v1/test-cases/{asset['id']}"
            assert (await client.post(path+'/publish',json=version,headers=headers)).status_code == 409
            assert (await client.post(path+'/review',json=version|{"decision":"approved"},headers={"X-Demo-User":"demo-other"})).status_code == 404
            assert (await client.post(path+'/review',json=version|{"decision":"approved"},headers=headers)).status_code == 200
            published = (await client.post(path+'/publish',json=version,headers=headers)).json()
            assert published['status'] == 'completed' and published['mode'] == 'local_simulation'
            assert published['external_id'].startswith('DEMO-')
            for remaining in assets[1:]:
                value = {"version":remaining['version'],"content_hash":remaining['content_hash'],"decision":"rejected"}
                assert (await client.post(f"/api/v1/test-cases/{remaining['id']}/review",json=value,headers=headers)).status_code == 200
            assert (await tasks.get(task['task_id']))['status'] == 'completed'
