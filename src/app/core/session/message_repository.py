"""Repository for persisted chat messages — the durable record of a session's conversation.

Append-only and scoped by ``session_id``. Reads are chronological with cursor pagination
(``before_id``) so a long conversation can be loaded in bounded pages, newest-page-first.

Follows the established repository pattern in this package (``SessionRepository`` /
``SessionEventRepository`` / ``PendingActionRepository``): a synchronous ``session_scope`` per call.
The pattern is deliberately consistent across the repos (and the in-memory test harness relies on
single-connection access); moving DB work off the loop is tracked repo-wide, not diverged here.
"""

from typing import List, Optional

from sqlalchemy import func
from sqlmodel import select

from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.session.message_model import ChatMessage, ChatMessageStep


class ChatMessageRepository:
    """Persistence for a session's conversation history, scoped by ``session_id``."""

    async def add_message(self, session_id: str, user_id: int, role: str, content: str) -> ChatMessage:
        """Append one message to a session's history."""
        with session_scope() as session:
            message = ChatMessage(session_id=session_id, user_id=user_id, role=role, content=content)
            session.add(message)
            session.commit()
            session.refresh(message)
            logger.info("chat_message_persisted", message_id=message.id, session_id=session_id, role=role)
            return message

    async def get_messages(
        self, session_id: str, limit: int = 200, before_id: Optional[int] = None
    ) -> List[ChatMessage]:
        """Return a page of a session's messages in chronological order.

        Loads the newest ``limit`` rows before the ``before_id`` cursor (or the latest page when the
        cursor is omitted), then reverses so the caller gets them oldest-first.
        """
        with session_scope() as session:
            statement = select(ChatMessage).where(ChatMessage.session_id == session_id)
            if before_id is not None:
                statement = statement.where(ChatMessage.id < before_id)
            statement = statement.order_by(ChatMessage.id.desc()).limit(limit)
            rows = list(session.exec(statement).all())
            rows.reverse()
            return rows

    async def count(self, session_id: str) -> int:
        """Count persisted messages in a session."""
        with session_scope() as session:
            statement = select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == session_id)
            return int(session.exec(statement).one())

    async def delete_for_session(self, session_id: str) -> int:
        """Delete every message of a session (cascade on session deletion). Returns the count."""
        with session_scope() as session:
            rows = list(session.exec(select(ChatMessage).where(ChatMessage.session_id == session_id)).all())
            for row in rows:
                session.delete(row)
            session.commit()
            return len(rows)


class ChatMessageStepRepository:
    """Persistence for the tool-activity trail of assistant turns, scoped by ``session_id``."""

    async def add_steps(self, session_id: str, message_id: int, steps: List[dict]) -> int:
        """Persist the ordered tool steps of one assistant turn. Returns how many were stored."""
        if not steps:
            return 0
        with session_scope() as session:
            for step in steps:
                session.add(
                    ChatMessageStep(
                        session_id=session_id,
                        message_id=message_id,
                        name=step.get("name", ""),
                        input=step.get("input"),
                        output=step.get("output"),
                    )
                )
            session.commit()
            logger.info("chat_steps_persisted", session_id=session_id, message_id=message_id, count=len(steps))
            return len(steps)

    async def get_for_session(self, session_id: str) -> List[ChatMessageStep]:
        """Return a session's tool steps in insertion order (oldest first)."""
        with session_scope() as session:
            statement = (
                select(ChatMessageStep).where(ChatMessageStep.session_id == session_id).order_by(ChatMessageStep.id)
            )
            return list(session.exec(statement).all())

    async def delete_for_session(self, session_id: str) -> int:
        """Delete every step of a session (cascade on session deletion). Returns the count."""
        with session_scope() as session:
            rows = list(session.exec(select(ChatMessageStep).where(ChatMessageStep.session_id == session_id)).all())
            for row in rows:
                session.delete(row)
            session.commit()
            return len(rows)
