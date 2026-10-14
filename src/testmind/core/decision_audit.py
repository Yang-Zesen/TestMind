"""Emit bounded, content-free decision events for model-backed workflows."""

from __future__ import annotations

import logging
from typing import Any

_ALLOWED_FIELDS = frozenset(
    {
        "trace_id",
        "task_id",
        "attempt_id",
        "node",
        "project_id",
        "intent",
        "answer_mode",
        "outcome",
        "stop_reason",
        "error_code",
        "policy_decision",
        "tool_name",
        "model_version",
        "prompt_version",
        "schema_version",
        "duration_ms",
        "retry_count",
        "result_count",
    }
)


def _bounded_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:160]
    return None


def emit_decision_event(
    logger: logging.Logger, event: str, **fields: Any
) -> dict[str, Any]:
    """Log only approved identifiers and outcome metadata, never prompt content."""
    record: dict[str, str | int | float | bool] = {"event": event[:80]}
    record.update(
        {
            key: bounded
            for key, value in fields.items()
            if key in _ALLOWED_FIELDS and (bounded := _bounded_value(value)) is not None
        }
    )
    logger.info(event, extra={"decision_event": record})
    return record
