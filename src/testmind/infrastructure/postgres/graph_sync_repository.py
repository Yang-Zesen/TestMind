"""PostgreSQL authority and transactional projection outbox."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from testmind.models.graph_sync import GraphEvent, decide_revision


class GraphSyncRepository:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @staticmethod
    async def _lock_maintenance_grant(connection: Any, project_id: str, user_id: str) -> None:
        cursor = await connection.execute(
            """SELECT user_id FROM graph_sync.project_memberships
               WHERE project_id=%s AND user_id=%s AND revoked=false AND can_maintain=true
               FOR UPDATE""", (project_id, user_id)
        )
        if await cursor.fetchone() is None:
            raise PermissionError("Maintenance permission was revoked")

    async def permission(self, project_id: str, user_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT role,can_maintain,permission_version FROM graph_sync.project_memberships
                   WHERE project_id=%s AND user_id=%s AND revoked=false""", (project_id, user_id)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def authorized_snapshot(self, project_id: str, owner_id: str | None) -> list[GraphEvent]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT payload FROM graph_sync.entities WHERE project_id=%s
                   AND tombstoned=false AND (%s::text IS NULL OR owner_id=%s)
                   ORDER BY entity_key LIMIT 10001""", (project_id, owner_id, owner_id)
            )
            rows = await cursor.fetchall()
            if len(rows) > 10000:
                raise ValueError("Metrics snapshot exceeds 10000 entities; use aggregation service")
            return [GraphEvent.model_validate(row["payload"]) for row in rows]

    async def project_health(self, project_id: str) -> dict[str, Any]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT count(*) FILTER (WHERE status<>'succeeded') AS pending,
                   max(updated_at) FILTER (WHERE status='succeeded') AS last_projected_at
                   FROM graph_sync.projection_outbox WHERE project_id=%s""", (project_id,)
            )
            return dict(await cursor.fetchone())

    async def watermark(self, *, source_instance: str, project_id: str, resource_type: str) -> Any:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT cursor FROM graph_sync.sync_watermarks
                   WHERE source_instance=%s AND project_id=%s AND resource_type=%s""",
                (source_instance, project_id, resource_type),
            )
            row = await cursor.fetchone()
            return row["cursor"] if row else None

    async def register_page(
        self, *, source_instance: str, project_id: str, resource_type: str,
        events: tuple[GraphEvent, ...], expected_cursor: Any, next_cursor: Any,
    ) -> None:
        if any(event.source_instance != source_instance or event.project_id != project_id
               or event.entity_type != resource_type for event in events):
            raise PermissionError("Page scope mismatch")
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"stream:{source_instance}:{project_id}:{resource_type}",),
            )
            bound = GraphSyncRepository(_ConnectionPool(connection))
            current = await bound.watermark(source_instance=source_instance, project_id=project_id,
                                            resource_type=resource_type)
            if current != expected_cursor:
                raise ValueError("Pull cursor changed concurrently; resume from saved watermark")
            for event in events:
                result = await bound.register_event(event, actor_id=f"source:{source_instance}")
                if result["status"] == "conflict":
                    raise ValueError("Conflicting source revision; watermark must not advance")
            await connection.execute(
                """INSERT INTO graph_sync.sync_watermarks
                   (source_instance,project_id,resource_type,cursor) VALUES (%s,%s,%s,%s)
                   ON CONFLICT(source_instance,project_id,resource_type)
                   DO UPDATE SET cursor=EXCLUDED.cursor,updated_at=now()""",
                (source_instance, project_id, resource_type, Jsonb(next_cursor)),
            )

    async def enqueue_maintenance(self, *, project_id: str, operation: str, actor_id: str,
                                  resource_type: str | None = None, authorized_user: str | None = None) -> str:
        if operation not in {"rebuild", "reconcile", "pull"}:
            raise ValueError("Unsupported maintenance operation")
        job_id = str(uuid4())
        async with self.pool.connection() as connection, connection.transaction():
            if authorized_user is not None:
                await self._lock_maintenance_grant(connection, project_id, authorized_user)
            await connection.execute(
                """INSERT INTO graph_sync.projection_outbox
                   (id,project_id,revision,payload,operation)
                   VALUES (%s,%s,0,%s,%s)""",
                (job_id, project_id, Jsonb({"generation": str(uuid4()), "resource_type": resource_type}), operation),
            )
            await connection.execute(
                """INSERT INTO graph_sync.audit_events
                   (id,actor_id,project_id,action,resource_id,details)
                   VALUES (%s,%s,%s,%s,%s,'{}'::jsonb)""",
                (str(uuid4()), actor_id, project_id, f"graph.{operation}", job_id),
            )
        return job_id

    async def generation(self, project_id: str) -> str:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT generation FROM graph_sync.project_generations WHERE project_id=%s",
                (project_id,),
            )
            row = await cursor.fetchone()
            return str(row["generation"]) if row else "initial"

    async def snapshot(self, project_id: str) -> list[GraphEvent]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT payload FROM graph_sync.entities WHERE project_id=%s ORDER BY entity_key",
                (project_id,),
            )
            return [GraphEvent.model_validate(row["payload"]) for row in await cursor.fetchall()]

    async def switch_generation(
        self, project_id: str, generation: str, fingerprints: dict[str, str],
        *, job_id: str | None = None, claim_token: str | None = None,
    ) -> bool:
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"project:{project_id}",)
            )
            if job_id is not None:
                lease = await connection.execute(
                    """SELECT id FROM graph_sync.projection_outbox
                       WHERE id=%s AND claim_token=%s AND status='processing'
                       AND lease_expires_at>clock_timestamp() FOR UPDATE""", (job_id, claim_token)
                )
                if await lease.fetchone() is None:
                    raise PermissionError("Rebuild lease is no longer owned")
            cursor = await connection.execute(
                "SELECT payload FROM graph_sync.entities WHERE project_id=%s", (project_id,)
            )
            events = [GraphEvent.model_validate(row["payload"]) for row in await cursor.fetchall()]
            if {event.entity_key: event.fingerprint for event in events} != fingerprints:
                return False
            switched = await connection.execute(
                """INSERT INTO graph_sync.project_generations (project_id,generation)
                   SELECT %s,%s WHERE %s::text IS NULL OR EXISTS (
                     SELECT 1 FROM graph_sync.projection_outbox
                     WHERE id=%s AND claim_token=%s AND status='processing'
                       AND lease_expires_at>clock_timestamp())
                   ON CONFLICT(project_id) DO UPDATE SET
                   generation=EXCLUDED.generation,updated_at=now() RETURNING project_id""",
                (project_id, generation, job_id, job_id, claim_token),
            )
            if await switched.fetchone() is None:
                raise PermissionError("Rebuild lease expired before activation")
            return True

    async def authorized_entities(self, project_id: str, owner_id: str | None) -> dict[str, Any]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT entity_key,revision FROM graph_sync.entities
                   WHERE project_id=%s AND tombstoned=false AND (%s::text IS NULL OR owner_id=%s)""",
                (project_id, owner_id, owner_id),
            )
            return {row["entity_key"]: dict(row) for row in await cursor.fetchall()}

    async def audit_query(self, *, project_id: str, actor_id: str, query_id: str, query_type: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """INSERT INTO graph_sync.audit_events
                   (id,actor_id,project_id,action,resource_id,details)
                   VALUES (%s,%s,%s,'graph.query',%s,%s)""",
                (str(uuid4()), actor_id, project_id, query_id, Jsonb({"query_type": query_type})),
            )

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM graph_sync.projection_outbox WHERE id=%s", (job_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def claim(self, *, lease_seconds: int = 60, project_id: str | None = None) -> dict[str, Any] | None:
        token = str(uuid4())
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute(
                """UPDATE graph_sync.projection_outbox SET status='failed_permanent',
                   error_code='attempts_exhausted', claim_token=NULL, lease_expires_at=NULL
                   WHERE status='processing' AND lease_expires_at<=clock_timestamp()
                   AND attempt_count>=max_attempts"""
            )
            cursor = await connection.execute(
                """WITH candidate AS (
                     SELECT id FROM graph_sync.projection_outbox
                     WHERE (%s::text IS NULL OR project_id=%s) AND attempt_count<max_attempts AND
                       ((status IN ('queued','failed_retryable') AND next_attempt_at<=now())
                         OR (status='processing' AND lease_expires_at<=clock_timestamp()))
                     ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
                   UPDATE graph_sync.projection_outbox j SET status='processing',
                     attempt_count=attempt_count+1, claim_token=%s,
                     lease_expires_at=clock_timestamp()+make_interval(secs=>%s), updated_at=now()
                   FROM candidate WHERE j.id=candidate.id RETURNING j.*""",
                (project_id, project_id, token, lease_seconds),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def renew(self, *, job_id: str, claim_token: str, lease_seconds: int) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """UPDATE graph_sync.projection_outbox
                   SET lease_expires_at=clock_timestamp()+make_interval(secs=>%s),updated_at=now()
                   WHERE id=%s AND claim_token=%s AND status='processing'
                     AND lease_expires_at>clock_timestamp() RETURNING id""",
                (lease_seconds, job_id, claim_token),
            )
            return await cursor.fetchone() is not None

    async def finish(self, *, job_id: str, claim_token: str) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """UPDATE graph_sync.projection_outbox SET status='succeeded',
                   claim_token=NULL,lease_expires_at=NULL,updated_at=now()
                   WHERE id=%s AND claim_token=%s AND status='processing'
                     AND lease_expires_at>clock_timestamp() RETURNING id""",
                (job_id, claim_token),
            )
            return await cursor.fetchone() is not None

    async def fail(
        self, *, job_id: str, claim_token: str, permanent: bool, error_code: str
    ) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """UPDATE graph_sync.projection_outbox SET
                   status=CASE WHEN %s OR attempt_count>=max_attempts
                     THEN 'failed_permanent' ELSE 'failed_retryable' END,
                   next_attempt_at=now()+make_interval(secs=>
                     LEAST(3600, power(2,attempt_count)::int)*(0.5+random())),
                   error_code=%s,claim_token=NULL,lease_expires_at=NULL,updated_at=now()
                   WHERE id=%s AND claim_token=%s AND status='processing'
                     AND lease_expires_at>clock_timestamp()""",
                (permanent, error_code, job_id, claim_token),
            )

    async def register_event(self, event: GraphEvent, *, actor_id: str,
                             authorized_user: str | None = None) -> dict[str, Any]:
        async with self.pool.connection() as connection, connection.transaction():
            if authorized_user is not None:
                await self._lock_maintenance_grant(connection, event.project_id, authorized_user)
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"project:{event.project_id}",),
            )
            # Serialize receipts as well as resource revisions, including first insertion.
            for key in sorted((f"receipt:{event.source_instance}:{event.event_id}", event.entity_key)):
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,)
                )
            cursor = await connection.execute(
                "SELECT * FROM graph_sync.event_receipts WHERE source_instance=%s AND event_id=%s",
                (event.source_instance, event.event_id),
            )
            existing = await cursor.fetchone()
            if existing:
                if existing["fingerprint"] != event.fingerprint:
                    await connection.execute(
                        """INSERT INTO graph_sync.audit_events
                           (id,actor_id,project_id,action,resource_id,details)
                           VALUES (%s,%s,%s,'event.conflict',%s,%s)""",
                        (str(uuid4()), actor_id, event.project_id, event.entity_key,
                         Jsonb({"event_id": event.event_id, "receipt_id": existing["id"]})),
                    )
                    return {"receipt_id": existing["id"], "job_id": None,
                            "status": "conflict", "replayed": False}
                return {"receipt_id": existing["id"], "job_id": existing["job_id"],
                        "status": existing["status"], "replayed": True}
            cursor = await connection.execute(
                "SELECT payload FROM graph_sync.entities WHERE entity_key=%s FOR UPDATE",
                (event.entity_key,),
            )
            current = await cursor.fetchone()
            decision = decide_revision(
                GraphEvent.model_validate(current["payload"]) if current else None, event
            )
            receipt_id = str(uuid4())
            job_id = str(uuid4()) if decision == "accepted" else None
            if decision == "accepted":
                await connection.execute(
                    """INSERT INTO graph_sync.entities
                       (entity_key, project_id, source_instance, entity_type, source_id,
                        revision, owner_id, tombstoned, payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (entity_key) DO UPDATE SET
                         revision=EXCLUDED.revision, owner_id=EXCLUDED.owner_id,
                         tombstoned=EXCLUDED.tombstoned, payload=EXCLUDED.payload,
                         updated_at=now()""",
                    (event.entity_key, event.project_id, event.source_instance, event.entity_type,
                     event.source_id, event.revision, event.owner_id, event.operation == "delete",
                     Jsonb(event.model_dump(mode="json"))),
                )
                await connection.execute(
                    """INSERT INTO graph_sync.projection_outbox
                       (id, entity_key, project_id, revision, payload)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (job_id, event.entity_key, event.project_id, event.revision,
                     Jsonb(event.model_dump(mode="json"))),
                )
            await connection.execute(
                """INSERT INTO graph_sync.event_receipts
                   (id, source_instance, event_id, project_id, fingerprint, status, job_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (receipt_id, event.source_instance, event.event_id, event.project_id,
                 event.fingerprint, decision, job_id),
            )
            await connection.execute(
                """INSERT INTO graph_sync.audit_events
                   (id, actor_id, project_id, action, resource_id, details)
                   VALUES (%s,%s,%s,'event.registered',%s,%s)""",
                (str(uuid4()), actor_id, event.project_id, event.entity_key,
                 Jsonb({"receipt_id": receipt_id, "decision": decision})),
            )
            return {"receipt_id": receipt_id, "job_id": job_id,
                    "status": decision, "replayed": False}


class _ConnectionPool:
    """Bind nested registration savepoints to the page transaction's connection."""

    def __init__(self, connection: Any) -> None:
        self.value = connection

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Any]:
        yield self.value
