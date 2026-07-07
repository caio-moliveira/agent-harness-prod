"""Tracks each ingested source file so incremental re-ingestion (#15) can tell what changed.

One row per (user, agent, source_path), holding a content hash. On a folder sync we compare the
live files against these rows to decide what to add, re-ingest, or delete — without reprocessing
the whole corpus.
"""

from typing import Optional

from sqlmodel import Field

from src.app.core.common.model.base import BaseModel


class IngestedFile(BaseModel, table=True):
    """A record of one ingested source file and its content hash, scoped per (user, agent)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    agent_id: Optional[int] = Field(default=None, foreign_key="agent.id", index=True)
    source_path: str = Field(index=True)
    content_hash: str = Field(default="")
    chunk_count: int = Field(default=0)
