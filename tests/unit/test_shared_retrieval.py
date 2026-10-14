import asyncio

import pytest

from testmind.models.retrieval import ActiveVersionScope, KnowledgeChunk, RetrievalScope


def scope(user="u", version="1"):
    return RetrievalScope(user_id=user, project_id="p", knowledge_base_ids=frozenset({"kb"}), active_versions={"d": ActiveVersionScope(document_version=version, index_version="i")})


class Backend:
    async def retrieve(self, query, selected, *, limit=5):
        await asyncio.sleep(0)
        return [KnowledgeChunk(chunk_id=query, document_id="d", document_version="1", index_version="i", knowledge_base_id="kb", project_id="p", content="confirmed evidence", source_locator="section:1", relevance=.9)]


async def test_shared_retrieval_rechecks_versions_after_backend_returns():
    from testmind.workflows.retrieval import SharedRetrievalService

    async def refresh(selected):
        return scope(selected.user_id, "2")

    service = SharedRetrievalService(Backend(), refresh_scope=refresh)
    bundle = await service.evidence("query", scope(), caller_id="task-a")
    assert bundle.chunks == []
    assert bundle.status == "insufficient_evidence"


async def test_parallel_callers_have_independent_evidence_and_no_answer():
    from testmind.workflows.retrieval import SharedRetrievalService

    service = SharedRetrievalService(Backend())
    first, second = await asyncio.gather(service.evidence("a", scope("u1"), caller_id="task-a"), service.evidence("b", scope("u2"), caller_id="task-b"))
    assert first.caller_id == "task-a" and second.caller_id == "task-b"
    assert first.chunks[0].chunk_id == "a" and second.chunks[0].chunk_id == "b"
    assert "answer" not in first.model_dump()


async def test_revocation_is_denied_instead_of_empty_evidence():
    from testmind.workflows.retrieval import SharedRetrievalService

    async def refresh(selected):
        raise PermissionError("revoked")

    with pytest.raises(PermissionError):
        await SharedRetrievalService(Backend(), refresh_scope=refresh).evidence("query", scope(), caller_id="task-a")
