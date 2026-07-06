"""Chunking: split a parsed document into retrieval-sized fragments carrying provenance metadata.

Splitting respects paragraph boundaries where possible and never loses a section's location,
so every chunk can later be attributed to a page/sheet/heading and re-embedded independently (#14).
"""

from typing import List, Optional

from pydantic import BaseModel

from src.app.core.ingestion.parsers import ParsedDocument

_DEFAULT_MAX_CHARS = 1200


class ChunkData(BaseModel):
    """A single chunk plus the metadata needed to persist and, later, cite it."""

    source_path: str
    doc_type: str
    section: str
    chunk_index: int
    content: str
    author: Optional[str] = None
    needs_ocr: bool = False


def chunk_document(doc: ParsedDocument, max_chars: int = _DEFAULT_MAX_CHARS) -> List[ChunkData]:
    """Split a parsed document into ordered chunks with per-chunk provenance metadata."""
    chunks: List[ChunkData] = []
    index = 0
    for section in doc.sections:
        pieces = _split_text(section.text, max_chars)
        # A scanned (empty) page still yields one placeholder chunk so it is visible and re-ingestible.
        if not pieces and section.needs_ocr:
            pieces = [""]
        for piece in pieces:
            chunks.append(
                ChunkData(
                    source_path=doc.path,
                    doc_type=doc.doc_type,
                    section=section.location,
                    chunk_index=index,
                    content=piece,
                    author=doc.author,
                    needs_ocr=section.needs_ocr,
                )
            )
            index += 1
    return chunks


def _split_text(text: str, max_chars: int) -> List[str]:
    """Split text into <= max_chars pieces, preferring paragraph boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    pieces: List[str] = []
    buffer = ""
    for paragraph in text.split("\n"):
        candidate = f"{buffer}\n{paragraph}" if buffer else paragraph
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer:
            pieces.append(buffer)
        # A single paragraph longer than the limit is hard-split.
        while len(paragraph) > max_chars:
            pieces.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        buffer = paragraph
    if buffer:
        pieces.append(buffer)
    return pieces
