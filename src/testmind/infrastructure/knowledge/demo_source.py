"""Persistent simulated ZenTao source polling feeds the existing durable queue."""

from io import BytesIO
from typing import Any

from testmind.infrastructure.knowledge.ingestion import KnowledgeIngestionHandler
from testmind.infrastructure.knowledge.source_events import NormalizedSourceEvent
from testmind.infrastructure.knowledge.source_storage import SourceStorage
from testmind.infrastructure.postgres.case_repository import CaseRepository
from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
from testmind.worker.knowledge_worker import PermanentJobError


class DemoSourcePoller:
    def __init__(self, cases: CaseRepository, knowledge: KnowledgeRepository):
        self.cases, self.knowledge = cases, knowledge

    async def poll(self) -> int:
        async with self.cases.pool.connection() as connection:
            cursor = await connection.execute("""SELECT s.*, g.kb_id FROM business.case_sources s
              JOIN business.demo_grants g ON g.user_id=s.user_id AND g.project_id=s.project_id AND g.revoked=false
              JOIN business.demo_users u ON u.user_id=s.user_id AND u.enabled=true
              LEFT JOIN business.source_watermarks w ON w.source_id=s.id
              WHERE COALESCE(w.version,0)<s.version ORDER BY s.updated_at,s.id LIMIT 20""")
            rows = await cursor.fetchall()
        for source in rows:
            event = NormalizedSourceEvent(source="demo-zentao", source_event_id=f"{source['id']}:{source['version']}", project_ref=source["project_id"], kb_ref=source["kb_id"], resource_type="requirement" if source["kind"] == "requirement" else "api_document", resource_id=source["id"], operation="delete" if source["deleted"] else "upsert", source_version=str(source["version"]), content_hash=source["content_hash"], snapshot={"title":source["title"], "content":source["content"], "source_id":source["id"], "version":source["version"]})
            await self.knowledge.register_source_event(event, actor_id=source["user_id"], correlation_id=event.source_event_id)
            async with self.cases.pool.connection() as connection:
                await connection.execute("INSERT INTO business.source_watermarks(source_id,version) VALUES (%s,%s) ON CONFLICT(source_id) DO UPDATE SET version=greatest(business.source_watermarks.version,EXCLUDED.version)", (source["id"], source["version"]))
        return len(rows)


class SourceSyncHandler:
    def __init__(self, repository: KnowledgeRepository, storage: SourceStorage, ingestion: KnowledgeIngestionHandler):
        self.repository, self.storage, self.ingestion = repository, storage, ingestion

    async def __call__(self, job: Any) -> None:
        async with self.repository._pool.connection() as connection:
            receipt = await self.repository._fetchone(connection, "SELECT * FROM knowledge.change_receipts WHERE job_id=%s", (job["id"],))
            if receipt is None:
                raise PermanentJobError("source_receipt_missing")
            snapshot = receipt["payload"]
            current = await self.repository._fetchone(connection, "SELECT version,deleted FROM business.case_sources WHERE id=%s", (snapshot["source_id"],))
            if current is None or current["version"] != snapshot["version"]:
                await connection.execute("UPDATE knowledge.document_versions SET status='rejected' WHERE id=%s", (job["version_id"],))
                return
        if receipt["operation"] == "delete":
            await self.repository.tombstone_document(project_id=job["project_id"], kb_id=job["kb_id"], document_id=job["document_id"])
            return
        content = f"# {snapshot['title']}\n\n{snapshot['content']}".encode()
        stored = self.storage.save(BytesIO(content), suffix=".md")
        async with self.repository._pool.connection() as connection:
            await connection.execute("UPDATE knowledge.document_versions SET storage_key=%s WHERE id=%s", (stored.key, job["version_id"]))
        await self.ingestion(job)
