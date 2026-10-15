"""Bounded, payload-free process readiness and project queue diagnostics."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4


@asynccontextmanager
async def component_heartbeat(pool: Any, component: str, *, model_ready: bool = False) -> AsyncIterator[None]:
    if not hasattr(pool, "connection"):
        yield
        return
    instance = str(uuid4())

    async def record(status: str) -> None:
        async with pool.connection() as connection:
            await connection.execute("""INSERT INTO business.component_heartbeats
                (instance_id,component,status,model_ready) VALUES (%s,%s,%s,%s)
                ON CONFLICT(instance_id) DO UPDATE SET status=EXCLUDED.status,
                model_ready=EXCLUDED.model_ready,seen_at=now()""", (instance, component, status, model_ready))

    async def pulse() -> None:
        while True:
            await asyncio.sleep(10)
            try:
                async with asyncio.timeout(5):
                    await record("ready")
            except Exception:  # noqa: BLE001 - stale heartbeat is the health signal.
                logging.getLogger(__name__).warning("component_heartbeat_unavailable")

    await record("ready")
    ticker = asyncio.create_task(pulse())
    try:
        yield
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
        try:
            async with asyncio.timeout(3):
                await record("stopped")
        except Exception:  # noqa: BLE001 - shutdown must not hang on PostgreSQL.
            logging.getLogger(__name__).warning("component_shutdown_heartbeat_unavailable")


async def runtime_snapshot(pool: Any, project_id: str) -> dict[str, Any]:
    async with pool.connection() as connection:
        cursor = await connection.execute("""SELECT DISTINCT ON(component) component,status,model_ready,
            seen_at,extract(epoch FROM now()-seen_at)::float AS age_seconds
            FROM business.component_heartbeats ORDER BY component,seen_at DESC""")
        components = [dict(row) for row in await cursor.fetchall()]
        await connection.execute("DELETE FROM business.component_heartbeats WHERE seen_at<now()-interval '7 days'")
        queues = {}
        for name, table in (("tasks", "business.tasks"), ("knowledge", "knowledge.jobs"), ("graph", "graph_sync.projection_outbox")):
            cursor = await connection.execute(f"""SELECT status,count(*) AS count,
                max(extract(epoch FROM now()-created_at))::float AS oldest_age_seconds
                FROM {table} WHERE project_id=%s GROUP BY status ORDER BY status""", (project_id,))
            queues[name] = [dict(row) for row in await cursor.fetchall()]
        cursor = await connection.execute("""SELECT id,trace_id,status,error_code,attempt_count,manual_retries,updated_at
            FROM business.tasks WHERE project_id=%s AND status IN ('failed','needs_reconciliation')
            ORDER BY updated_at DESC LIMIT 20""", (project_id,))
        failures = [dict(row) for row in await cursor.fetchall()]
        cursor = await connection.execute("""SELECT component,metric,sum(observations)::int AS observations,
            sum(failures)::int AS failures,round(sum(total_seconds)::numeric,3)::float AS total_seconds,
            max(max_seconds)::float AS max_seconds
            FROM business.runtime_metrics WHERE window_start>now()-interval '1 hour'
            GROUP BY component,metric ORDER BY component,metric""")
        metrics = [dict(row) for row in await cursor.fetchall()]
    alerts: list[dict[str, str]] = []
    expected = {"api", "worker", "knowledge-worker", "graph-worker"}
    live = set()
    for item in components:
        item["seen_at"] = item["seen_at"].isoformat()
        item["healthy"] = item["status"] == "ready" and item["age_seconds"] <= 45
        if item["healthy"]:
            live.add(item["component"])
    alerts.extend({"code": "component_unavailable", "component": item} for item in sorted(expected - live))
    for name, rows in queues.items():
        if any(row["status"] in {"failed", "failed_permanent", "needs_reconciliation"} for row in rows):
            alerts.append({"code": "queue_failures", "component": name})
        if any(row["status"] in {"queued", "failed_retryable"} and row["oldest_age_seconds"] > 60 for row in rows):
            alerts.append({"code": "queue_lag", "component": name})
    for failure in failures:
        failure["updated_at"] = failure["updated_at"].isoformat()
    return {"project_id": project_id, "components": components, "queues": queues,
            "alerts": alerts, "failures": failures,
            "metrics_last_hour": metrics,
            "limits": {"task_user_queue": 128, "task_project_queue": 256, "task_manual_retries": 3,
                       "component_stale_seconds": 45}, "scope": "single_host_demo"}
