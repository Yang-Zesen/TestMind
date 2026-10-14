"""PostgreSQL scope and real Milvus version activation; no model inference."""

import os
from uuid import uuid4

import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pymilvus import MilvusClient

from testmind.infrastructure.knowledge.milvus_index import MilvusKnowledgeIndex
from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository


@pytest.mark.integration
async def test_knowledge_scope_and_milvus_activation():
    dsn, uri = os.getenv("KNOWLEDGE_TEST_DSN"), os.getenv("KNOWLEDGE_TEST_MILVUS_URI")
    if not dsn or not uri:
        pytest.skip("KNOWLEDGE_TEST_DSN and KNOWLEDGE_TEST_MILVUS_URI are required")
    name = os.environ["KNOWLEDGE_TEST_COLLECTION"]
    chunk_id = "integration-" + str(uuid4())
    client = MilvusClient(uri)
    index = MilvusKnowledgeIndex(client, collection_name=name)
    record = {
        "chunk_id": chunk_id, "project_id": "demo-project", "knowledge_base_id": "demo-kb",
        "document_id": chunk_id, "document_version": "1", "index_version": "1",
        "content": "Assertions compare actual and expected results.",
        "dense_vector": [1.0] + [0.0] * 1023, "sparse_vector": {0: 1.0},
    }
    try:
        assert index.upsert_inactive([record]) == 1
        assert not client.query(name, filter=f'chunk_id == "{chunk_id}" and is_active == true')
        assert index.set_version_active(project_id="demo-project", kb_id="demo-kb", document_id=chunk_id, document_version="1", index_version="1", active=True) == 1
        assert not client.query(name, filter=f'chunk_id == "{chunk_id}" and project_id == "other-project"')
        assert index.set_version_active(project_id="demo-project", kb_id="demo-kb", document_id=chunk_id, document_version="1", index_version="1", active=False) == 1
        async with AsyncConnectionPool(dsn, open=False, kwargs={"row_factory": dict_row}) as pool:
            await pool.open()
            repository = KnowledgeRepository(pool)
            bases = await repository.list_knowledge_bases(frozenset({"demo-kb"}))
            assert bases[0]["project_id"] == "demo-project"
            scope = await repository.build_retrieval_scope(user_id="demo-user", project_id="demo-project", knowledge_base_ids=frozenset({"demo-kb"}))
            assert scope.user_id == "demo-user" and scope.project_id == "demo-project"
    finally:
        client.delete(name, ids=[chunk_id])
        client.close()
