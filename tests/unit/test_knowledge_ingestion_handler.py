from io import BytesIO

import pytest

from testmind.infrastructure.knowledge.ingestion import KnowledgeIngestionHandler


class FakeRepository:
    def __init__(self):
        self.quality = None
        self.index = None
        self.status = None

    async def get_version_processing_inputs(self, version_id):
        return {
            "version_id": version_id,
            "document_version": "v1",
            "index_version": "i1",
            "storage_key": "opaque.txt",
            "document_id": "doc1",
            "project_id": "project1",
            "kb_id": "kb1",
            "title": "Doc",
            "metadata": {},
        }

    async def save_quality_report(self, *, version_id, report, status):
        self.quality, self.status = report, status

    async def save_index_report(self, *, version_id, report, accepted):
        self.index = report
        self.status = "ready" if accepted else "index_pending"


class FakeStorage:
    def open(self, key):
        return BytesIO(b"sample text " * 20)


class FakeEmbedder:
    def encode_documents(self, texts):
        return {
            "dense": [[0.1, 0.2] for _ in texts],
            "sparse": [{1: 0.5} for _ in texts],
        }


class FakeIndex:
    def __init__(self, count=None):
        self.records = []
        self.count = count

    def upsert_inactive(self, records):
        self.records = records
        return len(records) if self.count is None else self.count


@pytest.mark.asyncio
async def test_ingestion_persists_quality_then_inactive_index_acceptance():
    repo, index = FakeRepository(), FakeIndex()
    handler = KnowledgeIngestionHandler(
        repo,
        FakeStorage(),
        FakeEmbedder(),
        index,
        model_version="bge-m3-test",
    )

    await handler({"version_id": "version1", "operation": "ingest"})

    assert repo.status == "ready"
    assert repo.quality["quality_status"] == "passed"
    assert repo.index["index_status"] == "accepted"
    assert index.records and all(record["is_active"] is False for record in index.records)
    assert index.records[0]["knowledge_base_id"] == "kb1"
    assert index.records[0]["acl_project_ids"] == ["project1"]
