import pytest
from fastapi import HTTPException

from testmind.models.knowledge import (
    JobStatus,
    QualityCheck,
    VersionStatus,
    activation_allowed,
    deterministic_chunk_id,
    idempotency_result,
    quality_report,
    retry_delay_seconds,
)


def test_idempotency_key_replays_same_request_and_conflicts_on_changed_request():
    assert idempotency_result("same", "same") == "replay"
    with pytest.raises(HTTPException) as exc:
        idempotency_result("original", "different")
    assert exc.value.status_code == 409


def test_quality_warnings_do_not_reject_but_errors_do():
    warning = quality_report([QualityCheck(name="ocr", status="failed", severity="warning")])
    error = quality_report([QualityCheck(name="text", status="failed", severity="error")])

    assert warning.overall_status == "passed"
    assert error.overall_status == "failed"


def test_chunk_ids_are_stable_and_activation_requires_accepted_version():
    assert deterministic_chunk_id("v1", 0) == deterministic_chunk_id("v1", 0)
    assert deterministic_chunk_id("v1", 0) != deterministic_chunk_id("v1", 1)
    assert activation_allowed(VersionStatus.READY)
    assert activation_allowed(VersionStatus.ARCHIVED)
    assert not activation_allowed(VersionStatus.REJECTED)
    assert not activation_allowed(VersionStatus.FAILED_PERMANENT)


def test_retry_backoff_is_bounded_and_states_distinguish_quality_and_infrastructure():
    assert retry_delay_seconds(1, base_seconds=2, max_seconds=10, jitter=0) == 2
    assert retry_delay_seconds(8, base_seconds=2, max_seconds=10, jitter=0) == 10
    assert JobStatus.FAILED_RETRYABLE != JobStatus.FAILED_PERMANENT
    assert VersionStatus.REJECTED != VersionStatus.FAILED_RETRYABLE
