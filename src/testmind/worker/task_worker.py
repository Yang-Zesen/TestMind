"""Independent PostgreSQL-leased graph worker; only read-only tasks are enabled."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from testmind.core.decision_audit import emit_decision_event
from testmind.core.logging import get_logger
from testmind.infrastructure.postgres.task_repository import TaskRepository
from testmind.models.task import retry_delay
from testmind.services.request_context import RequestContextService

_LOGGER = get_logger(__name__)
_RESULT_FIELDS = {
    "answer",
    "status",
    "intent",
    "citations",
    "clarification_question",
    "last_error",
    "insufficient_evidence",
    "current_stage",
    "answer_mode",
}


class TaskWorker:
    def __init__(
        self,
        repository: TaskRepository,
        graph: Any,
        scope_repository: Any,
        *,
        timeout_seconds: float = 60,
    ) -> None:
        if not 0 < timeout_seconds <= 300:
            raise ValueError("Task timeout must be between 0 and 300 seconds")
        self.repository, self.graph = repository, graph
        self.scope_service = RequestContextService(scope_repository)
        self.timeout = timeout_seconds

    async def _execute(self, task: dict[str, Any]) -> dict[str, Any]:
        auth = await self.repository.context(task["user_id"])
        if auth is None or task["project_id"] not in auth.authorized_project_ids:
            return {"status": "denied", "last_error": {"code": "project_access_denied"}}
        context = await self.scope_service.build(auth, project_id=task["project_id"])
        messages: list[Any] = []
        for turn in await self.repository.history(task):
            messages.extend(
                [HumanMessage(content=turn["query"]), AIMessage(content=turn["answer"])]
            )
        messages.append(HumanMessage(content=task["query"]))
        if sum(len(str(message.content)) for message in messages) > 16000:
            messages = messages[-1:]
        state = {
            "task_id": task["id"],
            "trace_id": task["trace_id"],
            "attempt_id": task["claim_token"],
            "user_id": auth.user_id,
            "project_id": task["project_id"],
            "session_id": task["session_id"],
            "raw_query": task["query"],
            "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages],
            "resolved_query": "",
            "corrected_query": "",
            "clarification_question": "",
            "status": "running",
            "answer": "",
            "citations": [],
            "retry_count": 0,
            "last_error": {},
        }
        result = await self.graph.ainvoke(
            state,
            context=context,
            config={
                "configurable": {"thread_id": task["execution_thread_id"]},
                "recursion_limit": 20,
            },
        )
        if not isinstance(result, dict):
            raise TypeError("invalid_graph_result")
        result = {key: value for key, value in result.items() if key in _RESULT_FIELDS}
        if len(json.dumps(result, ensure_ascii=False)) > 24000 or not isinstance(
            result.get("answer", ""), str
        ):
            raise ValueError("invalid_graph_result")
        return result

    async def run_once(self, *, task_id: str | None = None) -> bool:
        task = await self.repository.claim(task_id=task_id, lease_seconds=self.timeout + 10)
        if task is None:
            return False
        fields = {
            "task_id": task["id"],
            "trace_id": task["trace_id"],
            "attempt_id": task["claim_token"],
            "project_id": task["project_id"],
        }
        emit_decision_event(_LOGGER, "task_started", **fields, outcome="running")
        result: dict[str, Any] = {}
        code, status, retry_seconds = None, "failed", 0.0
        execution = asyncio.create_task(self._execute(task))
        remaining = min(self.timeout, float(task["budget_remaining"]))
        try:
            async with asyncio.timeout(remaining):
                while not execution.done():
                    await asyncio.wait({execution}, timeout=0.1)
                    current = await self.repository.get(task["id"])
                    if current is None or current["claim_token"] != task["claim_token"]:
                        return True
                    auth = await self.repository.context(task["user_id"])
                    if current["cancel_requested"]:
                        status, code = "cancelled", "cancelled"
                        break
                    if auth is None or task["project_id"] not in auth.authorized_project_ids:
                        status, code = "denied", "project_access_denied"
                        break
                else:
                    result = await execution
                    graph_status = result.get("status", "failed")
                    status = {
                        "needs_input": "waiting_input",
                        "insufficient_evidence": "completed",
                    }.get(graph_status, graph_status)
                    if status not in {"completed", "waiting_input", "denied", "failed"}:
                        status = "failed"
                    code = result.get("last_error", {}).get("code")
        except TimeoutError:
            code = "task_deadline_exhausted" if remaining < self.timeout else "task_timeout"
        except (httpx.TimeoutException, httpx.TransportError):
            code = "dependency_unavailable"
            if task["attempt_count"] < task["max_attempts"]:
                status, retry_seconds = "queued", retry_delay(task["attempt_count"])
        except asyncio.CancelledError:
            # Leave running: another process can reclaim the expired read-only lease.
            emit_decision_event(
                _LOGGER,
                "task_interrupted",
                **fields,
                outcome="interrupted",
                stop_reason="worker_shutdown",
            )
            raise
        except Exception as exc:  # noqa: BLE001 - persist bounded errors without exception text.
            code = (
                "invalid_graph_result"
                if isinstance(exc, (TypeError, ValueError))
                else "task_execution_failed"
            )
        finally:
            if not execution.done():
                execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        # Authorization may change in the final gap after the graph has returned.
        auth = await self.repository.context(task["user_id"])
        if auth is None or task["project_id"] not in auth.authorized_project_ids:
            status, code, result = "denied", "project_access_denied", {}
        saved = await self.repository.finish(
            task, status=status, result=result, error_code=code, retry_seconds=retry_seconds
        )
        if not saved:
            emit_decision_event(
                _LOGGER,
                "task_result_discarded",
                **fields,
                outcome="discarded",
                stop_reason="stale_lease",
            )
            return True
        emit_decision_event(
            _LOGGER,
            "task_stopped",
            **fields,
            outcome=status,
            stop_reason=code or status,
            error_code=code,
            retry_count=task["attempt_count"] - 1,
        )
        return True


async def run(*, once: bool = False, task_id: str | None = None) -> None:
    from testmind.core.config import get_config
    from testmind.demo.runtime import create_demo_graph
    from testmind.infrastructure.postgres.knowledge_repository import KnowledgeRepository
    from testmind.infrastructure.postgres.postgresql_client import open_postgresql_client

    if not get_config().demo_mode:
        raise RuntimeError("This task Worker currently requires explicit local demo mode")
    async with open_postgresql_client() as postgres:
        repository = TaskRepository(postgres.pool)
        worker = TaskWorker(
            repository,
            create_demo_graph(postgres.checkpointer, repository),
            KnowledgeRepository(postgres.pool),
        )
        while True:
            executed = await worker.run_once(task_id=task_id)
            if once:
                return
            if not executed:
                await asyncio.sleep(0.5)


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv

    load_dotenv("deploy/demo.env", override=True)
    os.environ["PYTHON_DOTENV_DISABLED"] = "true"
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run())
