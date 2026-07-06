"""DTOs for the session episodic event log endpoint."""

from datetime import datetime

from pydantic import BaseModel, Field


class SessionEventResponse(BaseModel):
    """One event in a session's episodic audit log."""

    id: int = Field(..., description="The event's primary key")
    agent_id: int | None = Field(default=None, description="The agent in play, if any")
    session_id: str = Field(..., description="The session this event belongs to")
    event_type: str = Field(..., description="document_read | query_executed | skill_used | artifact_generated")
    payload: dict = Field(default_factory=dict, description="Event-specific details")
    scope: str = Field(default="", description="Short scope descriptor for auditing")
    created_at: datetime = Field(..., description="When the event occurred")
