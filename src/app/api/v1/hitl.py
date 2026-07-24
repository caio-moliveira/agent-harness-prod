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
from src.app.core.session.message_model import ChatMessageRole
from src.app.core.user.user_model import User
from src.app.init import chat_message_repository, hitl_service, pending_action_repository

router = APIRouter()

_RATE = settings.RATE_LIMIT_ENDPOINTS["hitl"][0]


async def _note_artifact_generated(action) -> None:
    """Record a short assistant message in the chat when an artifact is confirmed.

    It anchors an "artifact generated" note at the approval moment (part of the persisted history,
    not a floating card) and, being in the history window, keeps the agent aware it already ran.
    """
    if action.action_type != "export_artifact":
        return
    payload = action.payload or {}
    spec = payload.get("spec") or {}
    title = spec.get("title") or "artefato"
    fmt = (payload.get("fmt") or "").upper()
    text = f"📄 Artefato “{title}” ({fmt}) gerado com sucesso."
    await chat_message_repository.add_message(action.session_id, action.user_id, ChatMessageRole.ASSISTANT, text)


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


@router.get("/{action_id}/preview", response_model=PendingActionResponse)
@limiter.limit(_RATE)
async def preview_action(
    request: Request, action_id: int, user: User = Depends(get_current_user)
) -> PendingActionResponse:
    """Return a pending action's payload for review before confirming/rejecting. Owner-scoped."""
    action = await _owned_or_error(action_id, user)
    return PendingActionResponse(
        id=action.id,
        session_id=action.session_id,
        action_type=action.action_type,
        payload=action.payload,
        status=action.status,
    )


@router.post("/{action_id}/confirm")
@limiter.limit(_RATE)
async def confirm_action(request: Request, action_id: int, user: User = Depends(get_current_user)) -> dict:
    """Confirm and execute a pending action. Owner-scoped; 409 if it is no longer pending."""
    action = await _owned_or_error(action_id, user)
    try:
        result = await hitl_service.confirm(action_id, user.id)
    except ConfirmationError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await _note_artifact_generated(action)
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
