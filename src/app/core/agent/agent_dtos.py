"""Pydantic DTOs for the Agent API."""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_UNSAFE = re.compile(r'[<>{}\[\]()\'"`]')


def _strip_unsafe(value: str) -> str:
    """Remove characters that could break downstream rendering/logging."""
    return _UNSAFE.sub("", value).strip()


class AgentCreate(BaseModel):
    """Request body to create an agent."""

    name: str = Field(..., min_length=1, max_length=100, description="Agent name")
    system_prompt: str = Field(default="", max_length=10000, description="Agent system prompt")

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        """Strip unsafe characters and reject a name that is empty after cleaning."""
        cleaned = _strip_unsafe(v)
        if not cleaned:
            raise ValueError("name must not be empty")
        return cleaned


class AgentUpdate(BaseModel):
    """Request body to update an agent. Omitted fields are left unchanged."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    system_prompt: Optional[str] = Field(default=None, max_length=10000)

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: Optional[str]) -> Optional[str]:
        """Strip unsafe characters; leave None untouched; reject an emptied name."""
        if v is None:
            return None
        cleaned = _strip_unsafe(v)
        if not cleaned:
            raise ValueError("name must not be empty")
        return cleaned


class AgentResponse(BaseModel):
    """Response model for an agent."""

    id: int
    name: str
    system_prompt: str
    folder: Optional[str] = Field(default=None, description="Bound sandboxed folder path, if any")
    config: dict = Field(default_factory=dict)


class BindFolderRequest(BaseModel):
    """Request body to bind a sandboxed folder to an agent."""

    path: str = Field(..., min_length=1, description="Host folder path to bind (read-only)")


class BindFolderResponse(BaseModel):
    """Response after binding/unbinding an agent's folder."""

    id: int
    folder: Optional[str] = None
