"""Transactional business state; claim tokens fence expired workers."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from testmind.core.security import AuthContext
from testmind.models.task import allowed_transition


class TaskConflict(Exception):
    pass


class TaskTransitionError(Exception):
    pass


class TaskCapacityError(Exception):
    pass


class TaskRepository:
    def __init__(self, pool: Any, *, queue_limit: int | None = None) -> None:
        if queue_limit is None:
            queue_limit = int(os.getenv("TASK_QUEUE_LIMIT_PER_USER", "128"))
        if not 1 <= queue_limit <= 10000:
            raise ValueError("queue_limit must be between 1 and 10000")
        self.pool = pool
        self.queue_limit = queue_limit

    @staticmethod
    async def _row(connection: Any, sql: str, values: tuple[Any, ...]) -> dict[str, Any] | None:
        row = await (await connection.execute(sql, values)).fetchone()
        return dict(row) if row else None

    @classmethod
    async def _event(cls, connection: Any, task_id: str, data: dict[str, Any]) -> None:
        task = await cls._row(
            connection,
            "UPDATE business.tasks SET event_seq=event_seq+1, updated_at=now() WHERE id=%s RETURNING event_seq,status",
            (task_id,),
        )
        assert task is not None
        await connection.execute(
            "INSERT INTO business.task_events(task_id,seq,status,data) VALUES (%s,%s,%s,%s)",
            (task_id, task["event_seq"], task["status"], Jsonb(data)),
        )

    async def context(self, user_id: str) -> AuthContext | None:
        async with self.pool.connection() as connection:
            user = await self._row(
                connection,
                "SELECT user_id FROM business.demo_users WHERE user_id=%s AND enabled=true",
                (user_id,),
            )
            if user is None:
                return None
            cursor = await connection.execute(
                "SELECT project_id,kb_id FROM business.demo_grants WHERE user_id=%s AND revoked=false",
                (user_id,),
            )
            grants = await cursor.fetchall()
        return AuthContext(
            user_id,
            frozenset(g["project_id"] for g in grants),
            frozenset(g["kb_id"] for g in grants),
        )

    async def submit(
        self, *, user_id: str, project_id: str, session_id: str, query: str, idempotency_key: str
    ) -> dict[str, Any]:
        fingerprint = hashlib.sha256(
            json.dumps([session_id, query], ensure_ascii=False).encode()
        ).hexdigest()
        task_id, trace_id = str(uuid4()), str(uuid4())
        async with self.pool.connection() as connection, connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"project:{project_id}",))
            existing = await self._row(connection, "SELECT * FROM business.tasks WHERE user_id=%s AND project_id=%s AND idempotency_key=%s", (user_id, project_id, idempotency_key))
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise TaskConflict("idempotency_conflict")
                return {**existing, "replayed": True}
            await self._queue_capacity(connection, user_id, project_id)
            task = await self._row(
                connection,
                """INSERT INTO business.tasks(id,user_id,project_id,session_id,query,status,trace_id,execution_thread_id,idempotency_key,fingerprint)
                VALUES (%s,%s,%s,%s,%s,'queued',%s,%s,%s,%s)
                ON CONFLICT(user_id,project_id,idempotency_key) DO NOTHING RETURNING *""",
                (
                    task_id,
                    user_id,
                    project_id,
                    session_id,
                    query,
                    trace_id,
                    f"task:{task_id}",
                    idempotency_key,
                    fingerprint,
                ),
            )
            replayed = task is None
            if task is None:
                task = await self._row(
                    connection,
                    "SELECT * FROM business.tasks WHERE user_id=%s AND project_id=%s AND idempotency_key=%s",
                    (user_id, project_id, idempotency_key),
                )
                assert task is not None
                if task["fingerprint"] != fingerprint:
                    raise TaskConflict("idempotency_conflict")
            else:
                await self._event(connection, task_id, {"kind": "accepted"})
        return {**task, "replayed": replayed}

    async def _queue_capacity(self, connection: Any, user_id: str, project_id: str) -> None:
        row = await self._row(connection, """SELECT count(*) AS project_count,
            count(*) FILTER(WHERE user_id=%s) AS user_count FROM business.tasks
            WHERE project_id=%s AND status IN ('queued','running')""", (user_id, project_id))
        assert row is not None
        if row["user_count"] >= self.queue_limit or row["project_count"] >= 2 * self.queue_limit:
            raise TaskCapacityError("task_queue_capacity_exceeded")

    async def record_metric(self, component: str, metric: str, seconds: float, *, failed: bool = False) -> None:
        if component not in {"worker", "rag", "model"} or metric not in {"execution", "retrieval", "answer"} or not 0 <= seconds <= 3600:
            return
        async with self.pool.connection() as connection:
            await connection.execute("""INSERT INTO business.runtime_metrics
                (component,metric,window_start,observations,failures,total_seconds,max_seconds)
                VALUES (%s,%s,date_trunc('minute',now()),1,%s,%s,%s)
                ON CONFLICT(component,metric,window_start) DO UPDATE SET
                observations=business.runtime_metrics.observations+1,
                failures=business.runtime_metrics.failures+EXCLUDED.failures,
                total_seconds=business.runtime_metrics.total_seconds+EXCLUDED.total_seconds,
                max_seconds=GREATEST(business.runtime_metrics.max_seconds,EXCLUDED.max_seconds)""",
                (component, metric, int(failed), seconds, seconds))
            await connection.execute("DELETE FROM business.runtime_metrics WHERE window_start<now()-interval '7 days'")

    async def retry(self, task_id: str, *, user_id: str, expected_version: int) -> None:
        async with self.pool.connection() as connection, connection.transaction():
            candidate = await self._row(connection, "SELECT project_id FROM business.tasks WHERE id=%s AND user_id=%s", (task_id, user_id))
            if candidate is None:
                raise TaskTransitionError("task_not_found")
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"project:{candidate['project_id']}",))
            task = await self._row(connection, "SELECT * FROM business.tasks WHERE id=%s AND user_id=%s FOR UPDATE", (task_id, user_id))
            grant = await self._row(connection, "SELECT g.user_id FROM business.demo_grants g JOIN business.demo_users u USING(user_id) WHERE g.user_id=%s AND g.project_id=%s AND g.revoked=false AND u.enabled=true LIMIT 1 FOR SHARE OF g,u", (user_id, candidate["project_id"]))
            if grant is None:
                raise PermissionError("project_access_denied")
            assert task is not None
            if task["version"] != expected_version:
                raise TaskConflict("task_version_conflict")
            if task["status"] != "failed" or task["cancel_requested"] or task["manual_retries"] >= 3 or task["error_code"] not in {"dependency_unavailable", "task_timeout", "task_deadline_exhausted", "attempt_budget_exhausted", "task_execution_failed", "answer_unavailable", "retrieval_unavailable", "dialogue_node_timeout", "dialogue_node_failed"}:
                raise TaskTransitionError("task_not_retryable")
            asset = await self._row(connection, "SELECT id FROM business.case_assets WHERE task_id=%s LIMIT 1", (task_id,))
            if asset:
                raise TaskTransitionError("case_task_requires_review_or_reconciliation")
            await self._queue_capacity(connection, user_id, task["project_id"])
            await connection.execute("""UPDATE business.tasks SET status='queued',version=version+1,
                manual_retries=manual_retries+1,attempt_count=0,result='{}',error_code=NULL,
                execution_deadline_at=NULL,next_attempt_at=now(),execution_thread_id=%s WHERE id=%s""",
                (f"task:{task_id}:retry:{uuid4()}", task_id))
            await self._event(connection, task_id, {"kind": "manual_retry", "actor_id": user_id})

    async def get(self, task_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            return await self._row(
                connection, "SELECT * FROM business.tasks WHERE id=%s", (task_id,)
            )

    async def list_tasks(self, user_id: str, projects: frozenset[str]) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM business.tasks WHERE user_id=%s AND project_id=ANY(%s) ORDER BY created_at DESC LIMIT 100",
                (user_id, sorted(projects)),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def events(self, task_id: str, *, after: int) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT seq,status,data,created_at FROM business.task_events WHERE task_id=%s AND seq>%s ORDER BY seq LIMIT 100",
                (task_id, after),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def claim(
        self, *, task_id: str | None = None, lease_seconds: float = 120, execution_budget_seconds: float = 180
    ) -> dict[str, Any] | None:
        async with self.pool.connection() as connection, connection.transaction():
            task = await self._row(
                connection,
                """SELECT *, execution_deadline_at<=now() AS deadline_exhausted FROM business.tasks WHERE (%s::text IS NULL OR id=%s)
                AND ((status='queued' AND next_attempt_at<=now()) OR (status='running' AND lease_expires_at<now()))
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",
                (task_id, task_id),
            )
            if task is None:
                return None
            if task["cancel_requested"] or task["deadline_exhausted"] or task["attempt_count"] >= task["max_attempts"]:
                code = "cancelled" if task["cancel_requested"] else "task_deadline_exhausted" if task["deadline_exhausted"] else "attempt_budget_exhausted"
                await connection.execute(
                    "UPDATE business.tasks SET status=%s,error_code=%s,version=version+1,claim_token=NULL,lease_expires_at=NULL WHERE id=%s",
                    ("cancelled" if task["cancel_requested"] else "failed", code, task["id"]),
                )
                await self._event(
                    connection,
                    task["id"],
                    {"kind": "stopped", "error_code": code},
                )
                return None
            task = await self._row(
                connection,
                """UPDATE business.tasks SET status='running',claim_token=%s,lease_expires_at=now()+(%s*interval '1 second'),execution_deadline_at=COALESCE(execution_deadline_at,now()+(%s*interval '1 second')),attempt_count=attempt_count+1,version=version+1 WHERE id=%s RETURNING *,extract(epoch from (execution_deadline_at-now())) AS budget_remaining""",
                (str(uuid4()), lease_seconds, execution_budget_seconds, task["id"]),
            )
            assert task is not None
            await self._event(
                connection, task["id"], {"kind": "started", "attempt": task["attempt_count"]}
            )
            return task

    async def finish(
        self,
        claim: dict[str, Any],
        *,
        status: str,
        result: dict[str, Any],
        error_code: str | None = None,
        retry_seconds: float = 0,
    ) -> bool:
        if not allowed_transition("running", status):
            raise TaskTransitionError("invalid_task_transition")
        async with self.pool.connection() as connection, connection.transaction():
            current = await self._row(
                connection, "SELECT * FROM business.tasks WHERE id=%s FOR UPDATE", (claim["id"],)
            )
            if (
                current is None
                or current["status"] != "running"
                or current["claim_token"] != claim["claim_token"]
                or current["lease_expires_at"] is None
            ):
                return False
            cursor = await connection.execute(
                "SELECT lease_expires_at>now() AS valid FROM business.tasks WHERE id=%s",
                (claim["id"],),
            )
            if not (await cursor.fetchone())["valid"]:
                return False
            if current["cancel_requested"]:
                status, result, error_code = "cancelled", {}, "cancelled"
            await connection.execute(
                """UPDATE business.tasks SET status=%s,result=%s,error_code=%s,intent=%s,version=version+1,claim_token=NULL,lease_expires_at=NULL,next_attempt_at=now()+(%s*interval '1 second') WHERE id=%s""",
                (
                    status,
                    Jsonb(result),
                    error_code,
                    result.get("intent"),
                    retry_seconds,
                    claim["id"],
                ),
            )
            await self._event(
                connection, claim["id"], {"kind": "stopped", "error_code": error_code}
            )
            if status == "completed":
                await connection.execute(
                    """INSERT INTO business.conversation_turns(task_id,user_id,project_id,session_id,query,answer) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(task_id) DO NOTHING""",
                    (
                        claim["id"],
                        claim["user_id"],
                        claim["project_id"],
                        claim["session_id"],
                        claim["query"],
                        result.get("answer", ""),
                    ),
                )
            return True

    async def resume(self, task_id: str, *, expected_version: int, query: str) -> None:
        async with self.pool.connection() as connection, connection.transaction():
            task = await self._row(
                connection, "SELECT * FROM business.tasks WHERE id=%s FOR UPDATE", (task_id,)
            )
            if task is None or task["status"] != "waiting_input":
                raise TaskTransitionError("task_not_waiting_input")
            if task["version"] != expected_version:
                raise TaskConflict("task_version_conflict")
            await connection.execute(
                """UPDATE business.tasks SET query=%s,status='queued',input_version=input_version+1,version=version+1,attempt_count=0,result='{}',error_code=NULL,execution_deadline_at=NULL,next_attempt_at=now() WHERE id=%s""",
                (query, task_id),
            )
            await self._event(connection, task_id, {"kind": "resumed"})

    async def cancel(self, task_id: str) -> None:
        async with self.pool.connection() as connection, connection.transaction():
            task = await self._row(
                connection, "SELECT * FROM business.tasks WHERE id=%s FOR UPDATE", (task_id,)
            )
            if task is None or task["status"] in {
                "completed",
                "cancelled",
                "failed",
                "denied",
                "rejected",
            }:
                return
            if task["status"] == "needs_reconciliation":
                raise TaskTransitionError("external_result_unknown")
            if task["cancel_requested"]:
                return
            await connection.execute(
                """UPDATE business.tasks SET cancel_requested=true,status=CASE WHEN status='running' THEN status ELSE 'cancelled' END,version=version+1 WHERE id=%s""",
                (task_id,),
            )
            await self._event(connection, task_id, {"kind": "cancel_requested"})

    async def history(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT t.* FROM business.conversation_turns c JOIN business.tasks t ON t.id=c.task_id WHERE c.user_id=%s AND c.project_id=%s AND c.session_id=%s AND c.task_id<>%s ORDER BY c.created_at DESC LIMIT 3""",
                (task["user_id"], task["project_id"], task["session_id"], task["id"]),
            )
            candidates = [dict(row) for row in await cursor.fetchall()]
        auth = await self.context(task["user_id"])
        if auth is None:
            return []
        turns = []
        for item in reversed(candidates):
            if await self.result_accessible(item, auth):
                turns.append({"query": item["query"], "answer": item["result"].get("answer", "")})
        return turns

    async def result_accessible(self, task: dict[str, Any], auth: AuthContext) -> bool:
        if task["user_id"] != auth.user_id or task["project_id"] not in auth.authorized_project_ids:
            return False
        citations = task["result"].get("citations", [])
        if not isinstance(citations, list) or len(citations) > 100:
            return False
        async with self.pool.connection() as connection:
            for citation in citations:
                if not isinstance(citation, dict):
                    return False
                kb_id = citation.get("knowledge_base_id")
                if kb_id not in auth.authorized_knowledge_base_ids:
                    return False
                current = await self._row(connection, """SELECT a.document_version,a.index_version
                    FROM knowledge.project_active_versions a JOIN knowledge.documents d ON d.id=a.document_id
                    JOIN business.demo_grants g ON g.project_id=a.project_id AND g.kb_id=a.kb_id
                    JOIN business.demo_users u ON u.user_id=g.user_id
                    WHERE g.user_id=%s AND g.revoked=false AND u.enabled=true AND a.project_id=%s
                    AND a.kb_id=%s AND a.document_id=%s AND d.tombstoned_at IS NULL""",
                    (auth.user_id, task["project_id"], kb_id, citation.get("document_id")))
                if current is None or current["document_version"] != citation.get("document_version") or current["index_version"] != citation.get("index_version"):
                    return False
            graph = task["result"].get("graph_result")
            if graph:
                grant = await self._row(connection, "SELECT permission_version FROM graph_sync.project_memberships WHERE project_id=%s AND user_id=%s AND revoked=false", (task["project_id"], auth.user_id))
                if grant is None or grant["permission_version"] != graph.get("permission_version"):
                    return False
        return True

    async def create_approval(
        self, task_id: str, *, asset_version: int, content_hash: str
    ) -> dict[str, Any]:
        async with self.pool.connection() as connection, connection.transaction():
            task = await self._row(
                connection,
                "SELECT project_id FROM business.tasks WHERE id=%s FOR UPDATE",
                (task_id,),
            )
            if task is None:
                raise TaskTransitionError("task_not_found")
            approval = await self._row(
                connection,
                "INSERT INTO business.approvals(id,task_id,project_id,asset_version,content_hash) VALUES (%s,%s,%s,%s,%s) RETURNING *",
                (str(uuid4()), task_id, task["project_id"], asset_version, content_hash),
            )
            assert approval is not None
            await self._event(
                connection, task_id, {"kind": "approval_created", "approval_id": approval["id"]}
            )
            return approval

    async def approval(self, approval_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            return await self._row(
                connection, "SELECT * FROM business.approvals WHERE id=%s", (approval_id,)
            )

    async def decide_approval(
        self,
        approval_id: str,
        *,
        reviewer: str,
        decision: str,
        asset_version: int,
        content_hash: str,
    ) -> dict[str, Any]:
        if decision not in {"approved", "rejected", "modified"}:
            raise TaskTransitionError("invalid_approval_decision")
        async with self.pool.connection() as connection, connection.transaction():
            approval = await self._row(
                connection,
                "SELECT * FROM business.approvals WHERE id=%s FOR UPDATE",
                (approval_id,),
            )
            if (
                approval is None
                or not approval["valid"]
                or approval["decision"] != "pending"
                or approval["asset_version"] != asset_version
                or approval["content_hash"] != content_hash
            ):
                raise TaskConflict("approval_version_conflict")
            updated = await self._row(
                connection,
                "UPDATE business.approvals SET decision=%s,reviewer_id=%s,decided_at=now(),valid=%s WHERE id=%s RETURNING *",
                (decision, reviewer, decision != "modified", approval_id),
            )
            assert updated is not None
            await self._event(
                connection,
                approval["task_id"],
                {"kind": "approval_decided", "decision": decision, "reviewer_id": reviewer},
            )
            return updated

    async def invalidate_approval(self, approval_id: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "UPDATE business.approvals SET valid=false WHERE id=%s", (approval_id,)
            )
