"""Parameterized Neo4j projection with a database-side revision fence."""

from __future__ import annotations

import json
from typing import Any

from testmind.models.graph_sync import EntityRef, GraphEvent

_APPLY_ENTITY = """
MERGE (n:GraphEntity {entity_key: $entity_key, generation: $generation})
ON CREATE SET n.revision = -1, n.live = false, n.lock_seq = 0
SET n.lock_seq = coalesce(n.lock_seq, 0) + 1
WITH n
WHERE n.revision < $revision OR
      (n.revision = $revision AND n.fingerprint = $fingerprint)
SET n.project_id = $project_id, n.source_instance = $source_instance,
    n.entity_type = $entity_type, n.source_id = $source_id,
    n.revision = $revision, n.fingerprint = $fingerprint,
    n.owner_id = $owner_id, n.tombstoned = $tombstoned,
    n.live = true, n.properties_json = $properties_json
RETURN n.revision AS revision
"""


class Neo4jProjection:
    def __init__(self, driver: Any, *, database: str = "neo4j", generation: str = "initial") -> None:
        self.driver = driver
        self.database = database
        self.generation = generation

    async def ensure_constraints(self) -> None:
        async with self.driver.session(database=self.database) as session:
            result = await session.run(
                "CREATE CONSTRAINT graph_entity_identity IF NOT EXISTS "
                "FOR (n:GraphEntity) REQUIRE (n.entity_key, n.generation) IS UNIQUE"
            )
            await result.consume()

    async def apply(self, event: GraphEvent) -> bool:
        async with self.driver.session(database=self.database) as session:
            return bool(await session.execute_write(self._apply, event))

    async def current(self, event: GraphEvent) -> dict[str, Any] | None:
        async with self.driver.session(database=self.database, default_access_mode="READ") as session:
            result = await session.run(
                "MATCH (n:GraphEntity {entity_key:$entity_key,generation:$generation}) "
                "RETURN n.revision AS revision,n.fingerprint AS fingerprint",
                entity_key=event.entity_key, generation=self.generation,
            )
            record = await result.single()
            return dict(record) if record else None

    async def _apply(self, tx: Any, event: GraphEvent) -> bool:
        params = {
            "entity_key": event.entity_key, "generation": self.generation,
            "project_id": event.project_id, "source_instance": event.source_instance,
            "entity_type": event.entity_type, "source_id": event.source_id,
            "revision": event.revision, "fingerprint": event.fingerprint,
            "owner_id": event.owner_id, "tombstoned": event.operation == "delete",
            "properties_json": json.dumps(event.properties, sort_keys=True, ensure_ascii=False),
        }
        result = await tx.run(_APPLY_ENTITY, **params)
        if await result.single() is None:
            return False
        # Outgoing relationships belong to this source snapshot; never delete incoming edges.
        await tx.run(
            "MATCH (n:GraphEntity {entity_key:$entity_key, generation:$generation}) "
            "OPTIONAL MATCH (n)-[r:SOURCE_RELATION]->() DELETE r",
            entity_key=event.entity_key, generation=self.generation,
        )
        if event.operation == "delete":
            return True
        for relation in event.relations:
            target_key = EntityRef(
                relation.target_source_instance, event.project_id,
                relation.target_type, relation.target_source_id,
            ).key
            # Reference-only nodes are excluded from reads until their own snapshot arrives.
            await tx.run(
                "MATCH (n:GraphEntity {entity_key:$entity_key, generation:$generation}) "
                "MERGE (target:GraphEntity {entity_key:$target_key, generation:$generation}) "
                "ON CREATE SET target.live=false, target.revision=-1, "
                "target.project_id=$project_id, target.tombstoned=false "
                "MERGE (n)-[r:SOURCE_RELATION {kind:$kind}]->(target) "
                "SET r.project_id=$project_id, r.source_revision=$revision",
                entity_key=event.entity_key, target_key=target_key,
                generation=self.generation, project_id=event.project_id,
                kind=relation.kind, revision=event.revision,
            )
        return True
