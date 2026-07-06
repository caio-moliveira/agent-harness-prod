"""The DocumentChunk model: one fragment of an ingested document, with provenance metadata.

Chunks are scoped to ``(user_id, agent_id)`` so a user's ingested corpus is isolated per agent
harness — the same isolation guarantee the rest of the product enforces. The vector embedding of
each chunk lands with #14 (semantic retrieval); this model holds the text + metadata only.
"""

from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field

from src.app.core.common.model.base import BaseModel


class DocumentChunk(BaseModel, table=True):
    """One fragment of an ingested document.

    Attributes:
        id: The primary key.
        user_id: Foreign key to the owning user.
        agent_id: Foreign key to the agent whose corpus this belongs to (nullable).
        source_path: Absolute path of the source document.
        doc_type: pdf | docx | xlsx | text.
        section: Location within the source (e.g. "page 3", "sheet Vendas", a heading).
        chunk_index: Zero-based order of this chunk within the document.
        content: The chunk text.
        meta: Extra metadata (author, needs_ocr, and future fields).
        embedding: The chunk's dense vector, populated by the indexing step (#14). None until
            indexed. Stored as JSON so it is portable across SQLite (tests) and Postgres; a
            pgvector column + ANN index is a production-scale optimization to layer on later.
        created_at: When the chunk was ingested (from ``BaseModel``).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    agent_id: Optional[int] = Field(default=None, foreign_key="agent.id", index=True)
    source_path: str = Field(index=True)
    doc_type: str = Field(index=True)
    section: str = Field(default="")
    chunk_index: int = Field(default=0)
    content: str = Field(default="")
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # none_as_null so an unindexed chunk is a real SQL NULL (not JSON 'null'), making the
    # "IS NULL / IS NOT NULL" indexing filters work across SQLite and Postgres.
    embedding: Optional[list] = Field(default=None, sa_column=Column(JSON(none_as_null=True)))
