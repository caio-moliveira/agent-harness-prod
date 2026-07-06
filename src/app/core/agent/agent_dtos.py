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
    database: Optional["DatabaseSummary"] = Field(default=None, description="Bound database summary, if any")
    config: dict = Field(default_factory=dict)


class BindFolderRequest(BaseModel):
    """Request body to bind a sandboxed folder to an agent."""

    path: str = Field(..., min_length=1, description="Host folder path to bind (read-only)")


class BindFolderResponse(BaseModel):
    """Response after binding/unbinding an agent's folder."""

    id: int
    folder: Optional[str] = None


class BindDatabaseRequest(BaseModel):
    """Connection details for binding a read-only database to an agent."""

    driver: str = Field(default="postgresql", description="SQLAlchemy driver, e.g. postgresql or mysql+pymysql")
    host: str = Field(..., min_length=1)
    port: int = Field(..., gt=0, lt=65536)
    database: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: str = Field(..., description="Encrypted at rest when ENCRYPTION_KEY is configured")
    sslmode: Optional[str] = None


class DatabaseSummary(BaseModel):
    """Non-secret summary of a bound database (never includes the password)."""

    driver: str
    host: str
    port: int
    database: str
    username: str
    sslmode: Optional[str] = None
    password_persisted: bool = False


class BindDatabaseResponse(BaseModel):
    """Response after binding/unbinding an agent's database."""

    id: int
    database: Optional[DatabaseSummary] = None
    password_persisted: bool = False


# Resolve the forward reference to DatabaseSummary used by AgentResponse.
AgentResponse.model_rebuild()
