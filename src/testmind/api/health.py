import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from testmind.core.config import get_config

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "alive", "mode": "demo" if get_config().demo_mode else "configured"}


@router.get("/ready", response_model=None)
async def ready(request: Request) -> dict[str, str | bool] | JSONResponse:
    postgres = getattr(request.app.state, "postgresql", None)
    if postgres is None:
        return JSONResponse(
            {"status": "not_ready", "error_code": "postgres_unavailable"}, status_code=503
        )
    try:
        async with asyncio.timeout(3), postgres.pool.connection() as connection:
            await connection.execute("SELECT 1 FROM business.tasks LIMIT 1")
        if getattr(request.app.state, "graph", None) is None:
            raise RuntimeError("graph_not_initialized")
    except Exception:  # noqa: BLE001 - health responses never expose connection detail.
        return JSONResponse(
            {"status": "not_ready", "error_code": "dependencies_unavailable"}, status_code=503
        )
    return {
        "status": "ready",
        "mode": "demo" if get_config().demo_mode else "configured",
        "external_writes_enabled": False,
    }


@router.get("/demo", include_in_schema=False)
async def demo_page() -> FileResponse:
    if not get_config().demo_mode:
        raise HTTPException(404)
    return FileResponse(Path(__file__).resolve().parents[3] / "frontend" / "demo" / "index.html")


@router.get("/demo/workflows.js", include_in_schema=False)
async def demo_workflows() -> FileResponse:
    if not get_config().demo_mode:
        raise HTTPException(404)
    return FileResponse(Path(__file__).resolve().parents[3] / "frontend" / "demo" / "workflows.js", media_type="text/javascript")
