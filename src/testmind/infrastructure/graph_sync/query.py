"""Read-only graph templates and authoritative post-filtering."""

from typing import Any
from uuid import uuid4

from testmind.core.security import AuthContext


class Neo4jGraphReader:
    def __init__(self, driver: Any, *, database: str = "neo4j") -> None:
        self.driver = driver
        self.database = database

    async def read(self, *, project_id: str, owner_id: str | None,
                   query_type: str, entity_type: str | None, limit: int,
                   generation: str = "initial", cursor: str | None = None) -> list[dict[str, Any]]:
        predicate = """n.project_id=$project_id AND n.generation=$generation
            AND n.live=true AND n.tombstoned=false
            AND ($owner_id IS NULL OR n.owner_id=$owner_id)
            AND ($entity_type IS NULL OR n.entity_type=$entity_type)"""
        if query_type == "entities":
            query = "MATCH (n:GraphEntity) WHERE " + predicate + " AND n.entity_key>$cursor RETURN n ORDER BY n.entity_key LIMIT $limit"
        elif query_type == "relationships":
            query = "MATCH (n:GraphEntity)-[r:SOURCE_RELATION]->(t:GraphEntity) WHERE " + predicate + """
                AND t.project_id=$project_id AND t.generation=n.generation
                AND t.live=true AND t.tombstoned=false
                AND ($owner_id IS NULL OR t.owner_id=$owner_id)
                AND n.entity_key+':'+t.entity_key+':'+r.kind>$cursor
                RETURN n,t,r.kind AS kind ORDER BY n.entity_key,t.entity_key,r.kind LIMIT $limit"""
        else:
            raise ValueError("Unsupported query template")
        async with self.driver.session(database=self.database, default_access_mode="READ") as session:
            result = await session.run(query, project_id=project_id, owner_id=owner_id,
                                       entity_type=entity_type, limit=limit, generation=generation, cursor=cursor or "")
            rows = []
            async for record in result:
                row = dict(record["n"])
                if query_type == "relationships":
                    row["target_key"] = record["t"]["entity_key"]
                    row["target_revision"] = record["t"]["revision"]
                    row["kind"] = record["kind"]
                rows.append(row)
            return rows


class GraphQueryService:
    def __init__(self, repository: Any, reader: Any) -> None:
        self.repository = repository
        self.reader = reader

    async def query(self, query: Any, context: AuthContext) -> dict[str, Any]:
        if query.project_id not in context.authorized_project_ids:
            raise PermissionError("Project not authorized")
        permission = await self.repository.permission(query.project_id, context.user_id)
        if permission is None or permission["role"] not in {"employee", "manager", "tse"}:
            raise PermissionError("Project membership is not authorized")
        owner_id = context.user_id if permission["role"] == "employee" else None
        generation = await self.repository.generation(query.project_id)
        rows = await self.reader.read(project_id=query.project_id, owner_id=owner_id,
                                     query_type=query.query_type, entity_type=query.entity_type,
                                     limit=query.limit, generation=generation, cursor=getattr(query, "cursor", None))
        allowed = await self.repository.authorized_entities(query.project_id, owner_id)
        items = []
        for row in rows:
            authority = allowed.get(row["entity_key"])
            if authority is None or authority["revision"] != row["revision"]:
                continue
            if "target_key" in row:
                target = allowed.get(row["target_key"])
                if target is None or target["revision"] != row["target_revision"]:
                    continue
            items.append(row)
        query_id = str(uuid4())
        await self.repository.audit_query(project_id=query.project_id, actor_id=context.user_id,
                                         query_id=query_id, query_type=query.query_type)
        if permission != await self.repository.permission(query.project_id, context.user_id):
            raise PermissionError("Project permission changed during query")
        next_cursor = None
        if rows and len(rows) == query.limit:
            last = rows[-1]
            next_cursor = last["entity_key"]
            if query.query_type == "relationships":
                next_cursor += f":{last['target_key']}:{last['kind']}"
        return {"query_id": query_id, "items": items, "stale": len(items) != len(rows),
                "permission_version": permission["permission_version"], "next_cursor": next_cursor}
