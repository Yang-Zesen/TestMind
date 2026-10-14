"""Bind ZenTao tools to a trusted request context, never model-supplied rights."""

import re
from typing import Any

from langchain_core.tools import BaseTool, tool

from testmind.core.security import AuthContext, require_project_access
from testmind.infrastructure.zentao.mcp import RESOURCES, ZenTaoService


def create_zentao_tools(service: ZenTaoService, context: AuthContext) -> list[BaseTool]:
    def authorize(project_id: str) -> None:
        if not re.fullmatch(r"[1-9][0-9]*", project_id):
            raise ValueError("Invalid project ID")
        require_project_access(context, project_id)

    @tool
    async def zentao_project(project_id: str) -> dict[str, Any]:
        """Read an authorized ZenTao project by its numeric ID."""
        authorize(project_id)
        return await service.project(project_id)

    @tool
    async def zentao_list(project_id: str, resource: str, page: int = 1) -> dict[str, Any]:
        """List stories, testcases or bugs in an authorized project."""
        authorize(project_id)
        if resource not in RESOURCES or not 1 <= page <= 100:
            raise ValueError("Invalid resource or page")
        return await service.list_records(project_id, resource, page)

    @tool
    async def zentao_detail(project_id: str, resource: str, identifier: str) -> dict[str, Any]:
        """Read a story, testcase or bug after verifying project membership."""
        authorize(project_id)
        if resource not in RESOURCES or not re.fullmatch(r"[1-9][0-9]*", identifier):
            raise ValueError("Invalid resource or record ID")
        return await service.detail(project_id, resource, identifier)

    return [zentao_project, zentao_list, zentao_detail]
