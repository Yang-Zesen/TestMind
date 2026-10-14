"""Fixed read operations over the official ZenTao MCP transport."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, Field, SecretStr


class ZenTaoError(Exception):
    def __init__(self, message: str, status_code: int = 502, *, code: str = "dependency_error"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ZenTaoSettings(BaseModel):
    enabled: bool = False
    mcp_url: str = "http://127.0.0.1:9090/zentao/mcp"
    base_url: str = "http://127.0.0.1/zentao"
    account: str = ""
    password: SecretStr = SecretStr("")
    basic_user: str = ""
    basic_password: SecretStr = SecretStr("")
    timeout: float = Field(default=20, gt=0)

    @classmethod
    def from_env(cls) -> ZenTaoSettings:
        return cls(
            enabled=os.getenv("ZENTAO_MCP_ENABLED", "false").lower() == "true",
            mcp_url=os.getenv("ZENTAO_MCP_URL", "http://127.0.0.1:9090/zentao/mcp"),
            base_url=os.getenv("ZENTAO_BASE_URL", "http://127.0.0.1/zentao"),
            account=os.getenv("ZENTAO_USER_TEST", ""),
            password=SecretStr(os.getenv("ZENTAO_PASSWORD_TEST", "")),
            basic_user=os.getenv("ZENTAO_USER_CACHE", ""),
            basic_password=SecretStr(os.getenv("ZENTAO_PASSWORD_CACHE", "")),
            timeout=float(os.getenv("ZENTAO_MCP_TIMEOUT_SECONDS", "20")),
        )


def parse_result(result: dict[str, Any]) -> dict[str, Any]:
    for item in result.get("content", []):
        text = item.get("text", "")
        if text == "HTTP 401":
            raise ZenTaoError("ZenTao authentication expired", 401, code="authentication_failed")
        if text == "HTTP 403":
            raise ZenTaoError("ZenTao access denied", 403, code="access_denied")
    if result.get("isError"):
        raise ZenTaoError("ZenTao tool failed", code="upstream_rejected")
    for item in result.get("content", []):
        text = item.get("text", "")
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get("status") == "success":
            return value
    raise ZenTaoError("Invalid ZenTao tool response", code="invalid_response")


class Caller(Protocol):
    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class ZenTaoMCPClient:
    def __init__(self, settings: ZenTaoSettings):
        if not settings.account or not settings.password.get_secret_value():
            raise ValueError("ZenTao application credentials are required")
        self.settings = settings
        self._token = ""
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            auth=(settings.basic_user, settings.basic_password.get_secret_value())
            if settings.basic_user
            else None,
            timeout=settings.timeout,
            trust_env=False,
            follow_redirects=False,
        )

    async def get_token(self) -> str:
        async with self._lock:
            if not self._token:
                try:
                    response = await self._http.post(
                        self.settings.base_url.rstrip("/") + "/api.php/v2/users/login",
                        json={
                            "account": self.settings.account,
                            "password": self.settings.password.get_secret_value(),
                        },
                    )
                    response.raise_for_status()
                    token = response.json().get("token")
                    if not isinstance(token, str) or not token:
                        raise ZenTaoError("ZenTao login failed", 503, code="authentication_failed")
                    self._token = token
                except (httpx.HTTPError, ValueError):
                    raise ZenTaoError("ZenTao login failed", 503, code="authentication_failed") from None
            return self._token

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = {"get_projects"} | {entry[i] for entry in RESOURCES.values() for i in (0, 1)}
        if name not in allowed:
            raise ZenTaoError("ZenTao tool not allowed", 403, code="tool_not_allowed")
        for attempt in range(2):
            token = await self.get_token()
            try:
                async with (
                    asyncio.timeout(self.settings.timeout),
                    httpx.AsyncClient(
                        headers={"Token": token},
                        timeout=self.settings.timeout,
                        trust_env=False,
                        follow_redirects=False,
                    ) as http,
                    streamable_http_client(self.settings.mcp_url, http_client=http) as streams,
                    ClientSession(streams[0], streams[1]) as session,
                ):
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
                return parse_result(result.model_dump(by_alias=True))
            except ZenTaoError as error:
                if error.status_code == 401 and attempt == 0:
                    async with self._lock:
                        if self._token == token:
                            self._token = ""
                    continue
                raise
            except (httpx.HTTPError, McpError, TimeoutError, ExceptionGroup):
                raise ZenTaoError("ZenTao MCP unavailable", 503, code="dependency_unavailable") from None
        raise ZenTaoError("ZenTao authentication failed", 503, code="authentication_failed")

    async def aclose(self) -> None:
        self._token = ""
        await self._http.aclose()


RESOURCES = {
    "stories": ("get_projects_projectID_stories", "get_stories_storyID", "storyID", "story"),
    "testcases": ("get_projects_projectID_testcases", "get_testcases_caseID", "caseID", "testcase"),
    "bugs": ("get_projects_projectID_bugs", "get_bugs_bugID", "bugID", "bug"),
}


def record_id(value: Any) -> str:
    text = str(value)
    return text.removeprefix("case_")


class ZenTaoService:
    def __init__(self, caller: Caller):
        self.caller = caller

    async def project(self, project_id: str) -> dict[str, Any]:
        for page in range(1, 101):
            result = await self.caller.call(
                "get_projects", {"browseType": "all", "pageID": page, "recPerPage": 100}
            )
            records = self._records(result, "projects")
            for record in records:
                if str(record.get("id")) == project_id:
                    return record
            if len(records) < 100:
                break
        raise ZenTaoError("Project unavailable", 404, code="not_found")

    @staticmethod
    def _records(result: dict[str, Any], resource: str) -> list[dict[str, Any]]:
        records = result.get(resource, result.get("cases") if resource == "testcases" else None)
        if isinstance(records, dict):
            records = list(records.values())
        if not isinstance(records, list) or any(not isinstance(x, dict) for x in records):
            raise ZenTaoError("Invalid ZenTao collection", code="invalid_response")
        return records

    async def list_records(
        self, project_id: str, resource: str, page: int = 1, limit: int = 100
    ) -> dict[str, Any]:
        name = RESOURCES[resource][0]
        result = await self.caller.call(
            name, {"projectID": int(project_id), "pageID": page, "recPerPage": limit}
        )
        records = self._records(result, resource)
        if any(str(x.get("project", project_id)) != project_id for x in records):
            raise ZenTaoError("ZenTao project scope mismatch", 403, code="scope_mismatch")
        return {"records": records, "pager": result.get("pager", {}), "page": page}

    async def detail(self, project_id: str, resource: str, identifier: str) -> dict[str, Any]:
        found = False
        for page in range(1, 101):
            result = await self.list_records(project_id, resource, page)
            records = result["records"]
            if any(record_id(x.get("id")) == identifier for x in records):
                found = True
                break
            if len(records) < 100:
                break
        if not found:
            raise ZenTaoError("Record not in project", 404, code="not_found")
        _, name, parameter, key = RESOURCES[resource]
        result = await self.caller.call(name, {parameter: int(identifier)})
        record = result.get(key)
        if not isinstance(record, dict) or record_id(record.get("id")) != identifier:
            raise ZenTaoError("Invalid ZenTao detail", code="invalid_response")
        if str(record.get("project", project_id)) != project_id:
            raise ZenTaoError("ZenTao project scope mismatch", 403, code="scope_mismatch")
        return record
