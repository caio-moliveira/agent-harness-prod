"""AgentMemory: the agent's two-tier "experience" memory (#23).

Records what the agent *did and decided* (deliverables generated, conclusions reached) — not just
what the user asked — so a new session knows the work already done and doesn't redo it. Two tiers,
progressive-disclosure like a ``MEMORY.md`` index over detail files:

- ``summary`` — a short line, the only field that is embedded; retrieval and the session-start
  briefing scan these.
- ``body`` / ``refs`` — the full detail (numbers, decisions, artifact paths/doc_ids), read on demand
  via ``ler_memoria(id)`` only when a summary is relevant.

Scoped per ``(user_id, agent_id)`` for the same isolation as the rest of the product. The embedding
is stored as JSON (like ``DocumentChunk``) and searched with in-memory cosine; a pgvector + ANN
column is a production-scale optimization to layer on later.
"""

from typing import Optional

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field

from src.app.core.common.model.base import BaseModel


class AgentMemoryKind:
    """The kinds of experience entries captured."""

    OUTCOME = "outcome"  # a deliverable was produced (artifact, report, spreadsheet)
    DECISION = "decision"  # a choice/conclusion the agent or user settled on
    FACT = "fact"  # a durable fact worth recalling


class AgentMemory(BaseModel, table=True):
    """One two-tier memory entry: an embedded ``summary`` (tier 1) plus a full ``body`` (tier 2)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    agent_id: Optional[int] = Field(default=None, foreign_key="agent.id", index=True)
    # Provenance only (which session produced it), NOT a FK: long-term memory must outlive the
    # session that created it — a deleted session must never cascade-delete or block the memory.
    session_id: Optional[str] = Field(default=None, index=True)
    kind: str = Field(default=AgentMemoryKind.OUTCOME, index=True)
    summary: str = Field(sa_column=Column(Text))  # tier 1 — embedded, scanned at session start
    body: dict = Field(default_factory=dict, sa_column=Column(JSON))  # tier 2 — read on demand
    refs: dict = Field(default_factory=dict, sa_column=Column(JSON))  # artifact paths, doc_ids, …
    # none_as_null so an un-embedded row is a real SQL NULL (mirrors DocumentChunk).
    embedding: Optional[list] = Field(default=None, sa_column=Column(JSON(none_as_null=True)))
