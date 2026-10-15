import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from testmind.core.config import get_config

router = APIRouter()


def demo_asset(name: str) -> Path:
    packaged = Path(__file__).resolve().parents[1] / "demo" / "static" / name
    return packaged if packaged.is_file() else Path(__file__).resolve().parents[3] / "frontend" / "demo" / name


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "alive", "mode": "demo" if get_config().demo_mode else "configured"}


@router.get("/ready", response_model=None)
async def ready(request: Request, deep: bool = False) -> dict[str, str | bool] | JSONResponse:
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
        if deep:
            from pymilvus import MilvusClient
            config = get_config()

            def probe_milvus() -> bool:
                client = MilvusClient(uri=config.milvus_uri, token=config.milvus_token, timeout=3)
                try:
                    return bool(client.has_collection(config.milvus_collection, timeout=3))
                finally:
                    client.close()
            async with asyncio.timeout(8):
                if not await asyncio.to_thread(probe_milvus):
                    raise RuntimeError("collection_unavailable")
                driver = getattr(request.app.state, "graph_driver", None)
                if driver is None:
                    raise RuntimeError("graph_unavailable")
                await driver.verify_connectivity()
                async with postgres.pool.connection() as connection:
                    cursor = await connection.execute("""SELECT DISTINCT component FROM business.component_heartbeats
                        WHERE status='ready' AND seen_at>now()-interval '45 seconds'
                        AND (component NOT IN ('worker','knowledge-worker') OR model_ready=true)""")
                    live = {row["component"] for row in await cursor.fetchall()}
                    if not {"api", "worker", "knowledge-worker", "graph-worker"}.issubset(live):
                        raise RuntimeError("workers_not_ready")
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
    return FileResponse(demo_asset("index.html"))


@router.get("/demo/workflows.js", include_in_schema=False)
async def demo_workflows() -> FileResponse:
    if not get_config().demo_mode:
        raise HTTPException(404)
    return FileResponse(demo_asset("workflows.js"), media_type="text/javascript")
