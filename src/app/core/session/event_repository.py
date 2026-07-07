"""Repository for the session episodic event log.

Each method runs in its own short-lived session (``session_scope``) so a failed write rolls back
on its own and never poisons a later request. Persistence-only; scoping (who may read a session's
events) is enforced by the API layer.
"""

from typing import List, Optional

from sqlmodel import select

from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.session.event_model import SessionEvent


class SessionEventRepository:
    """Persistence for session events — the episodic audit log."""

    async def record_event(
        self,
        user_id: int,
        session_id: str,
        event_type: str,
        agent_id: Optional[int] = None,
        payload: Optional[dict] = None,
        scope: str = "",
    ) -> SessionEvent:
        """Append one event to a session's episodic log."""
        with session_scope() as session:
            event = SessionEvent(
                user_id=user_id,
                session_id=session_id,
                event_type=event_type,
                agent_id=agent_id,
                payload=payload or {},
                scope=scope,
            )
            session.add(event)
            session.commit()
            session.refresh(event)
            logger.info(
                "session_event_recorded",
                event_id=event.id,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                event_type=event_type,
                scope=scope,
            )
            return event

    async def get_session_events(self, session_id: str) -> List[SessionEvent]:
        """Return a session's events in chronological order (oldest first)."""
        with session_scope() as session:
            statement = (
                select(SessionEvent)
                .where(SessionEvent.session_id == session_id)
                .order_by(SessionEvent.created_at, SessionEvent.id)
            )
            return list(session.exec(statement).all())

    async def get_agent_events(self, user_id: int, agent_id: Optional[int] = None) -> List[SessionEvent]:
        """Return all of a user/agent's events across sessions (for reflection, #20)."""
        with session_scope() as session:
            statement = select(SessionEvent).where(SessionEvent.user_id == user_id)
            if agent_id is not None:
                statement = statement.where(SessionEvent.agent_id == agent_id)
            return list(session.exec(statement.order_by(SessionEvent.created_at, SessionEvent.id)).all())

    async def delete_for_session(self, session_id: str) -> int:
        """Delete every event of a session (cascade on session deletion). Returns the count."""
        with session_scope() as session:
            rows = list(session.exec(select(SessionEvent).where(SessionEvent.session_id == session_id)).all())
            for row in rows:
                session.delete(row)
            session.commit()
            return len(rows)
