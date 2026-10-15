"""Measure bounded durable task admission against the local PostgreSQL demo."""

import asyncio
import json
import os
import time
from pathlib import Path
from statistics import median
from uuid import uuid4

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from testmind.infrastructure.postgres.task_repository import TaskCapacityError, TaskRepository


async def main_async() -> None:
    if os.getenv("TESTMIND_DEMO_MODE") != "true":
        raise RuntimeError("Capacity test is restricted to the local demo")
    dsn = os.environ["POSTGRES_DSN"]
    project = "capacity-" + uuid4().hex
    async with AsyncConnectionPool(dsn, open=False, kwargs={"row_factory": dict_row, "autocommit": True}, max_size=24) as pool:
        await pool.open()
        async with pool.connection() as connection:
            await connection.execute("INSERT INTO business.demo_grants(user_id,project_id,kb_id) VALUES ('demo-user',%s,'demo-kb')", (project,))
        repository = TaskRepository(pool, queue_limit=128)
        calls = 256
        latencies: list[float] = []
        accepted: list[str] = []
        rejected = 0
        started = time.monotonic()

        async def submit(index: int) -> None:
            nonlocal rejected
            before = time.monotonic()
            try:
                task = await repository.submit(user_id="demo-user", project_id=project,
                    session_id=project, query=f"capacity sample {index}", idempotency_key=str(uuid4()))
                accepted.append(task["id"])
            except TaskCapacityError:
                rejected += 1
            finally:
                latencies.append(time.monotonic() - before)

        try:
            await asyncio.gather(*(submit(index) for index in range(calls)))
            elapsed = time.monotonic() - started
            ordered = sorted(latencies)
            report = {"environment": "local demo PostgreSQL", "requests": calls,
                "accepted": len(accepted), "capacity_rejected": rejected,
                "queue_limit_per_user": 128, "elapsed_seconds": round(elapsed, 3),
                "accepted_per_second": round(len(accepted) / elapsed, 2),
                "admission_latency_seconds": {"p50": round(median(ordered), 4),
                    "p95": round(ordered[min(len(ordered)-1, int(len(ordered)*.95))], 4),
                    "p99": round(ordered[min(len(ordered)-1, int(len(ordered)*.99))], 4)},
                "scope": "one local host; admission/PG only, no model inference"}
            output = Path("output/p6-capacity.json")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
        finally:
            async with pool.connection() as connection, connection.transaction():
                await connection.execute("DELETE FROM business.tasks WHERE project_id=%s", (project,))
                await connection.execute("DELETE FROM business.demo_grants WHERE project_id=%s AND user_id='demo-user'", (project,))


def main() -> None:
    load_dotenv("deploy/demo.env", override=True)
    os.environ["PYTHON_DOTENV_DISABLED"] = "true"
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main_async())


if __name__ == "__main__":
    main()
