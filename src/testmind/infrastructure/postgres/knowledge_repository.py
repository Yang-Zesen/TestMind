"""Durable metadata, idempotency, queue leases, activation, and tombstones."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from testmind.infrastructure.knowledge.source_events import NormalizedSourceEvent
from testmind.models.knowledge import retry_delay_seconds
from testmind.models.retrieval import ActiveVersionScope, RetrievalScope


class IdempotencyConflict(Exception):
    """The same project-scoped key was reused for a different request."""


class InvalidVersionTransition(Exception):
    """A version or job cannot enter the requested state."""


class KnowledgeRepository:
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def build_retrieval_scope(
        self, *, user_id: str, project_id: str, knowledge_base_ids: frozenset[str]
    ) -> RetrievalScope:
        if not user_id.strip() or not project_id.strip() or not knowledge_base_ids:
            return RetrievalScope(
                user_id=user_id,
                project_id=project_id,
                knowledge_base_ids=knowledge_base_ids,
            )
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT a.kb_id, a.document_id, a.document_version, a.index_version
                   FROM knowledge.project_active_versions a
                   JOIN knowledge.documents d ON d.id = a.document_id
                   WHERE a.project_id = %s AND a.kb_id = ANY(%s)
                     AND d.tombstoned_at IS NULL""",
                (project_id, sorted(knowledge_base_ids)),
            )
            rows = await cursor.fetchall()
        active_versions = {
            row["document_id"]: ActiveVersionScope(
                document_version=row["document_version"], index_version=row["index_version"]
            )
            for row in rows
        }
        return RetrievalScope(
            user_id=user_id,
            project_id=project_id,
            knowledge_base_ids=knowledge_base_ids,
            active_versions=active_versions,
        )

    async def list_knowledge_bases(self, knowledge_base_ids: frozenset[str]) -> list[dict[str, Any]]:
        if not knowledge_base_ids:
            return []
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT id, project_id, name, metadata, created_at
                   FROM knowledge.knowledge_bases WHERE id = ANY(%s)
                   ORDER BY name, id""",
                (list(knowledge_base_ids),),
            )
            return list(await cursor.fetchall())

    async def register_source_event(
        self,
        event: NormalizedSourceEvent,
        *,
        actor_id: str,
        correlation_id: str,
    ) -> dict[str, str | bool | None]:
        """Persist a source receipt before dispatching a durable sync job."""
        async with self._pool.connection() as connection, connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"source:{event.source}:{event.source_event_id}",))
            existing = await self._fetchone(
                connection,
                """SELECT id, job_id FROM knowledge.change_receipts
                   WHERE source = %s AND source_event_id = %s FOR UPDATE""",
                (event.source, event.source_event_id),
            )
            if existing:
                return {
                    "receipt_id": existing["id"],
                    "job_id": existing["job_id"],
                    "replayed": True,
                }

            document_id = str(uuid5(NAMESPACE_URL, f"testmind:{event.source}:{event.project_ref}:{event.kb_ref}:{event.resource_type}:{event.resource_id}"))
            version_id, job_id, receipt_id = (str(uuid4()) for _ in range(3))
            version = f"source-{event.source_version}"
            index_version = f"index-{uuid4().hex[:12]}"
            title = str(event.snapshot.get("title") or f"{event.resource_type}:{event.resource_id}")
            storage_key = f"external/{event.source}/{event.resource_id}/{event.source_version}"
            await connection.execute(
                """INSERT INTO knowledge.documents
                   (id, project_id, kb_id, title, metadata, source_type)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title,
                     metadata = EXCLUDED.metadata, updated_at = now()""",
                (document_id, event.project_ref, event.kb_ref, title,
                 Jsonb(dict(event.snapshot)), f"{event.source}:{event.resource_type}"),
            )
            await connection.execute(
                """INSERT INTO knowledge.document_versions
                   (id, document_id, version_number, document_version, index_version,
                    status, storage_key, request_fingerprint)
                   VALUES (%s, %s, COALESCE((SELECT MAX(version_number) + 1
                     FROM knowledge.document_versions WHERE document_id = %s), 1),
                     %s, %s, 'processing', %s, %s)""",
                (version_id, document_id, document_id, version, index_version, storage_key,
                 f"{event.source}:{event.source_event_id}:{event.content_hash or ''}"),
            )
            await connection.execute(
                """INSERT INTO knowledge.jobs
                   (id, project_id, kb_id, document_id, version_id, operation, status,
                    request_fingerprint, correlation_id)
                   VALUES (%s, %s, %s, %s, %s, 'source_sync', 'queued', %s, %s)""",
                (job_id, event.project_ref, event.kb_ref, document_id, version_id,
                 f"{event.source}:{event.source_event_id}", correlation_id),
            )
            await connection.execute(
                """INSERT INTO knowledge.change_receipts
                   (id, source, source_event_id, project_id, kb_id, resource_type,
                    resource_id, operation, source_version, payload, job_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (receipt_id, event.source, event.source_event_id, event.project_ref,
                 event.kb_ref, event.resource_type, event.resource_id, event.operation,
                 event.source_version, Jsonb(dict(event.snapshot)), job_id),
            )
            await connection.execute(
                """INSERT INTO knowledge.audit_events
                   (id, actor_id, action, project_id, kb_id, resource_id, correlation_id)
                   VALUES (%s, %s, 'source_event.accepted', %s, %s, %s, %s)""",
                (str(uuid4()), actor_id, event.project_ref, event.kb_ref,
                 event.resource_id, correlation_id),
            )
            return {"receipt_id": receipt_id, "job_id": job_id, "replayed": False}

    async def get_knowledge_base(self, kb_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as connection:
            return await self._fetchone(
                connection,
                "SELECT id, project_id, name, metadata, created_at FROM knowledge.knowledge_bases WHERE id = %s",
                (kb_id,),
            )

    async def get_document_scope(self, document_id: str) -> dict[str, str] | None:
        async with self._pool.connection() as connection:
            return await self._fetchone(
                connection,
                """SELECT id AS document_id, project_id, kb_id FROM knowledge.documents
                   WHERE id = %s AND tombstoned_at IS NULL""",
                (document_id,),
            )

    async def update_document_metadata(
        self,
        *,
        project_id: str,
        kb_id: str,
        document_id: str,
        title: str | None,
        metadata: dict[str, Any] | None,
        actor_id: str,
        correlation_id: str,
    ) -> bool:
        async with self._pool.connection() as connection, connection.transaction():
            row = await self._fetchone(
                connection,
                """UPDATE knowledge.documents
                   SET title = COALESCE(%s, title), metadata = COALESCE(%s, metadata), updated_at = now()
                   WHERE id = %s AND project_id = %s AND kb_id = %s AND tombstoned_at IS NULL
                   RETURNING id""",
                (
                    title,
                    Jsonb(metadata) if metadata is not None else None,
                    document_id,
                    project_id,
                    kb_id,
                ),
            )
            if not row:
                return False
            await connection.execute(
                """INSERT INTO knowledge.audit_events
                   (id, actor_id, action, project_id, kb_id, resource_id, correlation_id)
                   VALUES (%s, %s, 'document.metadata_update', %s, %s, %s, %s)""",
                (str(uuid4()), actor_id, project_id, kb_id, document_id, correlation_id),
            )
            return True

    async def create_replacement(
        self,
        *,
        project_id: str,
        kb_id: str,
        document_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        storage_key: str,
        actor_id: str,
        correlation_id: str,
    ) -> dict[str, str | bool]:
        async with self._pool.connection() as connection, connection.transaction():
            existing = await self._fetchone(
                connection,
                """SELECT id, document_id, version_id, request_fingerprint FROM knowledge.jobs
                   WHERE project_id = %s AND idempotency_key = %s FOR UPDATE""",
                (project_id, idempotency_key),
            )
            if existing:
                if existing["request_fingerprint"] != request_fingerprint:
                    raise IdempotencyConflict("Idempotency key conflicts with another request")
                if existing["document_id"] != document_id:
                    raise IdempotencyConflict("Idempotency key belongs to another document")
                return {
                    "job_id": existing["id"],
                    "document_id": existing["document_id"],
                    "version_id": existing["version_id"],
                    "replayed": True,
                }
            document = await self._fetchone(
                connection,
                """SELECT id FROM knowledge.documents WHERE id = %s AND project_id = %s
                   AND kb_id = %s AND tombstoned_at IS NULL FOR UPDATE""",
                (document_id, project_id, kb_id),
            )
            if not document:
                raise LookupError("Document not found")
            last = await self._fetchone(
                connection,
                "SELECT COALESCE(MAX(version_number), 0) AS number FROM knowledge.document_versions WHERE document_id = %s",
                (document_id,),
            )
            version_id, job_id = str(uuid4()), str(uuid4())
            await connection.execute(
                """INSERT INTO knowledge.document_versions
                   (id, document_id, version_number, document_version, index_version, status,
                    storage_key, request_fingerprint)
                   VALUES (%s, %s, %s, %s, %s, 'processing', %s, %s)""",
                (
                    version_id,
                    document_id,
                    (last["number"] if last else 0) + 1,
                    version_id,
                    f"index-{uuid4().hex[:12]}",
                    storage_key,
                    request_fingerprint,
                ),
            )
            await connection.execute(
                """INSERT INTO knowledge.jobs
                   (id, project_id, kb_id, document_id, version_id, operation, status,
                    idempotency_key, request_fingerprint, correlation_id)
                   VALUES (%s, %s, %s, %s, %s, 'ingest', 'queued', %s, %s, %s)""",
                (
                    job_id,
                    project_id,
                    kb_id,
                    document_id,
                    version_id,
                    idempotency_key,
                    request_fingerprint,
                    correlation_id,
                ),
            )
            await connection.execute(
                """INSERT INTO knowledge.audit_events
                   (id, actor_id, action, project_id, kb_id, resource_id, correlation_id)
                   VALUES (%s, %s, 'document.replace', %s, %s, %s, %s)""",
                (str(uuid4()), actor_id, project_id, kb_id, document_id, correlation_id),
            )
            return {
                "job_id": job_id,
                "document_id": document_id,
                "version_id": version_id,
                "replayed": False,
            }

    async def get_version_scope(self, version_id: str) -> dict[str, str] | None:
        async with self._pool.connection() as connection:
            return await self._fetchone(
                connection,
                """SELECT v.id AS version_id, v.document_id, d.project_id, d.kb_id
                   FROM knowledge.document_versions v
                   JOIN knowledge.documents d ON d.id = v.document_id
                   WHERE v.id = %s AND d.tombstoned_at IS NULL""",
                (version_id,),
            )

    async def list_documents(self, *, kb_id: str, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT d.id, d.title, d.metadata, d.source_type, d.created_at, d.updated_at,
                          a.document_version AS active_version, a.index_version AS active_index_version
                   FROM knowledge.documents d LEFT JOIN knowledge.project_active_versions a
                     ON a.project_id = d.project_id AND a.kb_id = d.kb_id AND a.document_id = d.id
                   WHERE d.kb_id = %s AND d.tombstoned_at IS NULL
                   ORDER BY d.created_at DESC, d.id DESC LIMIT %s""",
                (kb_id, min(max(limit, 1), 100)),
            )
            return list(await cursor.fetchall())

    async def list_versions(self, *, document_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT id, version_number, document_version, index_version, status, created_at
                   FROM knowledge.document_versions WHERE document_id = %s
                   ORDER BY version_number DESC, id DESC""",
                (document_id,),
            )
            return list(await cursor.fetchall())

    async def get_quality_report(self, version_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as connection:
            row = await self._fetchone(
                connection,
                "SELECT quality_report FROM knowledge.document_versions WHERE id = %s",
                (version_id,),
            )
            return row["quality_report"] if row else None

    async def get_version_processing_inputs(self, version_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as connection:
            return await self._fetchone(
                connection,
                """SELECT v.id AS version_id, v.document_version, v.index_version, v.storage_key,v.status,
                          d.id AS document_id, d.project_id, d.kb_id, d.title, d.metadata
                   FROM knowledge.document_versions v
                   JOIN knowledge.documents d ON d.id = v.document_id
                   WHERE v.id = %s AND d.tombstoned_at IS NULL""",
                (version_id,),
            )

    async def save_quality_report(
        self, *, version_id: str, report: dict[str, Any], status: str
    ) -> None:
        if status not in {"quality_passed", "rejected"}:
            raise ValueError("Invalid quality admission state")
        async with self._pool.connection() as connection:
            await connection.execute(
                """UPDATE knowledge.document_versions SET quality_report = %s, status = %s
                   WHERE id = %s AND status IN ('processing', 'failed_retryable')""",
                (Jsonb(report), status, version_id),
            )

    async def save_index_report(
        self, *, version_id: str, report: dict[str, Any], accepted: bool
    ) -> None:
        async with self._pool.connection() as connection, connection.transaction():
                row = await self._fetchone(
                    connection,
                    "SELECT quality_report,status FROM knowledge.document_versions WHERE id = %s FOR UPDATE",
                    (version_id,),
                )
                if not row or row["quality_report"] is None:
                    raise InvalidVersionTransition("Quality report is missing")
                if row["quality_report"].get("quality_status") != "passed":
                    raise InvalidVersionTransition("Version has not passed quality admission")
                await connection.execute(
                    """UPDATE knowledge.document_versions SET index_report = %s, status = %s
                       WHERE id = %s AND status IN ('quality_passed', 'index_pending', 'failed_retryable')""",
                    (Jsonb(report), "ready" if accepted else "index_pending", version_id),
                )

    async def mark_version_rejected(self, *, version_id: str, report: dict[str, Any]) -> None:
        await self.save_quality_report(version_id=version_id, report=report, status="rejected")

    async def complete_job(self, *, job_id: str, worker_id: str) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """UPDATE knowledge.jobs SET status = 'succeeded', lease_owner = NULL,
                          lease_expires_at = NULL, error_code = NULL, updated_at = now()
                   WHERE id = %s AND lease_owner = %s AND status = 'processing' RETURNING id""",
                (job_id, worker_id),
            )
            return await cursor.fetchone() is not None

    async def fail_permanently(self, *, job_id: str, worker_id: str, error_code: str) -> bool:
        async with self._pool.connection() as connection, connection.transaction():
            row = await self._fetchone(
                connection,
                """UPDATE knowledge.jobs SET status = 'failed_permanent', error_code = %s,
                          lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                   WHERE id = %s AND lease_owner = %s AND status = 'processing'
                   RETURNING version_id""",
                (error_code[:100], job_id, worker_id),
            )
            if not row:
                return False
            await connection.execute(
                "UPDATE knowledge.document_versions SET status = 'failed_permanent' WHERE id = %s AND status NOT IN ('ready','active','archived','rejected')",
                (row["version_id"],),
            )
            return True

    async def finalize_expired_dead_letters(self) -> int:
        async with self._pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                """UPDATE knowledge.jobs SET status = 'failed_permanent',
                          error_code = 'lease_expired_max_attempts', lease_owner = NULL,
                          lease_expires_at = NULL, updated_at = now()
                   WHERE status = 'processing' AND lease_expires_at < now()
                     AND attempt_count >= max_attempts RETURNING version_id"""
            )
            rows = await cursor.fetchall()
            for row in rows:
                await connection.execute(
                    "UPDATE knowledge.document_versions SET status = 'failed_permanent' WHERE id = %s AND status NOT IN ('ready','active','archived','rejected')",
                    (row["version_id"],),
                )
            return len(rows)

    async def enqueue_reindex(
        self, *, project_id: str, kb_id: str, document_id: str, actor_id: str, correlation_id: str
    ) -> dict[str, str]:
        async with self._pool.connection() as connection, connection.transaction():
            document = await self._fetchone(
                connection,
                """SELECT id, title, metadata FROM knowledge.documents
                   WHERE id = %s AND project_id = %s AND kb_id = %s AND tombstoned_at IS NULL
                   FOR UPDATE""",
                (document_id, project_id, kb_id),
            )
            if not document:
                raise LookupError("Document not found")
            source = await self._fetchone(
                connection,
                """SELECT storage_key, document_version FROM knowledge.document_versions
                   WHERE document_id = %s ORDER BY version_number DESC LIMIT 1""",
                (document_id,),
            )
            if not source:
                raise LookupError("Document version not found")
            version_id, job_id = str(uuid4()), str(uuid4())
            index_version = f"index-{uuid4().hex[:12]}"
            fingerprint = f"reindex:{source['document_version']}:{index_version}"
            await connection.execute(
                """INSERT INTO knowledge.document_versions
                   (id, document_id, version_number, document_version, index_version, status,
                    storage_key, request_fingerprint)
                   SELECT %s, %s, COALESCE(MAX(version_number), 0) + 1, %s, %s, 'processing', %s, %s
                   FROM knowledge.document_versions WHERE document_id = %s""",
                (version_id, document_id, source["document_version"], index_version,
                 source["storage_key"], fingerprint, document_id),
            )
            await connection.execute(
                """INSERT INTO knowledge.jobs
                   (id, project_id, kb_id, document_id, version_id, operation, status,
                    request_fingerprint, correlation_id)
                   VALUES (%s, %s, %s, %s, %s, 'reindex', 'queued', %s, %s)""",
                (job_id, project_id, kb_id, document_id, version_id, fingerprint, correlation_id),
            )
            await connection.execute(
                """INSERT INTO knowledge.audit_events
                   (id, actor_id, action, project_id, kb_id, resource_id, correlation_id)
                   VALUES (%s, %s, 'document.reindex', %s, %s, %s, %s)""",
                (str(uuid4()), actor_id, project_id, kb_id, document_id, correlation_id),
            )
            return {"job_id": job_id, "version_id": version_id}

    async def create_upload(
        self,
        *,
        project_id: str,
        kb_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        storage_key: str,
        title: str,
        metadata: dict[str, Any],
        actor_id: str,
        correlation_id: str,
    ) -> dict[str, str | bool]:
        async with self._pool.connection() as connection, connection.transaction():
            existing = await self._fetchone(
                connection,
                """SELECT id, document_id, version_id, request_fingerprint
                       FROM knowledge.jobs
                       WHERE project_id = %s AND idempotency_key = %s
                       FOR UPDATE""",
                (project_id, idempotency_key),
            )
            if existing:
                if existing["request_fingerprint"] != request_fingerprint:
                    raise IdempotencyConflict("Idempotency key conflicts with another request")
                return {
                    "job_id": existing["id"],
                    "document_id": existing["document_id"],
                    "version_id": existing["version_id"],
                    "replayed": True,
                }

            document_id, version_id, job_id = (str(uuid4()) for _ in range(3))
            await connection.execute(
                """INSERT INTO knowledge.documents
                       (id, project_id, kb_id, title, metadata, source_type)
                       VALUES (%s, %s, %s, %s, %s, 'manual')""",
                (document_id, project_id, kb_id, title, Jsonb(metadata)),
            )
            await connection.execute(
                """INSERT INTO knowledge.document_versions
                       (id, document_id, version_number, document_version, index_version,
                        status, storage_key, request_fingerprint)
                       VALUES (%s, %s, 1, %s, %s, 'processing', %s, %s)""",
                (version_id, document_id, version_id, "index-1", storage_key, request_fingerprint),
            )
            await connection.execute(
                """INSERT INTO knowledge.jobs
                       (id, project_id, kb_id, document_id, version_id, operation, status,
                        idempotency_key, request_fingerprint, correlation_id)
                       VALUES (%s, %s, %s, %s, %s, 'ingest', 'queued', %s, %s, %s)""",
                (
                    job_id,
                    project_id,
                    kb_id,
                    document_id,
                    version_id,
                    idempotency_key,
                    request_fingerprint,
                    correlation_id,
                ),
            )
            await connection.execute(
                """INSERT INTO knowledge.audit_events
                       (id, actor_id, action, project_id, kb_id, resource_id, correlation_id)
                       VALUES (%s, %s, 'document.upload', %s, %s, %s, %s)""",
                (str(uuid4()), actor_id, project_id, kb_id, document_id, correlation_id),
            )
            return {
                "job_id": job_id,
                "document_id": document_id,
                "version_id": version_id,
                "replayed": False,
            }

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as connection:
            return await self._fetchone(
                connection,
                """SELECT id, project_id, kb_id, document_id, version_id, operation, status,
                          attempt_count, max_attempts, next_attempt_at, error_code, created_at
                   FROM knowledge.jobs WHERE id = %s""",
                (job_id,),
            )

    async def list_jobs(
        self, *, kb_id: str, limit: int = 50, before: tuple[datetime, str] | None = None
    ) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 100)
        async with self._pool.connection() as connection:
            if before:
                cursor = await connection.execute(
                    """SELECT id, document_id, version_id, operation, status, attempt_count,
                              max_attempts, created_at
                       FROM knowledge.jobs WHERE kb_id = %s AND (created_at, id) < (%s, %s)
                       ORDER BY created_at DESC, id DESC LIMIT %s""",
                    (kb_id, before[0], before[1], limit),
                )
            else:
                cursor = await connection.execute(
                    """SELECT id, document_id, version_id, operation, status, attempt_count,
                              max_attempts, created_at
                       FROM knowledge.jobs WHERE kb_id = %s
                       ORDER BY created_at DESC, id DESC LIMIT %s""",
                    (kb_id, limit),
                )
            return list(await cursor.fetchall())

    async def claim_next_job(self, *, worker_id: str, lease_seconds: int = 60) -> dict[str, Any] | None:
        async with self._pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                """SELECT id FROM knowledge.jobs
                       WHERE ((status IN ('queued', 'failed_retryable') AND next_attempt_at <= now())
                         OR (status = 'processing' AND lease_expires_at < now()))
                         AND attempt_count < max_attempts
                         AND (lease_expires_at IS NULL OR lease_expires_at < now())
                       ORDER BY next_attempt_at, created_at, id
                       FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            row = await cursor.fetchone()
            if not row:
                return None
            claimed = await self._fetchone(
                connection,
                """UPDATE knowledge.jobs SET status = 'processing', lease_owner = %s,
                              lease_expires_at = now() + (%s * interval '1 second'),
                              attempt_count = attempt_count + 1, updated_at = now()
                       WHERE id = %s RETURNING *""",
                (worker_id, lease_seconds, row["id"]),
            )
            return claimed

    async def heartbeat(self, *, job_id: str, worker_id: str, lease_seconds: int = 60) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """UPDATE knowledge.jobs SET lease_expires_at = now() + (%s * interval '1 second'),
                          updated_at = now()
                   WHERE id = %s AND lease_owner = %s AND status = 'processing'
                   RETURNING id""",
                (lease_seconds, job_id, worker_id),
            )
            return await cursor.fetchone() is not None

    async def schedule_retry(
        self,
        *,
        job_id: str,
        worker_id: str,
        error_code: str,
        base_seconds: float = 2.0,
        max_seconds: float = 300.0,
    ) -> str:
        async with self._pool.connection() as connection, connection.transaction():
            job = await self._fetchone(
                connection,
                """SELECT attempt_count, max_attempts, version_id FROM knowledge.jobs
                       WHERE id = %s AND lease_owner = %s AND status = 'processing' FOR UPDATE""",
                (job_id, worker_id),
            )
            if not job:
                raise InvalidVersionTransition("Worker no longer owns this job lease")
            terminal = job["attempt_count"] >= job["max_attempts"]
            status = "failed_permanent" if terminal else "failed_retryable"
            delay = retry_delay_seconds(
                max(1, job["attempt_count"]), base_seconds=base_seconds, max_seconds=max_seconds
            )
            await connection.execute(
                """UPDATE knowledge.jobs SET status = %s, error_code = %s,
                              next_attempt_at = now() + (%s * interval '1 second'),
                              lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                       WHERE id = %s""",
                (status, error_code[:100], delay, job_id),
            )
            version_status = status
            await connection.execute(
                "UPDATE knowledge.document_versions SET status = %s WHERE id = %s AND status NOT IN ('ready','active','archived','rejected')",
                (version_status, job["version_id"]),
            )
            return status

    async def retry_job(self, job_id: str) -> bool:
        async with self._pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                """UPDATE knowledge.jobs SET status = 'queued', next_attempt_at = now(),
                              error_code = NULL, lease_owner = NULL, lease_expires_at = NULL,
                              updated_at = now()
                       WHERE id = %s AND status = 'failed_retryable' RETURNING version_id""",
                (job_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return False
            await connection.execute(
                "UPDATE knowledge.document_versions SET status = 'processing' WHERE id = %s",
                (row["version_id"],),
            )
            return True

    async def begin_activation(
        self,
        *,
        project_id: str,
        kb_id: str,
        document_id: str,
        version_id: str,
    ) -> str:
        operation_id, job_id = str(uuid4()), str(uuid4())
        async with self._pool.connection() as connection, connection.transaction():
                version = await self._fetchone(
                    connection,
                    """SELECT v.document_version, v.index_version, v.status, v.quality_report,
                              v.index_report
                       FROM knowledge.document_versions v
                       JOIN knowledge.documents d ON d.id = v.document_id
                       WHERE v.id = %s AND v.document_id = %s AND d.project_id = %s
                         AND d.kb_id = %s AND d.tombstoned_at IS NULL
                       FOR UPDATE OF d""",
                    (version_id, document_id, project_id, kb_id),
                )
                if not version or version["status"] not in {"ready", "archived"}:
                    raise InvalidVersionTransition("Only ready or archived versions can be activated")
                if not version["quality_report"] or version["quality_report"].get("quality_status") != "passed":
                    raise InvalidVersionTransition("Version has not passed quality admission")
                if not version["index_report"] or version["index_report"].get("index_status") != "accepted":
                    raise InvalidVersionTransition("Version index has not been accepted")
                pending = await self._fetchone(
                    connection,
                    """SELECT id, to_document_version, to_index_version
                       FROM knowledge.activation_operations
                       WHERE project_id = %s AND kb_id = %s AND document_id = %s
                         AND status IN ('pending', 'pointer_switched') FOR UPDATE""",
                    (project_id, kb_id, document_id),
                )
                if pending:
                    if (
                        pending["to_document_version"] == version["document_version"]
                        and pending["to_index_version"] == version["index_version"]
                    ):
                        return str(pending["id"])
                    raise InvalidVersionTransition("Another activation is already in progress")
                current = await self._fetchone(
                    connection,
                    """SELECT document_version, index_version FROM knowledge.project_active_versions
                       WHERE project_id = %s AND kb_id = %s AND document_id = %s FOR UPDATE""",
                    (project_id, kb_id, document_id),
                )
                await connection.execute(
                    """INSERT INTO knowledge.jobs
                       (id, project_id, kb_id, document_id, version_id, operation, status,
                        request_fingerprint, correlation_id)
                       VALUES (%s, %s, %s, %s, %s, 'activate', 'queued', %s, %s)""",
                    (
                        job_id,
                        project_id,
                        kb_id,
                        document_id,
                        version_id,
                        f"activate:{version_id}:{operation_id}",
                        operation_id,
                    ),
                )
                await connection.execute(
                    """INSERT INTO knowledge.activation_operations
                       (id, job_id, project_id, kb_id, document_id, from_document_version,
                        from_index_version, to_document_version, to_index_version, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')""",
                    (
                        operation_id,
                        job_id,
                        project_id,
                        kb_id,
                        document_id,
                        current["document_version"] if current else None,
                        current["index_version"] if current else None,
                        version["document_version"],
                        version["index_version"],
                    ),
                )
        return operation_id

    async def get_activation_operation(self, operation_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as connection:
            return await self._fetchone(
                connection,
                """SELECT a.*,
                          target.index_report ->> 'expected_chunk_count' AS target_chunk_count,
                          old.index_report ->> 'expected_chunk_count' AS old_chunk_count
                   FROM knowledge.activation_operations a
                   JOIN knowledge.document_versions target
                     ON target.document_id = a.document_id
                    AND target.document_version = a.to_document_version
                    AND target.index_version = a.to_index_version
                   LEFT JOIN knowledge.document_versions old
                     ON old.document_id = a.document_id
                    AND old.document_version = a.from_document_version
                    AND old.index_version = a.from_index_version
                   WHERE a.id = %s""",
                (operation_id,),
            )

    async def switch_active_version(self, operation_id: str) -> None:
        async with self._pool.connection() as connection, connection.transaction():
            operation = await self._fetchone(
                connection,
                "SELECT * FROM knowledge.activation_operations WHERE id = %s FOR UPDATE",
                (operation_id,),
            )
            if not operation or operation["status"] not in {"pending", "pointer_switched"}:
                raise InvalidVersionTransition("Activation operation is not switchable")
            if operation["status"] == "pointer_switched":
                return
            document = await self._fetchone(
                connection,
                """SELECT tombstoned_at FROM knowledge.documents
                   WHERE id = %s FOR UPDATE""",
                (operation["document_id"],),
            )
            if document is None or document["tombstoned_at"] is not None:
                raise InvalidVersionTransition("Tombstoned documents cannot be activated")
            await connection.execute(
                """INSERT INTO knowledge.project_active_versions
                       (project_id, kb_id, document_id, document_version, index_version)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (project_id, kb_id, document_id) DO UPDATE SET
                         document_version = EXCLUDED.document_version,
                         index_version = EXCLUDED.index_version, updated_at = now()""",
                (
                    operation["project_id"],
                    operation["kb_id"],
                    operation["document_id"],
                    operation["to_document_version"],
                    operation["to_index_version"],
                ),
            )
            if operation["from_document_version"]:
                await connection.execute(
                    """UPDATE knowledge.document_versions SET status = 'archived'
                           WHERE document_id = %s AND document_version = %s AND index_version=%s""",
                    (operation["document_id"], operation["from_document_version"], operation["from_index_version"]),
                )
            await connection.execute(
                """UPDATE knowledge.document_versions SET status = 'active'
                       WHERE document_id = %s AND document_version = %s AND index_version=%s""",
                (operation["document_id"], operation["to_document_version"], operation["to_index_version"]),
            )
            await connection.execute(
                """UPDATE knowledge.activation_operations SET status = 'pointer_switched',
                              updated_at = now() WHERE id = %s""",
                (operation_id,),
            )
            await connection.execute(
                """INSERT INTO knowledge.invalidation_outbox (id, dedupe_key, payload)
                       VALUES (%s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING""",
                (
                    str(uuid4()),
                    f"activate:{operation_id}",
                    Jsonb({"project_id": operation["project_id"], "kb_id": operation["kb_id"],
                           "document_id": operation["document_id"],
                           "document_version": operation["to_document_version"],
                           "index_version": operation["to_index_version"]}),
                ),
            )

    async def tombstone_document(self, *, project_id: str, kb_id: str, document_id: str) -> bool:
        async with self._pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                """UPDATE knowledge.documents SET tombstoned_at = now(), updated_at = now()
                       WHERE id = %s AND project_id = %s AND kb_id = %s AND tombstoned_at IS NULL
                       RETURNING id""",
                (document_id, project_id, kb_id),
            )
            if not await cursor.fetchone():
                return False
            await connection.execute(
                """INSERT INTO knowledge.tombstones (document_id, project_id, kb_id)
                       VALUES (%s, %s, %s) ON CONFLICT (document_id) DO NOTHING""",
                (document_id, project_id, kb_id),
            )
            await connection.execute(
                """DELETE FROM knowledge.project_active_versions
                       WHERE project_id = %s AND kb_id = %s AND document_id = %s""",
                (project_id, kb_id, document_id),
            )
            await connection.execute(
                """INSERT INTO knowledge.invalidation_outbox (id, dedupe_key, payload)
                       VALUES (%s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING""",
                (
                    str(uuid4()),
                    f"delete:{document_id}",
                    Jsonb({"project_id": project_id, "kb_id": kb_id, "document_id": document_id}),
                ),
            )
            return True

    async def finish_activation(self, operation_id: str) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """UPDATE knowledge.activation_operations SET status = 'succeeded', updated_at = now()
                   WHERE id = %s AND status = 'pointer_switched'""",
                (operation_id,),
            )

    @staticmethod
    async def _fetchone(connection: Any, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        cursor = await connection.execute(query, params)
        row = await cursor.fetchone()
        return dict(row) if row is not None else None
