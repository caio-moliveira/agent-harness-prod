"""ChatMessage: one durable, persisted turn in a session's conversation history.

The Data Agent is stateless per request (the client resends context), so this append-only table is
the source of truth for *reopening* a past conversation. Rows are ordered by their monotonic ``id``
within a session and scoped to (user, session).

Kept out of ``session/__init__`` on purpose: this module is imported late (from ``init``), after the
``session`` and ``user`` tables are registered, so ``create_all`` never sees the FK before its target
(mirrors the ``event_model`` caveat).
"""

from typing import Optional

from sqlalchemy import Column, Text
from sqlmodel import Field

from src.app.core.common.model.base import BaseModel


class ChatMessageRole:
    """Who authored a persisted message."""

    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel, table=True):
    """A single persisted message in a session's conversation history."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role: str = Field(index=True)
    content: str = Field(sa_column=Column(Text))
