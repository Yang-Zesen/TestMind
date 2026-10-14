import httpx
import pytest

from testmind.core.model_recovery import ModelOutputError, invoke_with_recovery


async def test_transport_retry_and_structured_model_repair_are_separate():
    attempts, delays = [], []
    async def invoke(feedback):
        attempts.append(feedback)
        if len(attempts) == 1:
            raise httpx.ConnectError("private connection details")
        return "invalid" if len(attempts) == 2 else "ok"
    def validate(value):
        if value != "ok":
            raise ModelOutputError("invalid_output", "answer")
        return value
    async def sleep(delay):
        delays.append(delay)
    assert await invoke_with_recovery(invoke, validate, sleep=sleep) == "ok"
    assert delays == [1]
    assert attempts == [None, None, {"code": "invalid_output", "field": "answer", "repair_attempt": 1}]


async def test_output_repairs_stop_and_permission_errors_do_not_retry():
    calls = []
    async def invalid(feedback):
        calls.append(feedback)
        return "invalid"
    def validate(value):
        raise ModelOutputError("invalid_output", "answer")
    with pytest.raises(ModelOutputError):
        await invoke_with_recovery(invalid, validate)
    assert len(calls) == 2
    async def denied(feedback):
        raise PermissionError("denied")
    with pytest.raises(PermissionError):
        await invoke_with_recovery(denied, validate)
