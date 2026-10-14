"""Durable submission and subscription; HTTP requests never execute the task."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from testmind.core.security import AuthContext, get_auth_context, require_project_access
from testmind.infrastructure.postgres.task_repository import (
    TaskConflict,
    TaskRepository,
    TaskTransitionError,
)
from testmind.models.task import TERMINAL, TaskResume, TaskSubmission

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])
Authorized = Annotated[AuthContext, Depends(get_auth_context)]


def repository(request: Request) -> TaskRepository:
    value = getattr(request.app.state, "task_repository", None)
    if not isinstance(value, TaskRepository):
        raise HTTPException(503, "task_storage_unavailable")
    return value


def view(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task["id"],
        **{
            key: task[key]
            for key in (
                "trace_id",
                "project_id",
                "session_id",
                "status",
                "version",
                "input_version",
                "attempt_count",
                "cancel_requested",
                "result",
                "error_code",
                "created_at",
                "updated_at",
            )
        },
    }


async def authorized_task(request: Request, task_id: str, auth: AuthContext) -> dict[str, Any]:
    task = await repository(request).get(task_id)
    if task is None or task["user_id"] != auth.user_id:
        raise HTTPException(404, "task_not_found")
    require_project_access(auth, task["project_id"])
    await require_graph_result_access(request, task, auth)
    return task


async def require_graph_result_access(request: Request, task: dict[str, Any], auth: AuthContext) -> None:
    result = task["result"].get("graph_result")
    if not result:
        return
    graph = getattr(request.app.state, "graph_sync_repository", None)
    grant = await graph.permission(task["project_id"], auth.user_id) if graph is not None else None
    if grant is None or grant["permission_version"] != result.get("permission_version"):
        raise HTTPException(403, "graph_result_access_changed")


@router.post("", status_code=202)
async def submit(
    payload: TaskSubmission,
    request: Request,
    auth: Authorized,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> dict[str, Any]:
    require_project_access(auth, payload.project_id)
    try:
        task = await repository(request).submit(
            user_id=auth.user_id, idempotency_key=key, **payload.model_dump()
        )
    except TaskConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return {
        "task_id": task["id"],
        "trace_id": task["trace_id"],
        "status": task["status"],
        "replayed": task["replayed"],
    }


@router.get("")
async def list_tasks(request: Request, auth: Authorized) -> list[dict[str, Any]]:
    items = []
    for task in await repository(request).list_tasks(auth.user_id, auth.authorized_project_ids):
        try:
            await require_graph_result_access(request, task, auth)
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
            continue
        items.append(view(task))
    return items


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request, auth: Authorized) -> dict[str, Any]:
    return view(await authorized_task(request, task_id, auth))


@router.post("/{task_id}/resume", status_code=202)
async def resume(
    task_id: str, payload: TaskResume, request: Request, auth: Authorized
) -> dict[str, Any]:
    await authorized_task(request, task_id, auth)
    try:
        await repository(request).resume(task_id, **payload.model_dump())
    except (TaskConflict, TaskTransitionError) as exc:
        raise HTTPException(409, str(exc)) from None
    return view(await authorized_task(request, task_id, auth))


@router.post("/{task_id}/cancel", status_code=202)
async def cancel(task_id: str, request: Request, auth: Authorized) -> dict[str, Any]:
    await authorized_task(request, task_id, auth)
    try:
        await repository(request).cancel(task_id)
    except TaskTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    return view(await authorized_task(request, task_id, auth))


@router.get("/{task_id}/events")
async def events(
    task_id: str,
    request: Request,
    auth: Authorized,
    after: Annotated[int, Header(alias="Last-Event-ID", ge=0)] = 0,
) -> StreamingResponse:
    await authorized_task(request, task_id, auth)

    async def stream() -> AsyncIterator[str]:
        cursor = after
        while not await request.is_disconnected():
            try:
                current = await get_auth_context(request)
                task = await authorized_task(request, task_id, current)
            except HTTPException:
                yield 'event: stopped\ndata: {"error_code":"access_revoked"}\n\n'
                return
            batch = await repository(request).events(task_id, after=cursor)
            for event in batch:
                cursor = event["seq"]
                data = json.dumps({"status": event["status"], **event["data"]}, ensure_ascii=False)
                yield f"id: {cursor}\nevent: task\ndata: {data}\n\n"
            if task["status"] in TERMINAL and len(batch) < 100:
                return
            if not batch:
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
