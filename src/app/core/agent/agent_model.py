"""The Agent model: a persisted, user-owned agent configuration.

The ``config`` JSON column holds capability toggles and source bindings that later
vertical slices fill in (bound database, bound folder, attached skills, web-search and
memory toggles). This walking-skeleton slice uses only ``name`` and ``system_prompt``.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Column
from sqlmodel import Field

from src.app.core.common.model.base import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    pass


class Agent(BaseModel, table=True):
    """A user-configurable agent harness, isolated per owner.

    Attributes:
        id: The primary key.
        user_id: Foreign key to the owning user.
        name: Human-friendly agent name.
        system_prompt: The agent's system prompt (empty falls back to the runtime default).
        config: JSON payload for capability toggles and source bindings (later slices).
        created_at: When the agent was created (from ``BaseModel``).
        updated_at: When the agent config was last changed.
    """

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str = Field(index=True)
    system_prompt: str = Field(default="")
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
