"""Assemble server-owned retrieval scope from caller permissions and active mappings."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from testmind.core.logging import get_logger
from testmind.core.security import AuthContext
from testmind.models.retrieval import RetrievalScope
from testmind.orchestration.context import RequestContext


class ContextAccessDenied(Exception):
    """The requested project or injected context does not belong to the caller."""


class ContextUnavailable(Exception):
    """Authoritative scope cannot be loaded; callers must not widen or reuse it."""


class ScopeRepository(Protocol):
    async def list_knowledge_bases(
        self, knowledge_base_ids: frozenset[str]
    ) -> list[dict[str, Any]]: ...

    async def build_retrieval_scope(
        self, *, user_id: str, project_id: str, knowledge_base_ids: frozenset[str]
    ) -> RetrievalScope: ...


_LOGGER = get_logger(__name__)


class RequestContextService:
    def __init__(self, repository: ScopeRepository | None, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Scope timeout must be positive")
        self._repository = repository
        self._timeout = timeout_seconds

    async def build(
        self,
        auth: AuthContext,
        *,
        project_id: str | None,
        injected_scope: object | None = None,
    ) -> RequestContext:
        if not auth.is_well_formed():
            raise ContextAccessDenied("Invalid authenticated context")
        hint = self._validate_hint(auth, project_id, injected_scope)
        project = project_id or (hint.project_id if hint else "")
        if project and project not in auth.authorized_project_ids:
            raise ContextAccessDenied("Project access denied")
        if not project:
            return RequestContext()
        # Compatibility for trusted middleware-only deployments. A configured
        # repository always supersedes this hint, including after version withdrawal.
        if self._repository is None and hint is not None:
            return RequestContext(retrieval_scope=hint)
        if not auth.authorized_knowledge_base_ids:
            return RequestContext(
                retrieval_scope=RetrievalScope(user_id=auth.user_id, project_id=project)
            )
        if self._repository is None:
            raise ContextUnavailable("Project knowledge scope is unavailable")
        try:
            scope = await asyncio.wait_for(self._load_scope(auth, project), timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 - prevent storage errors reaching API responses.
            _LOGGER.warning("Retrieval scope assembly failed (%s)", type(exc).__name__)
            raise ContextUnavailable("Project knowledge scope is unavailable") from None
        return RequestContext(retrieval_scope=scope)

    @staticmethod
    def _validate_hint(
        auth: AuthContext, project_id: str | None, injected_scope: object | None
    ) -> RetrievalScope | None:
        if injected_scope is None:
            return None
        if not isinstance(injected_scope, RetrievalScope):
            raise ContextAccessDenied("Invalid server retrieval scope")
        if (
            injected_scope.user_id != auth.user_id
            or injected_scope.project_id not in auth.authorized_project_ids
            or (project_id and project_id != injected_scope.project_id)
            or not injected_scope.knowledge_base_ids.issubset(auth.authorized_knowledge_base_ids)
        ):
            raise ContextAccessDenied("Retrieval scope does not match authenticated access")
        return injected_scope

    async def _load_scope(self, auth: AuthContext, project: str) -> RetrievalScope:
        repository = self._repository
        assert repository is not None
        bases = await repository.list_knowledge_bases(auth.authorized_knowledge_base_ids)
        selected = frozenset(
            base["id"]
            for base in bases
            if base.get("project_id") == project
            and base.get("id") in auth.authorized_knowledge_base_ids
        )
        if not selected:
            return RetrievalScope(user_id=auth.user_id, project_id=project)
        scope = await repository.build_retrieval_scope(
            user_id=auth.user_id, project_id=project, knowledge_base_ids=selected
        )
        if (
            not isinstance(scope, RetrievalScope)
            or scope.user_id != auth.user_id
            or scope.project_id != project
            or scope.knowledge_base_ids != selected
            or scope.document_versions
        ):
            raise ContextUnavailable("Invalid authoritative retrieval scope")
        return scope
