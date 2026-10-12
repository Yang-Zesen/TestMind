"""Typed contracts for knowledge ingestion, quality admission, and versioning."""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_PERMANENT = "failed_permanent"


class VersionStatus(StrEnum):
    PROCESSING = "processing"
    QUALITY_PASSED = "quality_passed"
    INDEX_PENDING = "index_pending"
    READY = "ready"
    REJECTED = "rejected"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_PERMANENT = "failed_permanent"
    ACTIVE = "active"
    ARCHIVED = "archived"


class QualityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    status: Literal["passed", "failed", "skipped"]
    severity: Literal["error", "warning"]
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    overall_status: Literal["passed", "failed"]
    quality_status: Literal["passed", "failed"]
    index_status: Literal["pending", "accepted", "failed"] = "pending"
    checks: tuple[QualityCheck, ...]
    metrics: dict[str, int | float | str] = Field(default_factory=dict)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def quality_report(
    checks: list[QualityCheck], *, metrics: dict[str, int | float | str] | None = None
) -> QualityReport:
    failed = any(check.status == "failed" and check.severity == "error" for check in checks)
    status = "failed" if failed else "passed"
    return QualityReport(
        overall_status=status,
        quality_status=status,
        checks=tuple(checks),
        metrics=metrics or {},
    )


def deterministic_chunk_id(version_id: str, chunk_index: int) -> str:
    if not version_id or chunk_index < 0:
        raise ValueError("version_id and non-negative chunk_index are required")
    return hashlib.sha256(f"{version_id}:{chunk_index}".encode()).hexdigest()


def idempotency_result(existing_fingerprint: str, request_fingerprint: str) -> Literal["replay"]:
    if existing_fingerprint != request_fingerprint:
        raise HTTPException(status_code=409, detail="Idempotency key conflicts with another request")
    return "replay"


def activation_allowed(status: VersionStatus) -> bool:
    return status in {VersionStatus.READY, VersionStatus.ARCHIVED}


def retry_delay_seconds(
    attempt: int,
    *,
    base_seconds: float = 2.0,
    max_seconds: float = 300.0,
    jitter: float | None = None,
) -> float:
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    delay = min(max_seconds, base_seconds * (2 ** (attempt - 1)))
    spread = random.uniform(0, 0.2) if jitter is None else jitter
    if not 0 <= spread <= 1:
        raise ValueError("jitter must be between 0 and 1")
    return min(max_seconds, delay * (1 + spread))
