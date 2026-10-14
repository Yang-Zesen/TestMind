"""Actual API restart and killed-worker lease recovery on isolated demo data."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


async def hold_task(task_id: str) -> None:
    from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
    from testmind.infrastructure.postgres.postgresql_client import open_postgresql_client
    from testmind.infrastructure.postgres.task_repository import TaskRepository
    from testmind.worker.task_worker import TaskWorker

    class SlowGraph:
        async def ainvoke(self, *args, **kwargs):
            await asyncio.sleep(120)

    async with open_postgresql_client() as postgres:
        await TaskWorker(TaskRepository(postgres.pool), SlowGraph(), KnowledgeRepository(postgres.pool), timeout_seconds=1).run_once(task_id=task_id)


def stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            process.kill()
    process.wait(timeout=10)


def main() -> None:
    load_dotenv("deploy/demo.env", override=True)
    os.environ["PYTHON_DOTENV_DISABLED"] = "true"
    if len(sys.argv) == 3 and sys.argv[1] == "hold":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(hold_task(sys.argv[2]))
        return
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    children = []
    Path("tmp").mkdir(exist_ok=True)
    with Path("tmp/demo-process-smoke.log").open("w", encoding="utf-8") as log:
        def launch(*args):
            process = subprocess.Popen([sys.executable, *args], stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            children.append(process)
            return process
        def api():
            process = launch("scripts/run_demo.py", "api", "--port", str(port))
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("API exited; inspect tmp/demo-process-smoke.log")
                try:
                    if httpx.get(base + "/ready", timeout=1, trust_env=False).status_code == 200:
                        return process
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
            raise TimeoutError("API readiness timeout")
        try:
            with httpx.Client(base_url=base, timeout=10, trust_env=False, headers={"X-Demo-User": "demo-user"}) as client, psycopg.connect(os.environ["POSTGRES_DSN"], autocommit=True, row_factory=dict_row) as connection:
                first = api()
                key = str(uuid4())
                payload = {"query": "What is pytest?", "project_id": "demo-project", "session_id": "process-smoke-" + key}
                response = client.post("/api/v1/tasks", json=payload, headers={"Idempotency-Key": key})
                assert response.status_code == 202
                task_id = response.json()["task_id"]
                held = launch("scripts/smoke_demo.py", "hold", task_id)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    task = connection.execute("SELECT * FROM business.tasks WHERE id=%s", (task_id,)).fetchone()
                    if task["status"] == "running":
                        break
                    time.sleep(0.05)
                assert task["status"] == "running"
                stop(held)
                stop(first)
                api()
                replay = client.post("/api/v1/tasks", json=payload, headers={"Idempotency-Key": key})
                assert replay.json()["task_id"] == task_id and replay.json()["replayed"]
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if connection.execute("SELECT lease_expires_at < now() AS expired FROM business.tasks WHERE id=%s", (task_id,)).fetchone()["expired"]:
                        break
                    time.sleep(0.2)
                recovered = launch("scripts/run_demo.py", "worker", "--once", "--task-id", task_id)
                assert recovered.wait(timeout=45) == 0
                task = client.get(f"/api/v1/tasks/{task_id}").json()
                assert task["status"] == "completed" and task["attempt_count"] == 2
                assert "Pytest" in task["result"]["answer"]
                events = client.get(f"/api/v1/tasks/{task_id}/events", headers={"Last-Event-ID": "1"})
                assert "id: 1\n" not in events.text and "id: 4\n" in events.text
                result = {"api_restart": "passed", "worker_kill_reclaim": "passed", "idempotency_replay": "passed", "sse_resume": "passed", "task_id": task_id, "attempt_count": 2}
                Path("tmp/demo-process-smoke.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
                print(json.dumps(result))
        finally:
            for process in children:
                stop(process)


if __name__ == "__main__":
    main()
