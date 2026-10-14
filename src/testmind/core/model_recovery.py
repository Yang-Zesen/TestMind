"""Bounded transient retries and separately budgeted model output repairs."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from testmind.core.decision_audit import emit_decision_event
from testmind.core.logging import get_logger

_LOGGER = get_logger(__name__)


class ModelOutputError(ValueError):
    def __init__(self, code: str, field: str):
        super().__init__(code)
        self.code, self.field = code, field


async def invoke_with_recovery(
    invoke: Callable[[dict[str, Any] | None], Awaitable[Any]],
    validate: Callable[[Any], Any],
    *,
    timeout_seconds: float = 60,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    audit: dict[str, Any] | None = None,
) -> Any:
    feedback = None
    retries, repairs = 0, 0
    async with asyncio.timeout(timeout_seconds):
        while True:
            try:
                return validate(await invoke(feedback))
            except ModelOutputError as exc:
                if repairs >= 1:
                    raise
                repairs += 1
                feedback = {"code": exc.code, "field": exc.field, "repair_attempt": repairs}
                emit_decision_event(
                    _LOGGER,
                    "model_output_repair",
                    **(audit or {}),
                    outcome="retrying",
                    stop_reason="output_contract",
                    retry_count=repairs,
                )
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = not isinstance(
                    exc, httpx.HTTPStatusError
                ) or exc.response.status_code in {429, 500, 502, 503, 504}
                if not retryable or retries >= 2:
                    raise
                delay = 2**retries
                retries += 1
                emit_decision_event(
                    _LOGGER,
                    "model_dependency_retry",
                    **(audit or {}),
                    outcome="retrying",
                    stop_reason="transient_dependency",
                    retry_count=retries,
                )
                await sleep(delay)
