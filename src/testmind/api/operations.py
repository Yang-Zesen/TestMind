"""Project maintainer diagnostics; never returns task bodies or credentials."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from testmind.api.tasks import Authorized, repository
from testmind.core.api_metrics import ApiMetricsMiddleware
from testmind.core.security import require_project_access
from testmind.services.runtime_status import runtime_snapshot

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


@router.get("")
async def status(project_id: str, request: Request, auth: Authorized) -> dict[str, Any]:
    require_project_access(auth, project_id)
    graph = getattr(request.app.state, "graph_sync_repository", None)
    grant = await graph.permission(project_id, auth.user_id) if graph else None
    if grant is None or not grant["can_maintain"]:
        raise HTTPException(403, "maintenance_access_denied")
    assert graph is not None
    async with asyncio.timeout(5):
        snapshot = await runtime_snapshot(repository(request).pool, project_id)
    latest = await graph.permission(project_id, auth.user_id)
    if latest is None or not latest["can_maintain"] or latest["permission_version"] != grant["permission_version"]:
        raise HTTPException(403, "maintenance_access_changed")
    return {**snapshot, "api": ApiMetricsMiddleware.snapshot()}
