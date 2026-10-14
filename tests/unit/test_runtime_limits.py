import asyncio

import httpx
from fastapi import FastAPI

from testmind.core.runtime_limits import RuntimeLimitsMiddleware


async def test_request_rate_and_concurrency_limits():
    app = FastAPI()
    app.add_middleware(RuntimeLimitsMiddleware, max_concurrent=1, max_requests=2)
    started, release = asyncio.Event(), asyncio.Event()

    @app.post("/chat")
    async def chat():
        started.set()
        await release.wait()
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        first = asyncio.create_task(client.post("/chat"))
        await started.wait()
        assert (await client.post("/chat")).status_code == 429
        release.set()
        assert (await first).status_code == 200
        assert (await client.post("/chat")).status_code == 429


async def test_health_bypasses_model_request_limiter():
    app = FastAPI()
    app.add_middleware(RuntimeLimitsMiddleware, max_concurrent=1, max_requests=1)

    @app.get("/health")
    async def health():
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/health")).status_code == 200
