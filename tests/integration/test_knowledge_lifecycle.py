import os
from io import BytesIO
from uuid import uuid4

import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pymilvus import MilvusClient

from testmind.infrastructure.knowledge.activation import KnowledgeActivationService
from testmind.infrastructure.knowledge.ingestion import KnowledgeIngestionHandler
from testmind.infrastructure.knowledge.milvus_index import MilvusKnowledgeIndex
from testmind.infrastructure.knowledge.source_storage import FilesystemSourceStorage
from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository

pytestmark = pytest.mark.integration


class FixedEncoder:
    def encode_documents(self, texts):
        return {"dense":[[1.] + [0.] * 1023 for _ in texts], "sparse":[{1:1.} for _ in texts]}


async def test_replacement_reindex_replay_delete_with_actual_postgres_milvus(tmp_path):
    if not os.getenv("KNOWLEDGE_TEST_DSN") or not os.getenv("KNOWLEDGE_TEST_MILVUS_URI"):
        pytest.skip("Dedicated PG/Milvus required")
    client = MilvusClient(os.environ["KNOWLEDGE_TEST_MILVUS_URI"])
    index = MilvusKnowledgeIndex(client, collection_name=os.environ["KNOWLEDGE_TEST_COLLECTION"])
    storage = FilesystemSourceStorage(tmp_path)
    async with AsyncConnectionPool(os.environ["KNOWLEDGE_TEST_DSN"], open=False, kwargs={"row_factory":dict_row,"autocommit":True}) as pool:
        await pool.open()
        repo = KnowledgeRepository(pool)
        ingestion = KnowledgeIngestionHandler(repo, storage, FixedEncoder(), index, model_version="integration-fixed")
        activation = KnowledgeActivationService(repo, index)
        key = storage.save(BytesIO(b"First document version and valid content."), suffix=".txt").key
        first = await repo.create_upload(project_id="demo-project", kb_id="demo-kb", idempotency_key=str(uuid4()), request_fingerprint=str(uuid4()), storage_key=key, title="Lifecycle test", metadata={}, actor_id="demo-user", correlation_id=str(uuid4()))
        async def scope():
            return await repo.build_retrieval_scope(user_id="demo-user",project_id="demo-project",knowledge_base_ids=frozenset({"demo-kb"}))
        async def ingest_activate(upload):
            await ingestion(await repo.get_job(upload["job_id"]))
            operation = await repo.begin_activation(project_id="demo-project",kb_id="demo-kb",document_id=first["document_id"],version_id=upload["version_id"])
            await activation.activate(operation)
            await activation.activate(operation)
        await ingest_activate(first)
        initial = (await scope()).active_versions[first["document_id"]]
        # A lease reclaim of an already indexed active version must not hide vectors.
        await ingestion(await repo.get_job(first["job_id"]))
        assert index.set_version_active(project_id="demo-project",kb_id="demo-kb",document_id=first["document_id"],document_version=initial.document_version,index_version=initial.index_version,active=True) > 0
        key = storage.save(BytesIO(b"Replacement document with new confirmed rules."), suffix=".txt").key
        replacement = await repo.create_replacement(project_id="demo-project",kb_id="demo-kb",document_id=first["document_id"],idempotency_key=str(uuid4()),request_fingerprint=str(uuid4()),storage_key=key,actor_id="demo-user",correlation_id=str(uuid4()))
        await ingestion(await repo.get_job(replacement["job_id"]))
        assert (await scope()).active_versions[first["document_id"]] == initial
        await ingest_activate(replacement)
        updated = (await scope()).active_versions[first["document_id"]]
        assert updated != initial
        reindexed = await repo.enqueue_reindex(project_id="demo-project",kb_id="demo-kb",document_id=first["document_id"],actor_id="demo-user",correlation_id=str(uuid4()))
        await ingest_activate(reindexed)
        rebuilt = (await scope()).active_versions[first["document_id"]]
        assert rebuilt.document_version == updated.document_version and rebuilt.index_version != updated.index_version
        versions = await repo.list_versions(document_id=first["document_id"])
        assert sum(version["status"] == "active" for version in versions) == 1
        await repo.tombstone_document(project_id="demo-project",kb_id="demo-kb",document_id=first["document_id"])
        assert first["document_id"] not in (await scope()).active_versions
        from testmind.infrastructure.knowledge.demo_source import (
            DemoSourcePoller,
            SourceSyncHandler,
        )
        from testmind.infrastructure.postgres.case_repository import CaseRepository
        from testmind.models.test_case import CaseSourceInput
        cases = CaseRepository(pool)
        source = await cases.create_source(CaseSourceInput(project_id="demo-project",kind="requirement",title="Automatic source acceptance",content="Source rules for automatic knowledge ingestion."),"demo-user")
        poller = DemoSourcePoller(cases,repo)
        for _ in range(10):
            if not await poller.poll():
                break
        async def source_job(version):
            async with pool.connection() as connection:
                cursor = await connection.execute("SELECT j.* FROM knowledge.jobs j JOIN knowledge.change_receipts r ON r.job_id=j.id WHERE r.source='demo-zentao' AND r.source_event_id=%s",(f"{source['id']}:{version}",))
                return dict(await cursor.fetchone())
        sync = SourceSyncHandler(repo,storage,ingestion)
        automatic = await source_job(1)
        await sync(automatic)
        operation = await repo.begin_activation(project_id="demo-project",kb_id="demo-kb",document_id=automatic["document_id"],version_id=automatic["version_id"])
        await activation.activate(operation)
        assert automatic["document_id"] in (await scope()).active_versions
        async with pool.connection() as connection:
            await connection.execute("UPDATE business.case_sources SET deleted=true,version=2 WHERE id=%s",(source["id"],))
        await poller.poll()
        await sync(await source_job(2))
        assert automatic["document_id"] not in (await scope()).active_versions
    client.delete(os.environ["KNOWLEDGE_TEST_COLLECTION"], filter=f'document_id == "{first["document_id"]}"')
    client.close()
