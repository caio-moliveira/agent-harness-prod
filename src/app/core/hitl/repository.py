"""Repository for confirmation-gated pending actions (#19)."""

from typing import List, Optional

from sqlmodel import select

from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.hitl.pending_model import PendingAction, PendingActionStatus


class PendingActionRepository:
    """Persistence for pending external actions, scoped to (user_id, session_id)."""

    async def create(self, user_id: int, session_id: str, action_type: str, payload: dict) -> PendingAction:
        """Register a new action in the ``pending`` state (not executed)."""
        with session_scope() as session:
            action = PendingAction(
                user_id=user_id, session_id=session_id, action_type=action_type, payload=payload or {}
            )
            session.add(action)
            session.commit()
            session.refresh(action)
            logger.info("pending_action_created", action_id=action.id, user_id=user_id, action_type=action_type)
            return action

    async def get(self, action_id: int) -> Optional[PendingAction]:
        """Fetch one action by id (ownership enforced by the caller)."""
        with session_scope() as session:
            return session.get(PendingAction, action_id)

    async def list_pending(self, user_id: int) -> List[PendingAction]:
        """List a user's still-pending actions, oldest first (query-level scoped)."""
        with session_scope() as session:
            statement = (
                select(PendingAction)
                .where(PendingAction.user_id == user_id, PendingAction.status == PendingActionStatus.PENDING)
                .order_by(PendingAction.created_at)
            )
            return list(session.exec(statement).all())

    async def set_status(self, action_id: int, status: str) -> Optional[PendingAction]:
        """Move an action to ``confirmed`` or ``rejected``. None if not found."""
        with session_scope() as session:
            action = session.get(PendingAction, action_id)
            if action is None:
                return None
            action.status = status
            session.add(action)
            session.commit()
            session.refresh(action)
            return action
