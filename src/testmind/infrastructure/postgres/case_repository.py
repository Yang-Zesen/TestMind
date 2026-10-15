"""Transactional case assets and approvals; external writes use a separate ledger."""

from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from testmind.infrastructure.postgres.graph_sync_repository import (
    GraphSyncRepository,
    _ConnectionPool,
)
from testmind.infrastructure.postgres.task_repository import TaskRepository
from testmind.models.graph_sync import EntityRef, GraphEvent, RelationSpec
from testmind.models.test_case import CaseBody, CaseSourceInput, digest


class CaseConflict(ValueError):
    pass


class CaseRepository(TaskRepository):
    async def _lock_project(self, connection: Any, project_id: str) -> None:
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"project:{project_id}",))

    async def _lock_resource_project(self, connection: Any, resource: str, identifier: str) -> None:
        queries = {"source": "SELECT project_id FROM business.case_sources WHERE id=%s",
                   "asset": "SELECT project_id FROM business.case_assets WHERE id=%s",
                   "task": "SELECT project_id FROM business.tasks WHERE id=%s",
                   "operation": "SELECT project_id FROM business.external_operations WHERE id=%s"}
        row = await self._row(connection, queries[resource], (identifier,))
        if row:
            await self._lock_project(connection, row["project_id"])

    async def _graph_event(self, connection: Any, *, project_id: str, entity_type: str,
                           source_id: str, revision: int, properties: dict[str, Any],
                           owner_id: str, stage: str,
                           relations: tuple[RelationSpec, ...] = (), deleted: bool = False) -> None:
        entity_type = "requirement" if entity_type == "openapi" else entity_type
        await self._lock_project(connection, project_id)
        event_id = f"case:{source_id}:v{revision}:stage:{stage}"
        receipt = await self._row(connection, "SELECT id FROM graph_sync.event_receipts WHERE source_instance='testmind-case' AND event_id=%s", (event_id,))
        if receipt:
            return
        current = await self._row(connection, "SELECT revision FROM graph_sync.entities WHERE entity_key=%s", (EntityRef("testmind-case", project_id, entity_type, source_id).key,))
        event = GraphEvent(
            event_id=event_id,
            source_instance="testmind-case", project_id=project_id,
            entity_type=entity_type, source_id=source_id, revision=current["revision"] + 1 if current else 1,
            operation="delete" if deleted else "upsert", owner_id=owner_id, properties=properties,
            relations=relations,
        )
        await GraphSyncRepository(_ConnectionPool(connection)).register_event(
            event, actor_id=owner_id
        )

    async def _finalize_task(self, connection: Any, task_id: str) -> None:
        task = await self._row(connection, "SELECT status FROM business.tasks WHERE id=%s FOR UPDATE", (task_id,))
        if task is None or task["status"] not in {"waiting_review", "needs_reconciliation"}:
            return
        cursor = await connection.execute("SELECT a.id,a.decision,o.status FROM business.case_assets a LEFT JOIN business.external_operations o ON o.asset_id=a.id AND o.asset_version=a.version WHERE a.task_id=%s", (task_id,))
        assets = await cursor.fetchall()
        if not assets:
            return
        if any(asset["status"] == "needs_reconciliation" for asset in assets):
            status = "needs_reconciliation"
        elif all(asset["decision"] == "rejected" or asset["status"] == "completed" for asset in assets):
            status = "completed" if any(asset["status"] == "completed" for asset in assets) else "rejected"
        else:
            status = "waiting_review"
        if status != task["status"]:
            await connection.execute("UPDATE business.tasks SET status=%s,version=version+1,updated_at=now() WHERE id=%s", (status, task_id))
            await self._event(connection, task_id, {"kind":"case_review_finalized", "outcome":status})

    async def create_source(self, payload: CaseSourceInput, user_id: str) -> dict[str, Any]:
        async with self.pool.connection() as connection, connection.transaction():
            row = await self._row(connection, "INSERT INTO business.case_sources(id,project_id,user_id,kind,title,content,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *", (str(uuid4()), payload.project_id, user_id, payload.kind, payload.title, payload.content, digest(payload.content)))
            assert row is not None
            await self._graph_event(connection, project_id=payload.project_id,
                entity_type=payload.kind, source_id=row["id"], revision=1,
                properties={"title": payload.title, "content_hash": row["content_hash"], "version": 1},
                owner_id=user_id, stage="source")
            return row

    async def source(self, source_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            return await self._row(connection, "SELECT * FROM business.case_sources WHERE id=%s", (source_id,))

    async def sources(self, user_id: str, project_id: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute("SELECT * FROM business.case_sources WHERE user_id=%s AND project_id=%s AND deleted=false ORDER BY updated_at DESC LIMIT 100", (user_id, project_id))
            return [dict(row) for row in await cursor.fetchall()]

    async def update_source(self, source_id: str, user_id: str, *, version: int, content: str) -> dict[str, Any]:
        async with self.pool.connection() as connection, connection.transaction():
            await self._lock_resource_project(connection, "source", source_id)
            source = await self._row(connection, "SELECT * FROM business.case_sources WHERE id=%s AND user_id=%s FOR UPDATE", (source_id, user_id))
            if source is None or source["version"] != version:
                raise CaseConflict("source_version_conflict")
            CaseSourceInput(project_id=source["project_id"], kind=source["kind"], title=source["title"], content=content)
            row = await self._row(connection, "UPDATE business.case_sources SET content=%s,content_hash=%s,version=version+1,updated_at=now() WHERE id=%s RETURNING *", (content, digest(content), source_id))
            assert row is not None
            await self._graph_event(connection, project_id=source["project_id"],
                entity_type=source["kind"], source_id=source_id, revision=row["version"],
                properties={"title": row["title"], "content_hash": row["content_hash"], "version": row["version"]},
                owner_id=user_id, stage="source")
            return row

    async def asset(self, asset_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            return await self._row(connection, "SELECT * FROM business.case_assets WHERE id=%s", (asset_id,))

    async def delete_source(self, source_id: str, user_id: str) -> None:
        async with self.pool.connection() as connection, connection.transaction():
            await self._lock_resource_project(connection, "source", source_id)
            row = await self._row(connection, "UPDATE business.case_sources SET deleted=true,version=version+1,updated_at=now() WHERE id=%s AND user_id=%s AND deleted=false RETURNING *", (source_id, user_id))
            if row:
                await self._graph_event(connection, project_id=row["project_id"], entity_type=row["kind"],
                    source_id=source_id, revision=row["version"], properties={}, owner_id=user_id,
                    stage="deleted", deleted=True)

    async def assets(self, task_id: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute("SELECT * FROM business.case_assets WHERE task_id=%s ORDER BY ordinal", (task_id,))
            return [dict(row) for row in await cursor.fetchall()]

    async def save_drafts(self, claim: dict[str, Any], sources: list[dict[str, Any]], bodies: list[CaseBody], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        snapshots = [{key: source[key] for key in ("id", "project_id", "kind", "title", "content", "version", "content_hash")} for source in sources]
        async with self.pool.connection() as connection, connection.transaction():
            await self._lock_resource_project(connection, "task", claim["id"])
            task = await self._row(connection, "SELECT * FROM business.tasks WHERE id=%s FOR UPDATE", (claim["id"],))
            if task is None or task["claim_token"] != claim["claim_token"] or task["status"] != "running" or task["cancel_requested"]:
                raise CaseConflict("stale_task_claim")
            for snapshot in snapshots:
                source = await self._row(connection, "SELECT * FROM business.case_sources WHERE id=%s FOR SHARE", (snapshot["id"],))
                if source is None or source["deleted"] or source["user_id"] != task["user_id"] or source["project_id"] != task["project_id"] or source["version"] != snapshot["version"]:
                    raise CaseConflict("source_snapshot_expired")
            for ordinal, body in enumerate(bodies):
                value = body.model_dump(mode="json")
                asset_id = str(uuid4())
                row = await self._row(connection, "INSERT INTO business.case_assets(id,task_id,project_id,user_id,ordinal,body,sources,evidence,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(task_id,ordinal) DO NOTHING RETURNING id", (asset_id, task["id"], task["project_id"], task["user_id"], ordinal, Jsonb(value), Jsonb(snapshots), Jsonb(evidence), digest(value)))
                if row:
                    await connection.execute("INSERT INTO business.case_asset_versions(asset_id,version,body,content_hash,actor_id) VALUES (%s,1,%s,%s,%s)", (asset_id, Jsonb(value), digest(value), task["user_id"]))
                    await connection.execute("INSERT INTO business.approvals(id,task_id,project_id,asset_version,content_hash,asset_id) VALUES (%s,%s,%s,1,%s,%s)", (str(uuid4()), task["id"], task["project_id"], digest(value), asset_id))
                    await self._graph_event(connection, project_id=task["project_id"],
                        entity_type="test_case", source_id=asset_id, revision=1,
                        properties={"title": value.get("title", ""), "version": 1, "decision": "pending"},
                        owner_id=task["user_id"], stage="draft",
                        relations=tuple(RelationSpec(kind="covers", target_source_instance="testmind-case",
                            target_type="requirement", target_source_id=source["id"]) for source in sources))
        return await self.assets(claim["id"])

    async def _lock_asset(self, connection: Any, asset_id: str, user_id: str, version: int, content_hash: str | None = None) -> dict[str, Any]:
        row = await self._row(connection, "SELECT * FROM business.case_assets WHERE id=%s AND user_id=%s FOR UPDATE", (asset_id, user_id))
        if row is None or row["version"] != version or (content_hash is not None and row["content_hash"] != content_hash):
            raise CaseConflict("case_version_conflict")
        operation = await self._row(connection, "SELECT id FROM business.external_operations WHERE asset_id=%s AND status IN ('prepared','in_flight','completed','needs_reconciliation') LIMIT 1", (asset_id,))
        if operation:
            raise CaseConflict("case_publication_locked")
        return row

    async def edit(self, asset_id: str, user_id: str, *, version: int, body: CaseBody) -> dict[str, Any]:
        value = body.model_dump(mode="json")
        async with self.pool.connection() as connection, connection.transaction():
            await self._lock_resource_project(connection, "asset", asset_id)
            asset = await self._lock_asset(connection, asset_id, user_id, version)
            await connection.execute("UPDATE business.approvals SET valid=false WHERE asset_id=%s", (asset_id,))
            await connection.execute("UPDATE business.case_assets SET body=%s,content_hash=%s,version=version+1,decision='pending',reviewer_id=NULL,updated_at=now() WHERE id=%s", (Jsonb(value), digest(value), asset_id))
            await connection.execute("INSERT INTO business.case_asset_versions(asset_id,version,body,content_hash,actor_id) VALUES (%s,%s,%s,%s,%s)", (asset_id, version + 1, Jsonb(value), digest(value), user_id))
            await connection.execute("INSERT INTO business.approvals(id,task_id,project_id,asset_version,content_hash,asset_id) VALUES (%s,%s,%s,%s,%s,%s)", (str(uuid4()), asset["task_id"], asset["project_id"], version + 1, digest(value), asset_id))
            await self._event(connection, asset["task_id"], {"kind":"case_modified", "asset_id":asset_id, "asset_version":version + 1, "actor_id":user_id})
            await self._asset_graph(connection, asset_id, stage="edited")
        result = await self.asset(asset_id)
        assert result is not None
        return result

    async def decide(self, asset_id: str, user_id: str, *, version: int, content_hash: str, decision: str) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise CaseConflict("invalid_case_decision")
        async with self.pool.connection() as connection, connection.transaction():
            await self._lock_resource_project(connection, "asset", asset_id)
            asset = await self._lock_asset(connection, asset_id, user_id, version, content_hash)
            if asset["decision"] != "pending":
                if asset["decision"] == decision:
                    return asset
                raise CaseConflict("case_already_reviewed")
            await connection.execute("UPDATE business.approvals SET decision=%s,reviewer_id=%s,decided_at=now() WHERE asset_id=%s AND asset_version=%s AND content_hash=%s AND valid=true", (decision, user_id, asset_id, version, content_hash))
            await connection.execute("UPDATE business.case_assets SET decision=%s,reviewer_id=%s,updated_at=now() WHERE id=%s", (decision, user_id, asset_id))
            await self._event(connection, asset["task_id"], {"kind":"case_reviewed", "asset_id":asset_id, "asset_version":version, "decision":decision, "actor_id":user_id})
            await self._asset_graph(connection, asset_id, stage=decision)
            await self._finalize_task(connection, asset["task_id"])
        result = await self.asset(asset_id)
        assert result is not None
        return result

    async def _validate_sources(self, connection: Any, asset: dict[str, Any]) -> None:
        for snapshot in asset["sources"]:
            current = await self._row(connection, "SELECT * FROM business.case_sources WHERE id=%s FOR SHARE", (snapshot["id"],))
            if current is None or current["deleted"] or current["version"] != snapshot["version"] or current["content_hash"] != snapshot["content_hash"]:
                raise CaseConflict("source_snapshot_expired")
        for evidence in asset["evidence"]:
            access = await self._row(connection, "SELECT g.user_id FROM business.demo_grants g JOIN business.demo_users u USING(user_id) WHERE g.user_id=%s AND g.project_id=%s AND g.kb_id=%s AND g.revoked=false AND u.enabled=true FOR SHARE OF g,u", (asset["user_id"], asset["project_id"], evidence["knowledge_base_id"]))
            if access is None:
                raise CaseConflict("source_evidence_access_revoked")
            current = await self._row(connection, "SELECT a.document_version,a.index_version FROM knowledge.project_active_versions a JOIN knowledge.documents d ON d.id=a.document_id WHERE a.document_id=%s AND a.project_id=%s AND a.kb_id=%s AND d.tombstoned_at IS NULL FOR SHARE OF a,d", (evidence["document_id"], asset["project_id"], evidence["knowledge_base_id"]))
            if current is None or current["document_version"] != evidence["document_version"] or current["index_version"] != evidence["index_version"]:
                raise CaseConflict("source_evidence_expired")

    async def _asset_graph(self, connection: Any, asset_id: str, *, stage: str,
                           operation: dict[str, Any] | None = None) -> None:
        asset = await self._row(connection, "SELECT * FROM business.case_assets WHERE id=%s", (asset_id,))
        assert asset is not None
        await self._graph_event(connection, project_id=asset["project_id"],
            entity_type="test_case", source_id=asset_id, revision=asset["version"],
            properties={"title": asset["body"]["title"], "version": asset["version"],
                "decision": asset["decision"], "content_hash": asset["content_hash"],
                "external_id": operation["external_id"] if operation else None,
                "publication_status": operation["status"] if operation else "not_published"},
            owner_id=asset["user_id"], stage=stage,
            relations=tuple(RelationSpec(kind="covers", target_source_instance="testmind-case",
                target_type="requirement", target_source_id=source["id"]) for source in asset["sources"]))

    async def prepare_publish(self, asset_id: str, user_id: str, *, version: int, content_hash: str) -> dict[str, Any]:
        async with self.pool.connection() as connection, connection.transaction():
            asset = await self._row(connection, "SELECT * FROM business.case_assets WHERE id=%s AND user_id=%s FOR UPDATE", (asset_id, user_id))
            if asset is None or asset["version"] != version or asset["content_hash"] != content_hash:
                raise CaseConflict("case_version_conflict")
            existing = await self._row(connection, "SELECT * FROM business.external_operations WHERE asset_id=%s AND asset_version=%s", (asset_id, version))
            if existing:
                return {**existing, "dispatch":False}
            approval = await self._row(connection, "SELECT * FROM business.approvals WHERE asset_id=%s AND asset_version=%s AND content_hash=%s AND decision='approved' AND valid=true FOR SHARE", (asset_id, version, content_hash))
            if not approval or asset["decision"] != "approved":
                raise CaseConflict("case_approval_required")
            auth = await self._row(connection, "SELECT g.user_id FROM business.demo_grants g JOIN business.demo_users u USING(user_id) WHERE g.user_id=%s AND g.project_id=%s AND g.revoked=false AND u.enabled=true LIMIT 1 FOR SHARE OF g,u", (user_id, asset["project_id"]))
            if not auth:
                raise PermissionError("case_write_access_denied")
            task = await self._row(connection, "SELECT status,cancel_requested FROM business.tasks WHERE id=%s", (asset["task_id"],))
            if task is None or task["cancel_requested"] or task["status"] in {"cancelled","denied","rejected","failed"}:
                raise CaseConflict("case_task_not_publishable")
            await self._validate_sources(connection, asset)
            operation = await self._row(connection, "INSERT INTO business.external_operations(id,task_id,project_id,idempotency_key,asset_version,status,asset_id,content_hash,payload,mode) VALUES (%s,%s,%s,%s,%s,'prepared',%s,%s,%s,'local_simulation') RETURNING *", (str(uuid4()), asset["task_id"], asset["project_id"], f"case:{asset_id}:v{version}", version, asset_id, content_hash, Jsonb(asset["body"])))
            assert operation is not None
            await self._event(connection, asset["task_id"], {"kind":"case_publication_prepared", "operation_id":operation["id"], "asset_id":asset_id, "actor_id":user_id})
            return {**operation, "dispatch":True}

    async def operation(self, operation_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            return await self._row(connection, "SELECT * FROM business.external_operations WHERE id=%s", (operation_id,))

    async def mark_in_flight(self, operation_id: str) -> bool:
        async with self.pool.connection() as connection, connection.transaction():
            operation = await self._row(connection, "SELECT * FROM business.external_operations WHERE id=%s FOR UPDATE", (operation_id,))
            if operation is None or operation["status"] != "prepared":
                return False
            asset = await self._row(connection, "SELECT * FROM business.case_assets WHERE id=%s FOR SHARE", (operation["asset_id"],))
            if asset is None or asset["version"] != operation["asset_version"] or asset["content_hash"] != operation["content_hash"] or asset["decision"] != "approved":
                raise CaseConflict("case_approval_expired")
            auth = await self._row(connection, "SELECT g.user_id FROM business.demo_grants g JOIN business.demo_users u USING(user_id) WHERE g.user_id=%s AND g.project_id=%s AND g.revoked=false AND u.enabled=true LIMIT 1 FOR SHARE OF g,u", (asset["user_id"], asset["project_id"]))
            if not auth:
                raise PermissionError("case_write_access_denied")
            task = await self._row(connection, "SELECT status,cancel_requested FROM business.tasks WHERE id=%s FOR SHARE", (asset["task_id"],))
            if task is None or task["cancel_requested"] or task["status"] in {"cancelled","denied","rejected","failed"}:
                raise CaseConflict("case_task_not_publishable")
            await self._validate_sources(connection, asset)
            row = await self._row(connection, "UPDATE business.external_operations SET status='in_flight',updated_at=now() WHERE id=%s AND status='prepared' RETURNING id", (operation_id,))
            return row is not None

    async def complete_operation(self, operation_id: str, external_id: str | None) -> dict[str, Any]:
        async with self.pool.connection() as connection, connection.transaction():
            await self._lock_resource_project(connection, "operation", operation_id)
            row = await self._row(connection, "UPDATE business.external_operations SET status=%s,external_id=%s,error_code=%s,updated_at=now() WHERE id=%s AND status IN ('in_flight','needs_reconciliation') RETURNING *", ("completed" if external_id else "needs_reconciliation", external_id, None if external_id else "external_result_unknown", operation_id))
            if row:
                await self._event(connection, row["task_id"], {"kind":"case_publication_result", "operation_id":operation_id, "status":row["status"], "external_id":external_id})
                await self._asset_graph(connection, row["asset_id"], stage=row["status"], operation=row)
                await self._finalize_task(connection, row["task_id"])
            else:
                row = await self._row(connection, "SELECT * FROM business.external_operations WHERE id=%s", (operation_id,))
            assert row is not None
            return row

    async def demo_create(self, operation: dict[str, Any]) -> str:
        async with self.pool.connection() as connection, connection.transaction():
            current = await self._row(connection, "SELECT * FROM business.external_operations WHERE id=%s FOR UPDATE", (operation["id"],))
            if current is None or current["status"] != "in_flight":
                raise CaseConflict("operation_not_in_flight")
            asset = await self._row(connection, "SELECT * FROM business.case_assets WHERE id=%s FOR SHARE", (current["asset_id"],))
            if asset is None:
                raise CaseConflict("case_asset_missing")
            await self._validate_sources(connection, asset)
            auth = await self._row(connection, "SELECT g.user_id FROM business.demo_grants g JOIN business.demo_users u USING(user_id) WHERE g.user_id=%s AND g.project_id=%s AND g.revoked=false AND u.enabled=true LIMIT 1 FOR SHARE OF g,u", (asset["user_id"], asset["project_id"]))
            if not auth:
                raise PermissionError("case_write_access_denied")
            task = await self._row(connection, "SELECT status,cancel_requested FROM business.tasks WHERE id=%s FOR SHARE", (asset["task_id"],))
            if task is None or task["cancel_requested"] or task["status"] in {"cancelled","denied","failed","rejected"}:
                raise CaseConflict("case_task_not_publishable")
            row = await self._row(connection, "INSERT INTO business.demo_zentao_cases(operation_id,asset_id,project_id,body) VALUES (%s,%s,%s,%s) ON CONFLICT(operation_id) DO UPDATE SET operation_id=EXCLUDED.operation_id RETURNING id", (operation["id"], operation["asset_id"], operation["project_id"], Jsonb(operation["payload"])))
            assert row is not None
            return f"DEMO-{row['id']}"

    async def demo_lookup(self, operation_id: str) -> str | None:
        async with self.pool.connection() as connection:
            row = await self._row(connection, "SELECT id FROM business.demo_zentao_cases WHERE operation_id=%s", (operation_id,))
            return f"DEMO-{row['id']}" if row else None

    async def demo_count(self, asset_id: str) -> int:
        async with self.pool.connection() as connection:
            row = await self._row(connection, "SELECT count(*) AS n FROM business.demo_zentao_cases WHERE asset_id=%s", (asset_id,))
            assert row is not None
            return int(row["n"])
