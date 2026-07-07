"""The Skill model: a per-user instruction document (SKILL.md), never executable code.

Skills are versioned and approval-gated (#17): every content edit snapshots a ``SkillVersion`` and
resets the skill to ``draft``, and only ``approved`` skills are ever loaded into an agent.
"""

from datetime import UTC, datetime
from typing import Optional

from sqlmodel import Field

from src.app.core.common.model.base import BaseModel


class Skill(BaseModel, table=True):
    """A reusable instruction document owned by a user.

    Attributes:
        id: The primary key.
        user_id: Foreign key to the owning user.
        name: Short skill name (used for its SKILL.md directory).
        description: One-line description surfaced for progressive disclosure.
        body: The markdown instructions (the SKILL.md body / free-form notes).
        when_to_use: When the agent should reach for this skill.
        sources: The data sources the skill needs (folder docs, DB queries).
        steps: The step-by-step reasoning the skill prescribes.
        output_format: The expected shape of the produced artifact/answer.
        status: Review state — ``draft`` | ``in_review`` | ``approved``. Only ``approved`` loads.
        version: Current revision number (bumped on each content edit).
        source: How it was created — ``authored`` or ``fetched``.
        created_at: When the skill was created (from ``BaseModel``).
        updated_at: When the skill was last changed.
    """

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str = Field(index=True)
    description: str = Field(default="")
    body: str = Field(default="")
    when_to_use: str = Field(default="")
    sources: str = Field(default="")
    steps: str = Field(default="")
    output_format: str = Field(default="")
    status: str = Field(default="draft", index=True)
    version: int = Field(default=1)
    source: str = Field(default="authored")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SkillVersion(BaseModel, table=True):
    """An immutable content snapshot of a skill at one revision (audit + history)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    skill_id: int = Field(foreign_key="skill.id", index=True)
    version: int = Field(default=1)
    name: str = Field(default="")
    description: str = Field(default="")
    body: str = Field(default="")
    when_to_use: str = Field(default="")
    sources: str = Field(default="")
    steps: str = Field(default="")
    output_format: str = Field(default="")
    status: str = Field(default="draft")
