"""Human-in-the-loop confirmation service (#19).

An outward-facing action is never executed inline: it is *requested* (parked as ``pending``) and
only runs when the owner *confirms* it. Executors are registered per action_type; the actual
side-effect (send email, publish, export) lives in the executor, so the gate stays generic.
"""

from typing import Awaitable, Callable, Optional

from src.app.core.common.logging import logger
from src.app.core.hitl.pending_model import PendingAction, PendingActionStatus
from src.app.core.hitl.repository import PendingActionRepository

# action_type -> async executor(action) -> result
Executor = Callable[[PendingAction], Awaitable[object]]
_EXECUTORS: dict[str, Executor] = {}


def register_executor(action_type: str, executor: Executor) -> None:
    """Register the side-effecting executor for an action type."""
    _EXECUTORS[action_type] = executor


class ConfirmationError(Exception):
    """Raised when an action cannot be confirmed/rejected (missing, not owned, not pending)."""


class HitlService:
    """Requests, confirms, and rejects confirmation-gated external actions."""

    def __init__(self, repo: Optional[PendingActionRepository] = None):
        """Build the service over a pending-action repository."""
        self._repo = repo or PendingActionRepository()

    async def request(self, user_id: int, session_id: str, action_type: str, payload: dict) -> PendingAction:
        """Park an external action for confirmation. It is NOT executed here."""
        action = await self._repo.create(user_id, session_id, action_type, payload)
        logger.info("hitl_action_requested", action_id=action.id, user_id=user_id, action_type=action_type)
        return action

    async def confirm(self, action_id: int, user_id: int) -> object:
        """Confirm and execute a pending action owned by ``user_id``. Returns the executor result."""
        action = await self._guard(action_id, user_id)
        executor = _EXECUTORS.get(action.action_type)
        if executor is None:
            raise ConfirmationError(f"Nenhum executor registrado para '{action.action_type}'.")
        result = await executor(action)
        await self._repo.set_status(action_id, PendingActionStatus.CONFIRMED)
        logger.info("hitl_action_confirmed", action_id=action_id, user_id=user_id)
        return result

    async def reject(self, action_id: int, user_id: int) -> None:
        """Reject a pending action owned by ``user_id`` — it is never executed."""
        await self._guard(action_id, user_id)
        await self._repo.set_status(action_id, PendingActionStatus.REJECTED)
        logger.info("hitl_action_rejected", action_id=action_id, user_id=user_id)

    async def _guard(self, action_id: int, user_id: int) -> PendingAction:
        """Ensure the action exists, is owned by the user, and is still pending."""
        action = await self._repo.get(action_id)
        if action is None:
            raise ConfirmationError("Ação não encontrada.")
        if action.user_id != user_id:
            logger.warning("hitl_action_access_denied", action_id=action_id, user_id=user_id)
            raise ConfirmationError("Ação pertence a outro usuário.")
        if action.status != PendingActionStatus.PENDING:
            raise ConfirmationError(f"Ação já está '{action.status}'.")
        return action
