"""Business task contracts, independent from graph checkpoints."""

from pydantic import BaseModel, ConfigDict, Field

TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"waiting_input", "waiting_review", "completed", "denied", "failed", "needs_reconciliation", "cancelled", "queued"},
    "waiting_input": {"queued", "cancelled"},
    "waiting_review": {"queued", "rejected", "cancelled"},
    "failed": {"queued"},
    "needs_reconciliation": {"completed", "failed", "waiting_review"},
}
TERMINAL = frozenset({"completed", "denied", "failed", "rejected", "cancelled"})


def allowed_transition(before: str, after: str) -> bool:
    return after in TRANSITIONS.get(before, set())


def retry_delay(attempt: int) -> float:
    if attempt < 1:
        raise ValueError("Attempt must be positive")
    return float(min(30, 2 ** min(attempt - 1, 5)))


class TaskSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=6000)
    project_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)


class TaskResume(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=6000)
    expected_version: int = Field(ge=1)
