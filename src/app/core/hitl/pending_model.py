"""PendingAction: an external, outward-facing action held for explicit human confirmation (#19).

Anything that sends an artifact out of the system (email, publish, export) is registered here as
``pending`` and only executed after the owner confirms — approval in one context never carries to
the next. Scoped to (user, session).
"""

from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field

from src.app.core.common.model.base import BaseModel


class PendingActionStatus:
    """Lifecycle of a confirmation-gated action."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class PendingAction(BaseModel, table=True):
    """An outward-facing action awaiting the user's explicit confirmation."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    session_id: str = Field(default="", index=True)
    action_type: str = Field(index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="pending", index=True)
