"""Fail-closed authorization contracts for project-scoped knowledge routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, Request


@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: str
    authorized_project_ids: frozenset[str]
    authorized_knowledge_base_ids: frozenset[str]

    def is_well_formed(self) -> bool:
        return bool(self.user_id.strip()) and all(
            isinstance(value, str) and value.strip()
            for value in (*self.authorized_project_ids, *self.authorized_knowledge_base_ids)
        )


class AuthorizationProvider(Protocol):
    async def get_context(self, request: Request) -> AuthContext | None: ...


class RequestStateAuthorizationProvider:
    """Consumes context attached by trusted authentication middleware only."""

    async def get_context(self, request: Request) -> AuthContext | None:
        context = getattr(request.state, "auth_context", None)
        return context if isinstance(context, AuthContext) and context.is_well_formed() else None


class MockAuthorizationProvider:
    """Development-only fixed principal; permissions never come from request data."""

    def __init__(self, context: AuthContext) -> None:
        if not context.is_well_formed():
            raise ValueError("Mock authorization context must be well formed")
        self._context = context

    async def get_context(self, request: Request) -> AuthContext:
        del request
        return self._context


async def get_auth_context(request: Request) -> AuthContext:
    provider = getattr(request.app.state, "authorization_provider", None)
    if provider is None:
        raise HTTPException(status_code=503, detail="Authorization provider is not configured")
    context = await provider.get_context(request)
    if not isinstance(context, AuthContext) or not context.is_well_formed():
        raise HTTPException(status_code=401, detail="Authentication required")
    return context


def get_authorized_project_ids(context: AuthContext) -> frozenset[str]:
    return context.authorized_project_ids


def require_project_access(context: AuthContext, project_id: str) -> AuthContext:
    if project_id not in context.authorized_project_ids:
        raise HTTPException(status_code=403, detail="Project access denied")
    return context


def require_knowledge_base_access(context: AuthContext, knowledge_base_id: str) -> AuthContext:
    if knowledge_base_id not in context.authorized_knowledge_base_ids:
        raise HTTPException(status_code=403, detail="Knowledge-base access denied")
    return context
