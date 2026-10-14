"""Request-scoped authorization assembly without external database/model calls."""

import asyncio
from typing import Any

import pytest

from testmind.core.security import AuthContext
from testmind.models.retrieval import ActiveVersionScope, RetrievalScope


class Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.bases = [
            {"id": "kb1", "project_id": "p1"},
            {"id": "kb2", "project_id": "p2"},
            {"id": "unauthorized", "project_id": "p1"},
        ]
        self.versions = {"d1": ActiveVersionScope(document_version="v2", index_version="i3")}
        self.error_at: str | None = None
        self.delay = 0.0
        self.returned_scope: RetrievalScope | None = None

    async def list_knowledge_bases(self, ids: frozenset[str]) -> list[dict[str, Any]]:
        self.calls.append(("bases", ids))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error_at == "bases":
            raise RuntimeError("private-database-response")
        return self.bases

    async def build_retrieval_scope(
        self, *, user_id: str, project_id: str, knowledge_base_ids: frozenset[str]
    ) -> RetrievalScope:
        self.calls.append(("scope", (user_id, project_id, knowledge_base_ids)))
        if self.error_at == "scope":
            raise RuntimeError("private-database-response")
        return self.returned_scope or RetrievalScope(
            user_id=user_id,
            project_id=project_id,
            knowledge_base_ids=knowledge_base_ids,
            active_versions=self.versions,
        )


def identity(**changes: Any) -> AuthContext:
    values = {
        "user_id": "u1",
        "authorized_project_ids": frozenset({"p1", "p2"}),
        "authorized_knowledge_base_ids": frozenset({"kb1", "kb2"}),
    }
    return AuthContext(**(values | changes))


@pytest.mark.asyncio
async def test_scope_uses_only_project_owned_authorized_bases_and_active_versions():
    from testmind.services.request_context import RequestContextService

    repository = Repository()
    result = await RequestContextService(repository).build(identity(), project_id="p1")
    scope = result.retrieval_scope
    assert scope is not None
    assert scope.user_id == "u1" and scope.project_id == "p1"
    assert scope.knowledge_base_ids == frozenset({"kb1"})
    assert scope.active_versions == repository.versions
    assert scope.document_versions == {}
    assert repository.calls == [
        ("bases", frozenset({"kb1", "kb2"})),
        ("scope", ("u1", "p1", frozenset({"kb1"}))),
    ]


@pytest.mark.asyncio
async def test_no_project_keeps_direct_chat_independent_of_database():
    from testmind.services.request_context import RequestContextService

    repository = Repository()
    repository.error_at = "bases"
    result = await RequestContextService(repository).build(identity(), project_id=None)
    assert result.retrieval_scope is None
    assert repository.calls == []


@pytest.mark.asyncio
async def test_project_denied_before_any_repository_call():
    from testmind.services.request_context import ContextAccessDenied, RequestContextService

    repository = Repository()
    with pytest.raises(ContextAccessDenied):
        await RequestContextService(repository).build(identity(), project_id="other")
    assert repository.calls == []


@pytest.mark.asyncio
async def test_no_authorized_bases_produces_empty_scope_without_database_access():
    from testmind.services.request_context import RequestContextService

    repository = Repository()
    result = await RequestContextService(repository).build(
        identity(authorized_knowledge_base_ids=frozenset()), project_id="p1"
    )
    assert result.retrieval_scope is not None
    assert result.retrieval_scope.knowledge_base_ids == frozenset()
    assert not result.retrieval_scope.active_versions
    assert repository.calls == []


