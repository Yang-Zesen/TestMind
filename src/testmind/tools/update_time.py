from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()
