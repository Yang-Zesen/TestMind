from typing import Any

from fastapi import APIRouter, HTTPException, Request

from testmind.api.tasks import Authorized, authorized_task, repository
from testmind.infrastructure.postgres.task_repository import TaskConflict, TaskTransitionError
from testmind.models.approval import ApprovalDecision

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


@router.post("/{approval_id}/decision")
async def decide(
    approval_id: str, payload: ApprovalDecision, request: Request, auth: Authorized
) -> dict[str, Any]:
    repo = repository(request)
    approval = await repo.approval(approval_id)
    if approval is None:
        raise HTTPException(404, "approval_not_found")
    await authorized_task(request, approval["task_id"], auth)
    try:
        result = await repo.decide_approval(
            approval_id, reviewer=auth.user_id, **payload.model_dump()
        )
    except (TaskConflict, TaskTransitionError) as exc:
        raise HTTPException(409, str(exc)) from None
    return {
        key: result[key]
        for key in (
            "id",
            "task_id",
            "asset_version",
            "content_hash",
            "decision",
            "reviewer_id",
            "valid",
        )
    }
