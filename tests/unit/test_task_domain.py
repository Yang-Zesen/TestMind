import pytest
from pydantic import ValidationError

from testmind.models.task import TaskSubmission, allowed_transition, retry_delay


@pytest.mark.parametrize("before,after,allowed", [
    ("queued", "running", True), ("running", "completed", True),
    ("running", "waiting_input", True), ("completed", "running", False),
    ("cancelled", "completed", False), ("waiting_input", "queued", True),
    ("queued", "completed", False),
])
def test_task_transitions(before, after, allowed):
    assert allowed_transition(before, after) is allowed


def test_task_input_cannot_supply_identity_or_unbounded_query():
    with pytest.raises(ValidationError):
        TaskSubmission(query="hello", project_id="demo-project", session_id="s", user_id="other")
    with pytest.raises(ValidationError):
        TaskSubmission(query="x" * 6001, project_id="demo-project", session_id="s")


def test_retry_backoff_is_bounded():
    assert [retry_delay(i) for i in range(1, 7)] == [1, 2, 4, 8, 16, 30]
