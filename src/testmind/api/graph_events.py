"""Service-authenticated normalized ZenTao webhook boundary."""

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request

from testmind.api.graph_query import repository

router = APIRouter(prefix="/api/v1/integrations", tags=["graph-sync"])


@router.post("/zentao/events", status_code=202)
async def receive_graph_event(
    request: Request,
    x_event_timestamp: Annotated[int, Header()],
    x_event_signature: Annotated[str, Header()],
) -> dict[str, Any]:
    source = getattr(request.app.state, "graph_event_source", None)
    if source is None:
        raise HTTPException(503, "Graph source adapter is not configured")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 1024 * 1024:
            raise HTTPException(413, "Source event exceeds limit")
        body.extend(chunk)
    try:
        event = source.normalize(bytes(body), timestamp=x_event_timestamp, signature=x_event_signature)
    except PermissionError as exc:
        raise HTTPException(403, "Source event authorization failed") from exc
    except ValueError as exc:
        raise HTTPException(422, "Invalid normalized event") from exc
    try:
        result = await repository(request).register_event(event, actor_id=f"source:{source.source_instance}")
        if result["status"] == "conflict":
            raise HTTPException(409, "Event conflict")
        return dict(result)
    except ValueError as exc:
        raise HTTPException(409, "Event conflict") from exc
