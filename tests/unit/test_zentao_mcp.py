import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from testmind.api.zentao import router
from testmind.core.security import AuthContext, MockAuthorizationProvider
from testmind.infrastructure.zentao.mcp import (
    ZenTaoError,
    ZenTaoMCPClient,
    ZenTaoService,
    ZenTaoSettings,
    parse_result,
)
from testmind.infrastructure.zentao.tools import create_zentao_tools


class Caller:
    def __init__(self):
        self.calls = []

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "get_projects":
            return {"status": "success", "projects": [{"id": "1", "name": "sandbox"}]}
        if name == "get_projects_projectID_testcases":
            return {"status": "success", "testcases": [{"id": "case_1", "project": "1"}]}
        return {"status": "success", "testcase": {"id": "1", "project": 1}}


@pytest.mark.asyncio
async def test_detail_checks_project_membership_before_loading_record():
    caller = Caller()
    service = ZenTaoService(caller)
    result = await service.detail("1", "testcases", "1")
    assert result["id"] == "1"
    assert [name for name, _ in caller.calls] == [
        "get_projects_projectID_testcases",
        "get_testcases_caseID",
    ]
    with pytest.raises(ZenTaoError, match="not in project"):
        await service.detail("1", "testcases", "99")
    assert caller.calls[-1][0] == "get_projects_projectID_testcases"


def test_result_parser_requires_success_and_hides_upstream_errors():
    assert (
        parse_result(
            {
                "content": [
                    {"type": "text", "text": "HTTP 200"},
                    {"type": "text", "text": json.dumps({"status": "success", "bugs": []})},
                ]
            }
        )["bugs"]
        == []
    )
    with pytest.raises(ZenTaoError):
        parse_result({"isError": True, "content": [{"text": "secret-token"}]})
    with pytest.raises(ZenTaoError):
        parse_result({"content": [{"text": '{"status":"fail"}'}]})


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"isError": True, "content": [{"text": "HTTP 401"}]}, "authentication_failed"),
        ({"isError": True, "content": [{"text": "HTTP 403"}]}, "access_denied"),
        ({"isError": True, "content": [{"text": "secret detail"}]}, "upstream_rejected"),
    ],
)
def test_protocol_errors_have_stable_codes_and_hide_upstream_details(payload, code):
    with pytest.raises(ZenTaoError) as raised:
        parse_result(payload)
    assert raised.value.code == code
    assert "secret detail" not in str(raised.value)


def test_api_denies_unauthorized_projects_without_upstream_call():
    app = FastAPI()
    app.include_router(router)
    app.state.authorization_provider = MockAuthorizationProvider(
        AuthContext("user", frozenset({"1"}), frozenset())
    )
    caller = Caller()
    app.state.zentao_service = ZenTaoService(caller)
    with TestClient(app) as client:
        assert client.get("/zentao/projects/2/testcases").status_code == 403
        assert caller.calls == []
        assert client.get("/zentao/projects/1/testcases/1").json()["data"]["id"] == "1"
        assert client.get("/zentao/projects/1/testcases/99").status_code == 404
        error = client.get("/zentao/projects/1/testcases/99").json()["detail"]
        assert error == {"code": "not_found", "message": "Record not in project"}


def test_api_disabled_is_explicit():
    app = FastAPI()
    app.include_router(router)
    app.state.authorization_provider = MockAuthorizationProvider(
        AuthContext("user", frozenset({"1"}), frozenset())
    )
    with TestClient(app) as client:
        assert client.get("/zentao/projects/1").status_code == 503


@pytest.mark.asyncio
async def test_token_cached_and_never_retries_bad_credentials():
    calls = []

    def login(request):
        calls.append(request)
        return httpx.Response(201, json={"token": "test-token"})

    client = ZenTaoMCPClient(ZenTaoSettings(account="TE1", password="local-secret"))
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(login))
    try:
        assert await client.get_token() == "test-token"
        assert await client.get_token() == "test-token"
        assert len(calls) == 1
        client._token = ""

        def denied(request):
            calls.append(request)
            return httpx.Response(401, json={"error": "secret-token"})

        await client._http.aclose()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(denied))
        with pytest.raises(ZenTaoError, match="login failed") as error:
            await client.get_token()
        assert "secret-token" not in str(error.value)
        assert len(calls) == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_bound_tools_reject_model_supplied_project():
    caller = Caller()
    tools = create_zentao_tools(
        ZenTaoService(caller), AuthContext("user", frozenset({"1"}), frozenset())
    )
    with pytest.raises(Exception) as error:
        await tools[2].ainvoke({"project_id": "2", "resource": "testcases", "identifier": "1"})
    assert error.value.status_code == 403
    assert caller.calls == []
    result = await tools[2].ainvoke({"project_id": "1", "resource": "testcases", "identifier": "1"})
    assert result["id"] == "1"


@pytest.mark.asyncio
async def test_refresh_once_on_token_expiry_and_no_refresh_on_forbidden(monkeypatch):
    import testmind.infrastructure.zentao.mcp as module

    replies = []
    logins = []

    def login(request):
        logins.append(request)
        return httpx.Response(200, json={"token": f"token-{len(logins)}"})

    @asynccontextmanager
    async def transport(*args, **kwargs):
        yield (None, None, None)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def initialize(self):
            pass

        async def call_tool(self, name, arguments):
            payload = replies.pop(0)
            return SimpleNamespace(model_dump=lambda **_: payload)

    monkeypatch.setattr(module, "streamable_http_client", transport)
    monkeypatch.setattr(module, "ClientSession", lambda *args: Session())
    client = ZenTaoMCPClient(ZenTaoSettings(account="TE1", password="local-secret"))
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(login))

    def denied(code):
        return {"isError": True, "content": [{"text": f"HTTP {code}"}]}

    success = {"content": [{"text": '{"status":"success","projects":[]}'}]}
    try:
        replies.extend([denied(401), success])
        assert (await client.call("get_projects", {}))["projects"] == []
        assert len(logins) == 2
        replies.append(denied(403))
        with pytest.raises(ZenTaoError) as error:
            await client.call("get_projects", {})
        assert error.value.status_code == 403
        assert len(logins) == 2
        replies.extend([denied(401), denied(401)])
        with pytest.raises(ZenTaoError):
            await client.call("get_projects", {})
        assert len(logins) == 3
        with pytest.raises(ZenTaoError) as error:
            await client.call("post_testcases", {})
        assert error.value.status_code == 403
    finally:
        await client.aclose()
