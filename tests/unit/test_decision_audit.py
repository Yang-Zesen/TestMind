import asyncio
import json
import logging

import pytest

from testmind.core.config import Config
from testmind.core.decision_audit import emit_decision_event
from testmind.core.logging import DecisionAuditFormatter
from testmind.orchestration.nodes.base import BaseNode


def test_decision_audit_formatter_emits_allowlisted_fields_without_message_body(caplog):
    logger = logging.getLogger("audit-test")
    with caplog.at_level(logging.INFO, logger="audit-test"):
        event = emit_decision_event(
            logger,
            "ai_node_stopped",
            trace_id="trace-1",
            node="dialogue",
            outcome="insufficient_evidence",
            stop_reason="no_usable_evidence",
            forbidden_prompt="secret question",
        )
    record = next(record for record in caplog.records if hasattr(record, "decision_event"))
    formatted = DecisionAuditFormatter().format(record)
    parsed = json.loads(formatted)
    assert parsed["trace_id"] == "trace-1"
    assert event["stop_reason"] == "no_usable_evidence"
    assert "forbidden_prompt" not in parsed
    assert "secret question" not in formatted


@pytest.mark.asyncio
async def test_base_node_emits_stopped_reason_and_trace_without_user_content(caplog):
    class StubNode(BaseNode):
        name = "stub_ai_node"

        async def process(self, state):
            return {
                "status": "insufficient_evidence",
                "last_error": {"code": "no_evidence"},
            }

    node = StubNode()
    with caplog.at_level(logging.INFO, logger=node._logger.name):
        result = await node(
            {
                "trace_id": "trace-2",
                "user_id": "user-1",
                "raw_query": "sensitive input",
            }
        )

    records = [record for record in caplog.records if hasattr(record, "decision_event")]
    assert result["status"] == "insufficient_evidence"
    assert records
    stopped = records[-1].decision_event
    assert stopped["trace_id"] == "trace-2"
    assert stopped["node"] == "stub_ai_node"
    assert stopped["stop_reason"] == "no_usable_evidence"
    assert stopped["error_code"] == "no_evidence"
    assert "raw_query" not in stopped
    assert "sensitive input" not in str(stopped)


@pytest.mark.asyncio
async def test_base_node_records_exception_stop_reason_without_exception_text(caplog):
    class StubNode(BaseNode):
        name = "failing_ai_node"

        async def process(self, state):
            raise RuntimeError("provider response contains secret input")

    node = StubNode()
    with caplog.at_level(logging.INFO, logger=node._logger.name), pytest.raises(RuntimeError):
        await node({"trace_id": "trace-3"})

    stopped = [record.decision_event for record in caplog.records if hasattr(record, "decision_event")]
    assert stopped[-1]["stop_reason"] == "exception"
    assert stopped[-1]["error_code"] == "RuntimeError"
    assert "secret input" not in str(stopped[-1])


@pytest.mark.asyncio
async def test_base_node_timeout_is_audited_and_cancels_process(caplog):
    cancelled = asyncio.Event()

    class SlowNode(BaseNode):
        name = "slow_ai_node"

        async def process(self, state):
            try:
                await asyncio.sleep(0.25)
            finally:
                cancelled.set()

    node = SlowNode(config=Config(ai_node_timeout_seconds=0.01))
    with caplog.at_level(logging.INFO, logger=node._logger.name), pytest.raises(TimeoutError):
        await node({"trace_id": "trace-timeout"})

    stopped = [record.decision_event for record in caplog.records if hasattr(record, "decision_event")][-1]
    assert stopped["trace_id"] == "trace-timeout"
    assert stopped["outcome"] == "failed"
    assert stopped["stop_reason"] == "timeout"
    assert stopped["error_code"] == "ai_node_timeout"
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_base_node_does_not_misclassify_process_timeout_error(caplog):
    class FailingNode(BaseNode):
        name = "provider_timeout_node"

        async def process(self, state):
            raise TimeoutError("provider request timed out")

    node = FailingNode(config=Config(ai_node_timeout_seconds=1))
    with caplog.at_level(logging.INFO, logger=node._logger.name), pytest.raises(TimeoutError):
        await node({"trace_id": "trace-provider-timeout"})

    stopped = [record.decision_event for record in caplog.records if hasattr(record, "decision_event")][-1]
    assert stopped["trace_id"] == "trace-provider-timeout"
    assert stopped["stop_reason"] == "exception"
    assert stopped["error_code"] == "TimeoutError"


@pytest.mark.asyncio
async def test_base_node_records_caller_cancellation_before_propagating(caplog):
    started = asyncio.Event()

    class SlowNode(BaseNode):
        name = "cancelled_ai_node"

        async def process(self, state):
            started.set()
            await asyncio.Event().wait()

    node = SlowNode(config=Config(ai_node_timeout_seconds=1))
    task = asyncio.create_task(node({"trace_id": "trace-cancelled"}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stopped = [record.decision_event for record in caplog.records if hasattr(record, "decision_event")][-1]
    assert stopped["trace_id"] == "trace-cancelled"
    assert stopped["outcome"] == "cancelled"
    assert stopped["stop_reason"] == "cancelled"
    assert stopped["error_code"] == "task_cancelled"


def test_base_node_step_log_records_description_length_not_content(caplog):
    class StubNode(BaseNode):
        name = "step_log_test"

        async def process(self, state):
            return state

    node = StubNode()
    with caplog.at_level(logging.DEBUG, logger="step-log-test"):
        node.log_step("correction_result", "private corrected answer")

    assert "correction_result" in caplog.text
    assert "private corrected answer" not in caplog.text
    assert "description_length=24" in caplog.text
