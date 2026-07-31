"""Persistence for per-user daily token usage (#73).

Follows the repository pattern used across ``core`` (a ``session_scope`` per call). The increment
is an upsert because two turns of the same user can finish concurrently: the row is created once
and then incremented in place, with the unique ``(user_id, day)`` constraint as the arbiter.
"""

from datetime import UTC, date, datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.usage.usage_model import TokenUsage


def today_utc() -> date:
    """The current UTC date — the budget window boundary."""
    return datetime.now(UTC).date()


class TokenUsageRepository:
    """Reads and increments the per-user daily token counters."""

    async def add_usage(
        self, user_id: int, input_tokens: int, output_tokens: int, day: Optional[date] = None
    ) -> TokenUsage:
        """Add one turn's tokens to a user's daily total, creating the row on first use."""
        target_day = day or today_utc()
        with session_scope() as session:
            row = session.exec(
                select(TokenUsage).where(TokenUsage.user_id == user_id, TokenUsage.day == target_day)
            ).first()
            if row is None:
                row = TokenUsage(user_id=user_id, day=target_day)
                session.add(row)
                try:
                    session.commit()
                except IntegrityError:
                    # A concurrent turn created the row first — take theirs and increment it.
                    session.rollback()
                    row = session.exec(
                        select(TokenUsage).where(TokenUsage.user_id == user_id, TokenUsage.day == target_day)
                    ).one()
            row.input_tokens += input_tokens
            row.output_tokens += output_tokens
            row.turns += 1
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()
            session.refresh(row)
            logger.debug(
                "token_usage_recorded",
                user_id=user_id,
                day=str(target_day),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                day_total=row.total_tokens,
            )
            return row

    async def get_usage(self, user_id: int, day: Optional[date] = None) -> Optional[TokenUsage]:
        """This user's usage row for the given (default: current) UTC day, or None if unused."""
        target_day = day or today_utc()
        with session_scope() as session:
            return session.exec(
                select(TokenUsage).where(TokenUsage.user_id == user_id, TokenUsage.day == target_day)
            ).first()

    async def total_for_day(self, user_id: int, day: Optional[date] = None) -> int:
        """Total tokens this user consumed on the given (default: current) UTC day."""
        row = await self.get_usage(user_id, day)
        return row.total_tokens if row else 0
