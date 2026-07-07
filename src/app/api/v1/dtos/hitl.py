"""DTOs for the human-in-the-loop confirmation endpoints (#19)."""

from pydantic import BaseModel, Field


class PendingActionResponse(BaseModel):
    """An action awaiting the user's confirmation."""

    id: int
    session_id: str = ""
    action_type: str
    payload: dict = Field(default_factory=dict)
    status: str
