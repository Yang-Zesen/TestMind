from testmind.infrastructure.knowledge.milvus_index import MilvusKnowledgeIndex


class FakeMilvus:
    def __init__(self):
        self.rows = {}
        self.upsert_calls = []
        self.query_calls = []

    def upsert(self, *, collection_name, data):
        self.upsert_calls.append(data)
        for row in data:
            self.rows[row["chunk_id"]] = dict(row)

    def flush(self, *, collection_name):
        return None

    def query(self, *, collection_name, filter, output_fields, consistency_level, timeout=None):
        self.query_calls.append(consistency_level)
        values = list(self.rows.values())
        return [row for row in values if row["document_id"] == "doc-1"]


def test_milvus_upsert_starts_inactive_and_version_activation_is_idempotent():
    client = FakeMilvus()
    index = MilvusKnowledgeIndex(client, collection_name="knowledge", timeout_seconds=3)
    records = [
        {
            "chunk_id": "stable-1",
            "project_id": "project-1",
            "knowledge_base_id": "kb-1",
            "document_id": "doc-1",
            "document_version": "v1",
            "index_version": "i1",
            "content": "text",
            "source_locator": "page:1",
            "is_active": True,
            "dense_vector": [0.1],
            "sparse_vector": {1: 0.2},
        }
    ]

    assert index.upsert_inactive(records) == 1
    assert client.rows["stable-1"]["is_active"] is False
    assert index.set_version_active(
        project_id="project-1", kb_id="kb-1", document_id="doc-1",
        document_version="v1", index_version="i1", active=True,
    ) == 1
    assert client.rows["stable-1"]["is_active"] is True
    assert set(client.query_calls) == {"Strong"}
    assert len(client.upsert_calls) == 2
