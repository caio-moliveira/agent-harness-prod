"""The Skill model: a per-user instruction document (SKILL.md), never executable code."""

from datetime import UTC, datetime

from sqlmodel import Field

from src.app.core.common.model.base import BaseModel


class Skill(BaseModel, table=True):
    """A reusable instruction document owned by a user.

    Attributes:
        id: The primary key.
        user_id: Foreign key to the owning user.
        name: Short skill name (used for its SKILL.md directory).
        description: One-line description surfaced for progressive disclosure.
        body: The markdown instructions (the SKILL.md body).
        source: How it was created — ``authored`` or ``fetched``.
        created_at: When the skill was created (from ``BaseModel``).
        updated_at: When the skill was last changed.
    """

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str = Field(index=True)
    description: str = Field(default="")
    body: str = Field(default="")
    source: str = Field(default="authored")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
