"""User repository for managing user database operations."""

from typing import Optional

from sqlmodel import Session, select

from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.user.user_model import User


class UserRepository:
    """Repository class for user database operations.

    Each method runs in its own short-lived session (``session_scope``) so a failed query rolls
    back on its own and never poisons a later request.
    """

    def __init__(self, session: Optional[Session] = None):
        """Accept an optional session for backward compatibility; methods use their own scope."""
        self.session = session

    async def create_user(self, email: str, password: str) -> User:
        """Create a new user.

        Args:
            email: User's email address
            password: Hashed password

        Returns:
            User: The created user
        """
        with session_scope() as session:
            user = User(email=email, hashed_password=password)
            session.add(user)
            session.commit()
            session.refresh(user)
            logger.info("user_created", email=email)
            return user

    async def get_user(self, user_id: int) -> Optional[User]:
        """Get a user by ID.

        Args:
            user_id: The ID of the user to retrieve

        Returns:
            Optional[User]: The user if found, None otherwise
        """
        with session_scope() as session:
            return session.get(User, user_id)

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Get a user by email.

        Args:
            email: The email of the user to retrieve

        Returns:
            Optional[User]: The user if found, None otherwise
        """
        with session_scope() as session:
            return session.exec(select(User).where(User.email == email)).first()

    async def delete_user_by_email(self, email: str) -> bool:
        """Delete a user by email.

        Args:
            email: The email of the user to delete

        Returns:
            bool: True if deletion was successful, False if user not found
        """
        with session_scope() as session:
            user = session.exec(select(User).where(User.email == email)).first()
            if not user:
                return False
            session.delete(user)
            session.commit()
            logger.info("user_deleted", email=email)
            return True