@pytest.mark.asyncio
async def test_other_project_bases_do_not_enable_project_wide_retrieval():
    from testmind.services.request_context import RequestContextService

    repository = Repository()
    result = await RequestContextService(repository).build(
        identity(authorized_knowledge_base_ids=frozenset({"kb2"})), project_id="p1"
    )
    assert result.retrieval_scope is not None
    assert not result.retrieval_scope.knowledge_base_ids
    assert not result.retrieval_scope.active_versions
    assert repository.calls == [("bases", frozenset({"kb2"}))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hint",
    [
        {"user_id": "u1", "project_id": "p1"},
        RetrievalScope(user_id="other", project_id="p1"),
        RetrievalScope(user_id="u1", project_id="other"),
        RetrievalScope(user_id="u1", project_id="p2"),
        RetrievalScope(user_id="u1", project_id="p1", knowledge_base_ids=frozenset({"other"})),
    ],
)
async def test_invalid_or_mismatched_server_hint_is_rejected_before_repository(hint):
    from testmind.services.request_context import ContextAccessDenied, RequestContextService

    repository = Repository()
    with pytest.raises(ContextAccessDenied):
        await RequestContextService(repository).build(
            identity(), project_id="p1", injected_scope=hint
        )
    assert repository.calls == []


@pytest.mark.asyncio
async def test_trusted_legacy_hint_is_supported_without_repository():
    from testmind.services.request_context import RequestContextService

    hint = RetrievalScope(user_id="u1", project_id="p1", document_versions={"d1": "v1"})
    result = await RequestContextService(None).build(
        identity(), project_id=None, injected_scope=hint
    )
    assert result.retrieval_scope is hint


@pytest.mark.asyncio
async def test_repository_refresh_supersedes_legacy_hint_even_after_deactivation():
    from testmind.services.request_context import RequestContextService

    repository = Repository()
    repository.versions = {}
    hint = RetrievalScope(user_id="u1", project_id="p1", document_versions={"d1": "v1"})
    result = await RequestContextService(repository).build(
        identity(), project_id=None, injected_scope=hint
    )
    assert result.retrieval_scope is not None
    assert result.retrieval_scope.project_id == "p1"
    assert not result.retrieval_scope.active_versions
    assert not result.retrieval_scope.document_versions


@pytest.mark.asyncio
async def test_missing_repository_is_not_a_silent_empty_result():
    from testmind.services.request_context import ContextUnavailable, RequestContextService

    with pytest.raises(ContextUnavailable):
        await RequestContextService(None).build(identity(), project_id="p1")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["bases", "scope"])
async def test_repository_failure_is_sanitized_and_never_falls_back_to_hint(operation):
    from testmind.services.request_context import ContextUnavailable, RequestContextService

    repository = Repository()
    repository.error_at = operation
    hint = RetrievalScope(user_id="u1", project_id="p1", document_versions={"d1": "v1"})
    with pytest.raises(ContextUnavailable) as raised:
        await RequestContextService(repository).build(
            identity(), project_id="p1", injected_scope=hint
        )
    assert "private-database-response" not in str(raised.value)


@pytest.mark.asyncio
async def test_scope_assembly_has_a_bounded_timeout():
    from testmind.services.request_context import ContextUnavailable, RequestContextService

    repository = Repository()
    repository.delay = 1
    with pytest.raises(ContextUnavailable):
        await RequestContextService(repository, timeout_seconds=0.01).build(
            identity(), project_id="p1"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": "other"},
        {"project_id": "other"},
        {"knowledge_base_ids": frozenset({"kb1", "kb2"})},
        {"document_versions": {"other": "v1"}},
    ],
)
async def test_repository_cannot_return_a_broader_or_legacy_scope(changes):
    from testmind.services.request_context import ContextUnavailable, RequestContextService

    repository = Repository()
    repository.returned_scope = RetrievalScope(
        **(
            {"user_id": "u1", "project_id": "p1", "knowledge_base_ids": frozenset({"kb1"})}
            | changes
        )
    )
    with pytest.raises(ContextUnavailable):
        await RequestContextService(repository).build(identity(), project_id="p1")


@pytest.mark.asyncio
async def test_malformed_auth_context_cannot_be_used_for_direct_chat():
    from testmind.services.request_context import ContextAccessDenied, RequestContextService

    with pytest.raises(ContextAccessDenied):
        await RequestContextService(None).build(identity(user_id=""), project_id=None)
