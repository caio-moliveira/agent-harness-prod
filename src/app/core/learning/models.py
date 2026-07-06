"""Continuous-learning models (#20): correction signals and derived per-agent preferences.

A ``CorrectionSignal`` records that a generated artifact needed manual correction (RF-19) — the
raw material for a *proposed* skill refinement, which always goes through the #17 approval gate
(never auto-applied). ``AgentPreference`` holds format/pattern preferences reflected from the
episodic event log (RF-18).
"""

from typing import Optional

from sqlmodel import Field

from src.app.core.common.model.base import BaseModel


class CorrectionSignal(BaseModel, table=True):
    """A captured signal that a produced artifact required manual correction."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    agent_id: Optional[int] = Field(default=None, foreign_key="agent.id", index=True)
    skill_id: Optional[int] = Field(default=None, foreign_key="skill.id", index=True)
    artifact_ref: str = Field(default="")
    note: str = Field(default="")


class AgentPreference(BaseModel, table=True):
    """One reflected preference for an agent (e.g. preferred_output_format=docx)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    agent_id: Optional[int] = Field(default=None, foreign_key="agent.id", index=True)
    key: str = Field(index=True)
    value: str = Field(default="")
