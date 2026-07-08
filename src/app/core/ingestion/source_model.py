"""Tracks each ingested source file so incremental re-ingestion (#15) can tell what changed.

One row per (user, agent, source_path), holding a content hash. On a folder sync we compare the
live files against these rows to decide what to add, re-ingest, or delete — without reprocessing
the whole corpus. This row is also the **document manifest**: the document-facing tools
(``list_documents`` etc.) read catalog metadata (stable id, page count, text-layer state) from
here rather than touching disk.
"""

from typing import Optional

from sqlmodel import Field

from src.app.core.common.model.base import BaseModel


def derive_doc_id(content_hash: str) -> str:
    """A stable, ASCII, tool-friendly id for a document, derived from its content hash.

    Content-addressed: the id is stable while the file's bytes are unchanged, and changes when the
    file is edited (a new content == a new document, by design). ASCII-only so it can be passed
    between tools without the encoding hazards a human title (cedilla, accents) would carry.
    """
    return f"doc_{content_hash[:12]}" if content_hash else ""


class IngestedFile(BaseModel, table=True):
    """A record of one ingested source file: its content hash plus manifest metadata.

    Scoped per (user, agent). ``content_hash``/``chunk_count`` drive incremental re-ingestion;
    ``doc_id``/``title``/``page_count``/``text_layer``/``ocr_confidence`` are the catalog fields the
    document tools expose (the id circulates between tools; the title is display-only).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    agent_id: Optional[int] = Field(default=None, foreign_key="agent.id", index=True)
    source_path: str = Field(index=True)
    content_hash: str = Field(default="")
    chunk_count: int = Field(default=0)
    # Manifest / catalog metadata (populated at ingest; read by the document tools).
    doc_id: str = Field(default="", index=True)
    title: str = Field(default="")  # display-only (e.g. the file name); never a tool parameter
    page_count: int = Field(default=0)
    text_layer: str = Field(default="native")  # native | ocr | mixed
    ocr_confidence: float = Field(default=1.0)  # heuristic: fraction of pages with extractable text
