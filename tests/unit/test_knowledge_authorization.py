from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from testmind.core.security import (
    AuthContext,
    get_auth_context,
    require_knowledge_base_access,
    require_project_access,
)


def make_context() -> AuthContext:
    return AuthContext(
        user_id="user-1",
        authorized_project_ids=frozenset({"project-1"}),
        authorized_knowledge_base_ids=frozenset({"kb-1"}),
    )


def test_project_access_requires_server_authorized_project():
    context = make_context()
    assert require_project_access(context, "project-1") is context
    with pytest.raises(HTTPException) as exc:
        require_project_access(context, "project-2")
    assert exc.value.status_code == 403


def test_knowledge_base_access_is_fail_closed():
    context = make_context()
    assert require_knowledge_base_access(context, "kb-1") is context
    with pytest.raises(HTTPException) as exc:
        require_knowledge_base_access(context, "kb-2")
    assert exc.value.status_code == 403


@pytest.mark.parametrize("context", [None, object(), SimpleNamespace(user_id="")])
@pytest.mark.asyncio
async def test_missing_or_malformed_request_context_is_unauthorized(context):
    from testmind.core.security import RequestStateAuthorizationProvider

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(authorization_provider=RequestStateAuthorizationProvider())),
        state=SimpleNamespace(auth_context=context),
    )
    with pytest.raises(HTTPException) as exc:
        await get_auth_context(request)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_missing_authorization_provider_is_service_unavailable():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), state=SimpleNamespace())
    with pytest.raises(HTTPException) as exc:
        await get_auth_context(request)
    assert exc.value.status_code == 503
