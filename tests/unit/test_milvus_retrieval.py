import json

import pytest

from testmind.core.config import Config
from testmind.infrastructure.milvus.retrieval import MilvusRetrievalService
from testmind.models.retrieval import RetrievalScope


class FakeEncoder:
    def encode_queries(self, sentences):
        return {"dense": [[0.1, 0.2, 0.3]], "sparse": [{5: 0.7, 12: 0.4}]}


class FakeReranker:
    def __init__(self):
        self.pairs = []

    def compute_score(self, pairs, **kwargs):
        assert kwargs["normalize"] is True
        self.pairs = pairs
        return [0.95] * len(pairs)


def hit(ident="c1", **changes):
    entity = {
        "chunk_id": ident,
        "document_id": "d1",
        "document_version": "v2",
        "project_id": "p1",
        "title": "Login",
        "content": "Five failures lock the account.",
        "source_locator": "login.md",
        "page": 1,
        "section": "lockout",
        "is_active": True,
    }
    entity.update(changes)
    return {"id": ident, "entity": entity, "distance": 0.9}


class FakeMilvus:
    def __init__(self, hits=None):
        self.hits = hits if hits is not None else [hit()]
        self.calls = []
        self.closed = False

    def hybrid_search(self, **kwargs):
        self.calls.append(kwargs)
        return [self.hits]

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_hybrid_search_filters_both_channels_and_deduplicates_before_reranking():
    client, ranker = FakeMilvus(), FakeReranker()
    service = MilvusRetrievalService(
        Config(), client=client, encoder=FakeEncoder(), reranker=ranker
    )
    scope = RetrievalScope(user_id="u1", project_id="p1", document_versions={"d1": "v2"})
    chunks = await service.retrieve("login", scope)
    assert len(chunks) == 1 and chunks[0].chunk_id == "c1"
    assert chunks[0].relevance == 0.95
    assert len(client.calls) == 1
    requests = client.calls[0]["reqs"]
    assert {request.anns_field for request in requests} == {"dense_vector", "sparse_vector"}
    assert requests[0].data == [[0.1, 0.2, 0.3]]
    assert requests[1].data == [{5: 0.7, 12: 0.4}]
    for call in client.calls:
        for request in call["reqs"]:
            assert 'project_id == "p1"' in request.expr
            assert 'document_id == "d1"' in request.expr
            assert 'document_version == "v2"' in request.expr
            assert "is_active == true" in request.expr
        assert call["timeout"] > 0
    assert len(ranker.pairs) == 1
    await service.aclose()
    assert client.closed


@pytest.mark.asyncio
async def test_untrusted_search_results_are_discarded_before_model_scoring():
    ranker = FakeReranker()
    client = FakeMilvus(
        [hit(project_id="other"), hit("c2", document_version="old"), hit("c3", is_active=False)]
    )
    service = MilvusRetrievalService(
        Config(), client=client, encoder=FakeEncoder(), reranker=ranker
    )
    scope = RetrievalScope(user_id="u1", project_id="p1", document_versions={"d1": "v2"})
    assert await service.retrieve("login", scope) == []
    assert ranker.pairs == []


@pytest.mark.asyncio
async def test_empty_scope_never_searches():
    client = FakeMilvus()
    service = MilvusRetrievalService(
        Config(), client=client, encoder=FakeEncoder(), reranker=FakeReranker()
    )
    scope = RetrievalScope(user_id="u1", project_id="p1", document_versions={})
    assert await service.retrieve("login", scope) == []
    assert client.calls == []


def test_filter_escapes_ids_and_keeps_document_version_pairs():
    scope = RetrievalScope(
        user_id="u1", project_id='p" or true', document_versions={'d"1': "v2", "d2": "v3"}
    )
    expr = MilvusRetrievalService.build_filter(scope)
    assert f"project_id == {json.dumps(scope.project_id)}" in expr
    assert '(document_id == "d2" and document_version == "v3")' in expr


def test_sparse_conversion_supports_scipy_csr_array():
    from scipy.sparse import csr_array

    matrix = csr_array(([0.7, 0.4], ([0, 0], [5, 12])), shape=(1, 20))
    result = MilvusRetrievalService._sparse_row_to_dict(matrix, 0)
    assert result == {5: 0.7, 12: 0.4}
    assert all(type(key) is int and type(value) is float for key, value in result.items())
