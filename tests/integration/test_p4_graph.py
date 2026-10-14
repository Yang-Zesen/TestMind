import os
from uuid import uuid4

import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from testmind.infrastructure.postgres.graph_sync_repository import GraphSyncRepository
from testmind.models.graph_sync import GraphEvent

pytestmark = pytest.mark.integration


async def test_local_source_pages_and_membership_revocation_are_independent():
    from testmind.infrastructure.graph_sync.demo_source import DemoGraphSource
    from testmind.infrastructure.graph_sync.polling import GraphPoller

    if not os.getenv("GRAPH_TEST_DSN"):
        pytest.skip("Dedicated PG required")
    project = f"p4-{uuid4()}"
    async with AsyncConnectionPool(os.environ["GRAPH_TEST_DSN"], open=False, kwargs={"row_factory":dict_row,"autocommit":True}) as pool:
        repo = GraphSyncRepository(pool)
        source = DemoGraphSource(pool, page_size=1)
        for revision, operation in ((1,"upsert"),(2,"delete")):
            await source.append(GraphEvent(event_id=f"{project}:{revision}",source_instance="demo-zentao-graph",project_id=project,entity_type="member",source_id="u",revision=revision,operation=operation,properties={"role":"employee","can_maintain":False}))
        poller = GraphPoller(repo,source,source_instance="demo-zentao-graph")
        assert await poller.run(project,"member") == 2
        assert await repo.permission(project,"u") is None
        assert await poller.run(project,"member") == 0
        await source.append(GraphEvent(event_id=f"{project}:old",source_instance="demo-zentao-graph",project_id=project,entity_type="member",source_id="u",revision=1,operation="upsert",properties={"role":"manager","can_maintain":True}))
        assert await poller.run(project,"member") == 1
        assert await repo.permission(project,"u") is None
        assert await repo.watermark(source_instance="demo-zentao-graph",project_id=project,resource_type="requirement") is None
        async with pool.connection() as connection:
            cursor=await connection.execute("SELECT count(*) AS n FROM knowledge.change_receipts WHERE source=%s",(project,))
            assert (await cursor.fetchone())["n"] == 0


async def test_real_projection_query_roles_reconcile_and_rebuild():
    from neo4j import AsyncGraphDatabase

    from testmind.api.graph_query import GraphQuery
    from testmind.core.security import AuthContext
    from testmind.infrastructure.graph_sync.maintenance import GraphMaintenance
    from testmind.infrastructure.graph_sync.projection import Neo4jProjection
    from testmind.infrastructure.graph_sync.query import GraphQueryService, Neo4jGraphReader

    if not os.getenv("GRAPH_TEST_DSN") or not os.getenv("GRAPH_TEST_NEO4J_URI"):
        pytest.skip("Dedicated PG/Neo4j required")
    project = f"p4-{uuid4()}"
    async with AsyncConnectionPool(os.environ["GRAPH_TEST_DSN"], open=False, kwargs={"row_factory":dict_row,"autocommit":True}) as pool, AsyncGraphDatabase.driver(os.environ["GRAPH_TEST_NEO4J_URI"],auth=None) as driver:
        repo=GraphSyncRepository(pool)
        events=[GraphEvent(event_id=f"{project}:{owner}",source_instance="testmind",project_id=project,entity_type="requirement",source_id=owner,owner_id=owner,revision=1,operation="upsert",properties={"title":owner}) for owner in ("u","v")]
        async with pool.connection() as connection:
            await connection.execute("INSERT INTO graph_sync.project_memberships(project_id,user_id,role,can_maintain) VALUES (%s,'u','employee',false),(%s,'manager','manager',true)",(project,project))
        for event in events:
            await repo.register_event(event,actor_id="test")
        maintenance=GraphMaintenance(repo,lambda generation:Neo4jProjection(driver,generation=generation))
        await maintenance.reconcile(project)
        service=GraphQueryService(repo,Neo4jGraphReader(driver))
        user=AuthContext("u",frozenset({project}),frozenset())
        manager=AuthContext("manager",frozenset({project}),frozenset())
        query=GraphQuery(project_id=project)
        assert len((await service.query(query,user))["items"]) == 1
        assert len((await service.query(query,manager))["items"]) == 2
        assert (await service.metrics(project,user))["requirements"] == 1
        import json
        json.dumps(await service.metrics(project,manager))
        async with driver.session() as session:
            await (await session.run("MATCH (n:GraphEntity {entity_key:$key}) SET n.properties_json=$p", key=events[0].entity_key,p='{"title":"CORRUPT"}')).consume()
            await (await session.run("MATCH (n:GraphEntity {entity_key:$a}),(t:GraphEntity {entity_key:$b}) MERGE (n)-[:SOURCE_RELATION {kind:'covers'}]->(t)", a=events[0].entity_key,b=events[1].entity_key)).consume()
        safe=(await service.query(query,user))["items"]
        assert json.loads(safe[0]["properties_json"])["title"] == "u"
        assert not (await service.query(GraphQuery(project_id=project,query_type="relationships"),manager))["items"]
        async with driver.session() as session:
            await (await session.run("MATCH (n:GraphEntity {entity_key:$key}) SET n.fingerprint='corrupt'",key=events[0].entity_key)).consume()
        assert not (await service.query(query,user))["items"]
        repaired=await maintenance.reconcile(project)
        assert repaired["generation"] != "initial"
        assert len((await service.query(query,user))["items"]) == 1
        async with driver.session() as session:
            await (await session.run("MATCH (n:GraphEntity {project_id:$p}) DETACH DELETE n",p=project)).consume()
        assert not (await service.query(query,user))["items"]
        await maintenance.reconcile(project)
        assert len((await service.query(query,user))["items"]) == 1
        result=await maintenance.rebuild(project)
        assert await repo.generation(project) == result["generation"] != "initial"
        assert len((await service.query(query,manager))["items"]) == 2
        snapshot=await repo.snapshot(project)
        async with pool.connection() as connection:
            await connection.execute("UPDATE graph_sync.project_memberships SET can_maintain=false,permission_version=permission_version+1 WHERE project_id=%s AND user_id='manager'",(project,))
        with pytest.raises(PermissionError):
            await repo.switch_generation(project,"must-not-activate",{event.entity_key:event.fingerprint for event in snapshot},authorized_user="manager")
        assert await repo.generation(project) == result["generation"]
        async with pool.connection() as connection:
            await connection.execute("UPDATE graph_sync.project_memberships SET revoked=true WHERE project_id=%s AND user_id='u'",(project,))
        with pytest.raises(PermissionError):
            await service.query(query,user)
        async with driver.session() as session:
            await (await session.run("MATCH (n:GraphEntity {project_id:$p}) DETACH DELETE n",p=project)).consume()
