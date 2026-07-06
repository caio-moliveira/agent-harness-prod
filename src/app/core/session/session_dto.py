"""This file contains the authentication schema for the application."""

import re
from typing import Optional

from pydantic import (
    BaseModel,
    Field,
    field_validator,
)

from src.app.core.common.token_dtos import Token


class SessionResponse(BaseModel):
    """Response model for session creation.

    Attributes:
        session_id: The unique identifier for the chat session
        agent_id: The agent this session is bound to (if any)
        name: Name of the session (defaults to empty string)
        token: The authentication token for the session
    """

    session_id: str = Field(..., description="The unique identifier for the chat session")
    agent_id: Optional[int] = Field(default=None, description="The agent this session is bound to")
    name: str = Field(default="", description="Name of the session", max_length=100)
    token: Token = Field(..., description="The authentication token for the session")

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        """Sanitize the session name.

        Args:
            v: The name to sanitize

        Returns:
            str: The sanitized name
        """
        # Remove any potentially harmful characters
        sanitized = re.sub(r'[<>{}[\]()\'"`]', "", v)
        return sanitized
