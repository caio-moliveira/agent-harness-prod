"""Pydantic DTOs for the Skill API."""

import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

_SLUG_UNSAFE = re.compile(r"[^a-zA-Z0-9 _-]")


class SkillCreate(BaseModel):
    """Request body to author a skill (RF-08 structured fields)."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=100000)
    when_to_use: str = Field(default="", max_length=5000)
    sources: str = Field(default="", max_length=5000)
    steps: str = Field(default="", max_length=20000)
    output_format: str = Field(default="", max_length=5000)

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        """Keep the name filesystem-safe (it becomes a SKILL.md directory)."""
        cleaned = _SLUG_UNSAFE.sub("", v).strip()
        if not cleaned:
            raise ValueError("name must contain letters or digits")
        return cleaned


class SkillUpdate(BaseModel):
    """Request body to update a skill. Omitted fields are left unchanged."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    body: Optional[str] = Field(default=None, max_length=100000)
    when_to_use: Optional[str] = Field(default=None, max_length=5000)
    sources: Optional[str] = Field(default=None, max_length=5000)
    steps: Optional[str] = Field(default=None, max_length=20000)
    output_format: Optional[str] = Field(default=None, max_length=5000)

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: Optional[str]) -> Optional[str]:
        """Keep the name filesystem-safe; leave None untouched."""
        if v is None:
            return None
        cleaned = _SLUG_UNSAFE.sub("", v).strip()
        if not cleaned:
            raise ValueError("name must contain letters or digits")
        return cleaned


class SkillResponse(BaseModel):
    """Response model for a skill."""

    id: int
    name: str
    description: str
    body: str
    when_to_use: str = ""
    sources: str = ""
    steps: str = ""
    output_format: str = ""
    source: str


class AttachSkillsRequest(BaseModel):
    """Request body to set the skills attached to an agent."""

    skill_ids: List[int] = Field(default_factory=list)
