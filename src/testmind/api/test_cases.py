"""Authorized source, design, per-case review and simulated publication API."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from testmind.api.tasks import Authorized, authorized_task, repository, view
from testmind.core.security import require_project_access
from testmind.infrastructure.postgres.case_repository import CaseConflict, CaseRepository
from testmind.infrastructure.postgres.task_repository import TaskConflict
from testmind.models.test_case import (
    CaseDesignRequest,
    CaseEdit,
    CaseReview,
    CaseSourceInput,
    CaseVersionRequest,
)
from testmind.workflows.test_case_generation import PREFIX

router = APIRouter(prefix="/api/v1", tags=["case-design"])


def case_repository(request: Request) -> CaseRepository:
    repo = getattr(request.app.state, "case_repository", None)
    if not isinstance(repo, CaseRepository):
        raise HTTPException(503, "case_storage_unavailable")
    return repo


async def authorized_asset(asset_id: str, request: Request, auth: Any) -> dict[str, Any]:
    asset = await case_repository(request).asset(asset_id)
    if asset is None or asset["user_id"] != auth.user_id:
        raise HTTPException(404, "case_not_found")
    require_project_access(auth, asset["project_id"])
    return asset


async def authorized_source(source_id: str, request: Request, auth: Any) -> dict[str, Any]:
    source = await case_repository(request).source(source_id)
    if source is None or source["user_id"] != auth.user_id:
        raise HTTPException(404, "source_not_found")
    require_project_access(auth, source["project_id"])
    return source


@router.post("/case-sources", status_code=201)
async def create_source(payload: CaseSourceInput, request: Request, auth: Authorized) -> dict[str, Any]:
    require_project_access(auth, payload.project_id)
    return await case_repository(request).create_source(payload, auth.user_id)


@router.get("/case-sources")
async def sources(project_id: str, request: Request, auth: Authorized) -> list[dict[str, Any]]:
    require_project_access(auth, project_id)
    return await case_repository(request).sources(auth.user_id, project_id)


class SourceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=24000)


@router.patch("/case-sources/{source_id}")
async def edit_source(source_id: str, payload: SourceEdit, request: Request, auth: Authorized) -> dict[str, Any]:
    await authorized_source(source_id, request, auth)
    try:
        return await case_repository(request).update_source(source_id, auth.user_id, **payload.model_dump())
    except ValueError:
        raise HTTPException(409, "source_version_or_content_invalid") from None


@router.delete("/case-sources/{source_id}")
async def delete_source(source_id: str, request: Request, auth: Authorized) -> dict[str, Any]:
    source = await authorized_source(source_id, request, auth)
    async with case_repository(request).pool.connection() as connection:
        await connection.execute("UPDATE business.case_sources SET deleted=true,version=version+1,updated_at=now() WHERE id=%s AND deleted=false", (source_id,))
    return {"id":source["id"], "status":"deleted"}


@router.post("/case-design", status_code=202)
async def design(payload: CaseDesignRequest, request: Request, auth: Authorized, key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]) -> dict[str, Any]:
    require_project_access(auth, payload.project_id)
    for source_id in payload.source_ids:
        source = await authorized_source(source_id, request, auth)
        if source["project_id"] != payload.project_id or source["deleted"]:
            raise HTTPException(409, "source_unavailable")
    try:
        task = await repository(request).submit(user_id=auth.user_id, project_id=payload.project_id, session_id=payload.session_id, query=PREFIX + json.dumps({"source_ids":payload.source_ids,"use_knowledge":payload.use_knowledge}), idempotency_key=key)
    except TaskConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return view(task)


@router.get("/tasks/{task_id}/cases")
async def cases(task_id: str, request: Request, auth: Authorized) -> list[dict[str, Any]]:
    await authorized_task(request, task_id, auth)
    result = await case_repository(request).assets(task_id)
    for asset in result:
        asset["source_stale"] = False
        for snapshot in asset["sources"]:
            current = await case_repository(request).source(snapshot["id"])
            if current is None or current["deleted"] or current["version"] != snapshot["version"]:
                asset["source_stale"] = True
        async with case_repository(request).pool.connection() as connection:
            try:
                await case_repository(request)._validate_sources(connection, asset)
            except CaseConflict:
                asset["source_stale"] = True
            cursor = await connection.execute("SELECT id,status,external_id,mode,error_code FROM business.external_operations WHERE asset_id=%s ORDER BY updated_at DESC", (asset["id"],))
            asset["publications"] = [dict(row) for row in await cursor.fetchall()]
    return result


@router.patch("/test-cases/{asset_id}")
async def edit_case(asset_id: str, payload: CaseEdit, request: Request, auth: Authorized) -> dict[str, Any]:
    await authorized_asset(asset_id, request, auth)
    try:
        return await case_repository(request).edit(asset_id, auth.user_id, version=payload.version, body=payload.body)
    except CaseConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/test-cases/{asset_id}/review")
async def review_case(asset_id: str, payload: CaseReview, request: Request, auth: Authorized) -> dict[str, Any]:
    await authorized_asset(asset_id, request, auth)
    try:
        return await case_repository(request).decide(asset_id, auth.user_id, **payload.model_dump())
    except CaseConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/test-cases/{asset_id}/publish")
async def publish_case(asset_id: str, payload: CaseVersionRequest, request: Request, auth: Authorized) -> dict[str, Any]:
    await authorized_asset(asset_id, request, auth)
    service = getattr(request.app.state, "case_publication", None)
    if service is None:
        raise HTTPException(503, "case_publication_not_enabled")
    try:
        result = await service.publish(asset_id, auth.user_id, **payload.model_dump())
    except CaseConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except PermissionError:
        raise HTTPException(403, "case_write_access_denied") from None
    return {key:result[key] for key in ("id","asset_id","asset_version","status","external_id","mode","error_code")}


@router.post("/case-publications/{operation_id}/reconcile")
async def reconcile(operation_id: str, request: Request, auth: Authorized) -> dict[str, Any]:
    operation = await case_repository(request).operation(operation_id)
    if operation is None or not operation.get("asset_id"):
        raise HTTPException(404, "operation_not_found")
    await authorized_asset(operation["asset_id"], request, auth)
    service = getattr(request.app.state, "case_publication", None)
    if service is None:
        raise HTTPException(503, "case_publication_not_enabled")
    result = await service.reconcile(operation_id, auth.user_id)
    return {key:result[key] for key in ("id","status","external_id","mode","error_code")}
