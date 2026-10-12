"""Bounded graph operations with server-derived authorization."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from testmind.core.security import AuthContext, get_auth_context, require_project_access
from testmind.models.graph_sync import GraphEvent

router = APIRouter(prefix="/api/v1", tags=["graph"])
Authorized = Annotated[AuthContext, Depends(get_auth_context)]


class MaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=200)


class PullRequest(MaintenanceRequest):
    resource_type: Literal["project", "member", "requirement", "test_case", "bug"]


@router.post("/integrations/sync-runs", status_code=202)
async def start_pull(payload: PullRequest, request: Request, context: Authorized) -> dict[str, Any]:
    require_project_access(context, payload.project_id)
    await require_maintenance(request, context, payload.project_id)
    if not getattr(request.app.state, "graph_pull_enabled", False):
        raise HTTPException(503, "Snapshot source adapter is not configured")
    try:
        job_id = await repository(request).enqueue_maintenance(
            project_id=payload.project_id, operation="pull", actor_id=context.user_id,
            resource_type=payload.resource_type, authorized_user=context.user_id,
        )
    except PermissionError as exc:
        raise HTTPException(403, "Maintenance permission was revoked") from exc
    return {"job_id": job_id}


@router.post("/graph/reconcile", status_code=202)
@router.post("/graph/rebuild", status_code=202)
async def maintain_graph(
    payload: MaintenanceRequest,
    request: Request, context: Authorized,
) -> dict[str, Any]:
    operation = request.url.path.rsplit("/", 1)[-1]
    require_project_access(context, payload.project_id)
    await require_maintenance(request, context, payload.project_id)
    from psycopg.errors import UniqueViolation
    try:
        job_id = await repository(request).enqueue_maintenance(
            project_id=payload.project_id, operation=operation, actor_id=context.user_id,
            authorized_user=context.user_id,
        )
    except UniqueViolation as exc:
        raise HTTPException(409, "A rebuild is already queued or running") from exc
    except PermissionError as exc:
        raise HTTPException(403, "Maintenance permission was revoked") from exc
    return {"job_id": job_id}


class GraphQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=200)
    query_type: Literal["entities", "relationships"] = "entities"
    entity_type: Literal["project", "member", "requirement", "test_case", "bug", "script", "test_run"] | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=160, pattern=r"^[0-9a-f]{64}(:[0-9a-f]{64}:[a-z_]+)?$")


@router.get("/graph/projects")
async def graph_projects(request: Request, context: Authorized) -> list[dict[str, Any]]:
    projects = []
    for project_id in sorted(context.authorized_project_ids):
        grant = await repository(request).permission(project_id, context.user_id)
        if grant is not None:
            projects.append({"project_id": project_id, **grant})
    return projects


@router.get("/metrics")
async def graph_metrics(project_id: str, request: Request, context: Authorized) -> dict[str, Any]:
    from testmind.infrastructure.graph_sync.metrics import calculate_metrics

    require_project_access(context, project_id)
    repo = repository(request)
    grant = await repo.permission(project_id, context.user_id)
    if grant is None:
        raise HTTPException(403, "Project membership is not authorized")
    owner_id = context.user_id if grant["role"] == "employee" else None
    try:
        events = await repo.authorized_snapshot(project_id, owner_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result = calculate_metrics(events)
    # Aggregate job health is maintenance-only; employees must not see project-wide workload.
    if grant["can_maintain"]:
        result["sync_health"] = await repo.project_health(project_id)
    await repo.audit_query(project_id=project_id, actor_id=context.user_id,
                           query_id="metrics", query_type="metrics")
    if grant != await repo.permission(project_id, context.user_id):
        raise HTTPException(403, "Project permission changed")
    return {"project_id": project_id, "permission_version": grant["permission_version"],
            "snapshot_kind": "current_assets", **result}


def repository(request: Request) -> Any:
    value = getattr(request.app.state, "graph_sync_repository", None)
    if value is None:
        raise HTTPException(503, "Graph repository is not configured")
    return value


async def require_maintenance(request: Request, context: AuthContext, project_id: str) -> None:
    grant = await repository(request).permission(project_id, context.user_id)
    if grant is None or not grant["can_maintain"]:
        raise HTTPException(403, "Graph maintenance permission required")


@router.post("/graph/events", status_code=202)
async def register_graph_event(event: GraphEvent, request: Request, context: Authorized) -> dict[str, Any]:
    require_project_access(context, event.project_id)
    await require_maintenance(request, context, event.project_id)
    if event.source_instance != "testmind":
        raise HTTPException(422, "External sources must use signed integration ingress")
    try:
        result = await repository(request).register_event(event, actor_id=context.user_id,
                                                         authorized_user=context.user_id)
        if result["status"] == "conflict":
            raise HTTPException(409, "Event conflicts with registered state")
        return dict(result)
    except ValueError as exc:
        raise HTTPException(409, "Event conflicts with registered state") from exc
    except PermissionError as exc:
        raise HTTPException(403, "Maintenance permission was revoked") from exc


@router.get("/graph/query")
async def query_graph(
    request: Request, context: Authorized, query: Annotated[GraphQuery, Query()]
) -> dict[str, Any]:
    require_project_access(context, query.project_id)
    service = getattr(request.app.state, "graph_query_service", None)
    if service is None:
        raise HTTPException(503, "Graph query service is not configured")
    try:
        return dict(await service.query(query, context))
    except PermissionError as exc:
        raise HTTPException(403, "Graph access revoked or denied") from exc


@router.get("/integrations/sync-jobs/{job_id}")
async def get_sync_job(job_id: str, request: Request, context: Authorized) -> dict[str, Any]:
    job = await repository(request).get_job(job_id)
    if job is None:
        raise HTTPException(404, "Sync job not found")
    require_project_access(context, job["project_id"])
    await require_maintenance(request, context, job["project_id"])
    return {key: job[key] for key in ("id", "project_id", "status", "attempt_count", "error_code")}
