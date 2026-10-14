"""Local demo principals are deliberately separate from production authentication."""

from fastapi import HTTPException, Request

from testmind.core.security import AuthContext
from testmind.infrastructure.postgres.task_repository import TaskRepository


class DemoAuthorizationProvider:
    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    async def get_context(self, request: Request) -> AuthContext | None:
        user_id = request.headers.get("X-Demo-User", "")
        if not user_id or len(user_id) > 200:
            return None
        try:
            return await self._repository.context(user_id)
        except Exception:  # noqa: BLE001 - no storage detail or credentials in responses.
            raise HTTPException(status_code=503, detail="authorization_unavailable") from None
