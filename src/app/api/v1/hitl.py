"""Human-in-the-loop API (#19): list, confirm, or reject confirmation-gated external actions.

Outward-facing actions (send/publish/export an artifact) are parked as ``pending`` and only run
once the owner explicitly confirms here. Owner-scoped: a user can only act on their own actions.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from src.app.api.security.limiter import limiter
from src.app.api.v1.auth import get_current_user
from src.app.api.v1.dtos.hitl import PendingActionResponse
from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.hitl import ConfirmationError
from src.app.core.user.user_model import User
from src.app.init import hitl_service, pending_action_repository

router = APIRouter()

_RATE = settings.RATE_LIMIT_ENDPOINTS["hitl"][0]


@router.get("/pending", response_model=List[PendingActionResponse])
@limiter.limit(_RATE)
async def list_pending(request: Request, user: User = Depends(get_current_user)) -> List[PendingActionResponse]:
    """List the authenticated user's actions still awaiting confirmation."""
    actions = await pending_action_repository.list_pending(user.id)
    return [
        PendingActionResponse(
            id=a.id, session_id=a.session_id, action_type=a.action_type, payload=a.payload, status=a.status
        )
        for a in actions
    ]


async def _owned_or_error(action_id: int, user: User):
    """Fetch an action and enforce ownership (404 absent, 403 owned-by-another)."""
    action = await pending_action_repository.get(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Ação não encontrada.")
    if action.user_id != user.id:
        logger.warning("hitl_access_denied", action_id=action_id, user_id=user.id)
        raise HTTPException(status_code=403, detail="Ação pertence a outro usuário.")
    return action


@router.post("/{action_id}/confirm")
@limiter.limit(_RATE)
async def confirm_action(request: Request, action_id: int, user: User = Depends(get_current_user)) -> dict:
    """Confirm and execute a pending action. Owner-scoped; 409 if it is no longer pending."""
    await _owned_or_error(action_id, user)
    try:
        result = await hitl_service.confirm(action_id, user.id)
    except ConfirmationError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"confirmed": True, "result": result}


@router.post("/{action_id}/reject")
@limiter.limit(_RATE)
async def reject_action(request: Request, action_id: int, user: User = Depends(get_current_user)) -> dict:
    """Reject a pending action so it is never executed. Owner-scoped."""
    await _owned_or_error(action_id, user)
    try:
        await hitl_service.reject(action_id, user.id)
    except ConfirmationError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"rejected": True}
