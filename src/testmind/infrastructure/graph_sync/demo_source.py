"""Persisted simulated external events; graph cursors never use knowledge state."""

from typing import Any

from psycopg.types.json import Jsonb

from testmind.infrastructure.graph_sync.polling import PollPage
from testmind.models.graph_sync import EntityType, GraphEvent, RelationSpec

SOURCE = "demo-zentao-graph"


class DemoGraphSource:
    def __init__(self, pool: Any, *, page_size: int = 50):
        if not 1 <= page_size <= 200:
            raise ValueError("invalid_page_size")
        self.pool, self.page_size = pool, page_size

    async def append(self, event: GraphEvent) -> None:
        if event.source_instance != SOURCE:
            raise ValueError("invalid_demo_source")
        async with self.pool.connection() as connection, connection.transaction():
            # Commit serialization prevents skipping an uncommitted smaller sequence.
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('demo-graph-journal',0))")
            cursor = await connection.execute("SELECT payload FROM graph_sync.demo_source_events WHERE event_id=%s", (event.event_id,))
            existing = await cursor.fetchone()
            if existing:
                if GraphEvent.model_validate(existing["payload"]).fingerprint != event.fingerprint:
                    raise ValueError("demo_event_conflict")
                return
            await connection.execute("INSERT INTO graph_sync.demo_source_events(event_id,project_id,resource_type,payload) VALUES (%s,%s,%s,%s)", (event.event_id,event.project_id,event.entity_type,Jsonb(event.model_dump(mode="json"))))

    async def fetch_page(self, project_id: str, resource_type: str, cursor: Any) -> PollPage:
        if cursor is not None and (not isinstance(cursor, int) or cursor < 0):
            raise ValueError("invalid_demo_cursor")
        async with self.pool.connection() as connection:
            rows = await connection.execute("SELECT sequence,payload FROM graph_sync.demo_source_events WHERE project_id=%s AND resource_type=%s AND sequence>%s ORDER BY sequence LIMIT %s", (project_id,resource_type,cursor or 0,self.page_size+1))
            data = await rows.fetchall()
        selected = data[:self.page_size]
        return PollPage(tuple(GraphEvent.model_validate(row["payload"]) for row in selected), int(selected[-1]["sequence"]) if selected else cursor, len(data)>self.page_size)


async def seed_demo_graph(pool: Any) -> None:
    source = DemoGraphSource(pool)
    for project, owner in (("demo-project","demo-user"),("other-project","demo-other")):
        specs: list[tuple[EntityType, str, dict[str, Any], tuple[RelationSpec, ...]]] = [
            ("project",project,{"title":project},()),
            ("member",owner,{"title":owner,"role":"manager" if owner=="demo-user" else "employee","can_maintain":True},()),
            ("requirement","login",{"title":"登录失败锁定规则","status":"active"},()),
            ("test_case","login-case",{"title":"登录锁定边界用例","status":"approved"},(RelationSpec(kind="covers",target_source_instance=SOURCE,target_type="requirement",target_source_id="login"),)),
            ("bug","login-bug",{"title":"锁定时间校验缺陷","status":"open"},(RelationSpec(kind="reports",target_source_instance=SOURCE,target_type="test_case",target_source_id="login-case"),)),
        ]
        for kind, identifier, properties, relations in specs:
            await source.append(GraphEvent(event_id=f"seed:{project}:{kind}:{identifier}:1",source_instance=SOURCE,project_id=project,entity_type=kind,source_id=identifier,revision=1,operation="upsert",owner_id=owner,properties=properties,relations=relations))
