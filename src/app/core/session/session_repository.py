"""Session repository for managing session database operations."""

from typing import List, Optional

from fastapi import HTTPException
from sqlmodel import Session as DBSession, select

from src.app.core.common.logging import logger
from src.app.core.session.session_model import Session


class SessionRepository:
    """Repository class for session database operations.

    This class handles all database operations related to Sessions.
    """

    def __init__(self, session: DBSession):
        """Initialize session repository with database session.

        Args:
            session: SQLModel database session
        """
        self.session = session

    async def create_session(
        self, session_id: str, user_id: int, name: str = "", agent_id: Optional[int] = None
    ) -> Session:
        """Create a new chat session.

        Args:
            session_id: The ID for the new session
            user_id: The ID of the user who owns the session
            name: Optional name for the session (defaults to empty string)
            agent_id: Optional agent this session is bound to

        Returns:
            Session: The created session
        """
        chat_session = Session(id=session_id, user_id=user_id, name=name, agent_id=agent_id)
        self.session.add(chat_session)
        self.session.commit()
        self.session.refresh(chat_session)
        logger.info("session_created", session_id=session_id, user_id=user_id, name=name, agent_id=agent_id)
        return chat_session

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session by ID.

        Args:
            session_id: The ID of the session to delete

        Returns:
            bool: True if deletion was successful, False if session not found
        """
        chat_session = self.session.get(Session, session_id)
        if not chat_session:
            return False

        self.session.delete(chat_session)
        self.session.commit()
        logger.info("session_deleted", session_id=session_id)
        return True

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID.

        Args:
            session_id: The ID of the session to retrieve

        Returns:
            Optional[Session]: The session if found, None otherwise
        """
        chat_session = self.session.get(Session, session_id)
        return chat_session

    async def get_user_sessions(self, user_id: int, agent_id: Optional[int] = None) -> List[Session]:
        """Get all sessions for a user, optionally scoped to one agent.

        Args:
            user_id: The ID of the user
            agent_id: When provided, only sessions bound to this agent are returned

        Returns:
            List[Session]: List of user's sessions
        """
        statement = select(Session).where(Session.user_id == user_id)
        if agent_id is not None:
            statement = statement.where(Session.agent_id == agent_id)
        statement = statement.order_by(Session.created_at)
        sessions = self.session.exec(statement).all()
        return sessions

    async def update_session_name(self, session_id: str, name: str) -> Session:
        """Update a session's name.

        Args:
            session_id: The ID of the session to update
            name: The new name for the session

        Returns:
            Session: The updated session

        Raises:
            HTTPException: If session is not found
        """
        chat_session = self.session.get(Session, session_id)
        if not chat_session:
            raise HTTPException(status_code=404, detail="Session not found")

        chat_session.name = name
        self.session.add(chat_session)
        self.session.commit()
        self.session.refresh(chat_session)
        logger.info("session_name_updated", session_id=session_id, name=name)
        return chat_session
