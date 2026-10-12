"""Authenticated, signed source-system event ingress."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from testmind.core.security import (
    AuthContext,
    get_auth_context,
    require_knowledge_base_access,
    require_project_access,
)

router = APIRouter(prefix="/api/v1/knowledge-events", tags=["knowledge-events"])
MAX_EVENT_BODY_BYTES = 1024 * 1024


async def read_bounded_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_EVENT_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Event body exceeds 1 MiB limit")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from None

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_EVENT_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Event body exceeds 1 MiB limit")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/zentao", status_code=status.HTTP_202_ACCEPTED)
async def receive_zentao_event(
    request: Request,
    context: AuthContext = Depends(get_auth_context),  # noqa: B008 - FastAPI dependency declaration
    x_correlation_id: str | None = Header(default=None),
) -> dict[str, object]:
    adapter = getattr(request.app.state, "zentao_event_adapter", None)
    if adapter is None:
        raise HTTPException(status_code=503, detail="ZenTao event adapter is not configured")
    try:
        body = await read_bounded_body(request)
        event = adapter.verify_and_normalize(request.headers, body)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="Invalid source event signature") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    require_project_access(context, event.project_ref)
    require_knowledge_base_access(context, event.kb_ref)
    repository = getattr(request.app.state, "knowledge_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="Knowledge repository is not configured")
    return await repository.register_source_event(
        event,
        actor_id=context.user_id,
        correlation_id=x_correlation_id or str(uuid4()),
    )
