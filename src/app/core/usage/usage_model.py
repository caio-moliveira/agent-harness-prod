"""TokenUsage: per-user, per-day token accounting — the unit cost governance runs on (#73).

Rate limiting caps *requests*; this caps *tokens*, which is what an LLM product actually pays for.
Two users with the same request count can differ by orders of magnitude in cost: one asks short
questions, the other runs 40-call turns over a large folder.

One row per ``(user_id, day)`` — deliberately aggregated rather than per-call:
- the enforcement question ("has this user spent their day's budget?") is a single indexed read;
- it keeps the table small (one row per active user per day) with no retention job needed early on;
- per-call detail already exists in the Langfuse traces, which is the right place for forensics.

``day`` is a UTC date so the budget resets on a boundary that doesn't move with the caller's
timezone (and, importantly, that a client cannot influence).
"""

from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, UniqueConstraint

from src.app.core.common.model.base import BaseModel


class TokenUsage(BaseModel, table=True):
    """Tokens consumed by one user on one UTC day."""

    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_tokenusage_user_day"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    day: date = Field(index=True, description="UTC date this usage belongs to")
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    # How many agent turns contributed — lets an operator tell "one huge turn" from "many turns"
    # when calibrating the budget.
    turns: int = Field(default=0)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def total_tokens(self) -> int:
        """Input + output — the number the budget is checked against."""
        return self.input_tokens + self.output_tokens
