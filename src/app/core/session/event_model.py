"""Session event model: an episodic, auditable log of what happened in a session.

Each row records one meaningful action taken during a session — a document consulted, a SQL
query executed, a skill used, an artifact generated — scoped to the owning user and agent so the
trail stays per-``(user, agent)`` isolated. Written by the agent runtime, read back for audit
and (later) to feed continuous learning.
"""

from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field

from src.app.core.common.model.base import BaseModel


class SessionEventType:
    """The kinds of events captured in a session's episodic log."""

    DOCUMENT_READ = "document_read"
    QUERY_EXECUTED = "query_executed"
    SKILL_USED = "skill_used"
    ARTIFACT_GENERATED = "artifact_generated"
    WEB_RESEARCH = "web_research"


class SessionEvent(BaseModel, table=True):
    """One audited event in a session's episodic log.

    Attributes:
        id: The primary key.
        user_id: Foreign key to the owning user.
        agent_id: Foreign key to the agent in play (nullable).
        session_id: Foreign key to the session this event belongs to.
        event_type: The kind of event (see ``SessionEventType``).
        payload: Event-specific details (e.g. the SQL run, the file path, the skill name).
        scope: A short scope descriptor for auditing (e.g. ``db:sales``, ``folder:/workspace``).
        created_at: When the event occurred (from ``BaseModel``).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    agent_id: Optional[int] = Field(default=None, foreign_key="agent.id", index=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    event_type: str = Field(index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    scope: str = Field(default="")
