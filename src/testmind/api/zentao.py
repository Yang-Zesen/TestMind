"""Authorized project-scoped ZenTao reads through MCP."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from testmind.core.security import AuthContext, get_auth_context, require_project_access
from testmind.infrastructure.zentao.mcp import ZenTaoError, ZenTaoService

router = APIRouter(prefix="/zentao", tags=["zentao"])
Resource = Literal["stories", "testcases", "bugs"]
Identity = Annotated[AuthContext, Depends(get_auth_context)]


def service(request: Request, context: AuthContext, project_id: str) -> ZenTaoService:
    require_project_access(context, project_id)
    instance = getattr(request.app.state, "zentao_service", None)
    if not isinstance(instance, ZenTaoService):
        raise HTTPException(503, "ZenTao MCP is not configured")
    return instance


@router.get("/projects/{project_id}")
async def project(
    request: Request, context: Identity, project_id: str = Path(pattern=r"^[1-9][0-9]*$")
) -> dict[str, Any]:
    upstream = service(request, context, project_id)
    try:
        return {"data": await upstream.project(project_id)}
    except ZenTaoError as error:
        raise HTTPException(error.status_code, detail={"code": error.code, "message": str(error)}) from None


@router.get("/projects/{project_id}/{resource}")
async def records(
    request: Request,
    resource: Resource,
    context: Identity,
    project_id: str = Path(pattern=r"^[1-9][0-9]*$"),
    page: int = Query(default=1, ge=1, le=100),
    limit: int = Query(default=100, ge=1, le=100),
) -> dict[str, Any]:
    upstream = service(request, context, project_id)
    try:
        return await upstream.list_records(project_id, resource, page, limit)
    except ZenTaoError as error:
        raise HTTPException(error.status_code, detail={"code": error.code, "message": str(error)}) from None


@router.get("/projects/{project_id}/{resource}/{identifier}")
async def detail(
    request: Request,
    resource: Resource,
    context: Identity,
    project_id: str = Path(pattern=r"^[1-9][0-9]*$"),
    identifier: str = Path(pattern=r"^[1-9][0-9]*$"),
) -> dict[str, Any]:
    upstream = service(request, context, project_id)
    try:
        return {"data": await upstream.detail(project_id, resource, identifier)}
    except ZenTaoError as error:
        raise HTTPException(error.status_code, detail={"code": error.code, "message": str(error)}) from None
