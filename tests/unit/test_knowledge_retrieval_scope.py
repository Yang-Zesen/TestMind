from testmind.infrastructure.milvus.retrieval import MilvusRetrievalService
from testmind.models.retrieval import ActiveVersionScope, RetrievalScope


def test_filter_and_hit_validation_use_exact_server_active_index_scope():
    scope = RetrievalScope(
        user_id="u1",
        project_id="p1",
        knowledge_base_ids=frozenset({"kb1"}),
        active_versions={"d1": ActiveVersionScope(document_version="v2", index_version="i3")},
    )
    expression = MilvusRetrievalService.build_filter(scope)
    assert 'knowledge_base_id in ["kb1"]' in expression
    assert 'document_version == "v2"' in expression
    assert 'index_version == "i3"' in expression
    assert "is_active == true" in expression

    base = {
        "chunk_id": "c1",
        "document_id": "d1",
        "document_version": "v2",
        "index_version": "i3",
        "project_id": "p1",
        "knowledge_base_id": "kb1",
        "content": "active text",
        "source_locator": "page:1",
        "is_active": True,
    }
    assert len(MilvusRetrievalService._validated_candidates([{"entity": base}], scope)) == 1
    wrong_index = {**base, "index_version": "i2"}
    wrong_base = {**base, "knowledge_base_id": "other"}
    assert not MilvusRetrievalService._validated_candidates([{"entity": wrong_index}], scope)
    assert not MilvusRetrievalService._validated_candidates([{"entity": wrong_base}], scope)
