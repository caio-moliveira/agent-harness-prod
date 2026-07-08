"""Folder ingestion orchestrator: parse → chunk → persist, scoped to (user_id, agent_id).

Parsing and file I/O run in worker threads (``asyncio.to_thread``) so a large corpus never blocks
the event loop for active users. A single unreadable file is logged and skipped rather than failing
the whole run. Embedding/indexing of the persisted chunks is #14.
"""

import asyncio
import os
from typing import List, Optional

from pydantic import BaseModel

from src.app.core.common.logging import logger
from src.app.core.ingestion.chunk_model import DocumentChunk
from src.app.core.ingestion.chunk_repository import DocumentChunkRepository
from src.app.core.ingestion.chunking import chunk_document
from src.app.core.ingestion.parsers import ParsedDocument, extract_document, is_supported


class IngestionResult(BaseModel):
    """Summary of a folder ingestion run."""

    files_ingested: int = 0
    files_skipped: int = 0
    chunks: int = 0


class IngestFileResult(BaseModel):
    """Per-file ingestion outcome: chunk count plus the document's manifest metadata."""

    chunk_count: int = 0
    page_count: int = 0
    text_layer: str = "native"  # native | ocr | mixed
    ocr_confidence: float = 1.0


def derive_manifest_meta(parsed: ParsedDocument) -> tuple[int, str, float]:
    """Derive ``(page_count, text_layer, ocr_confidence)`` from a parsed document.

    ``ocr_confidence`` is a heuristic — the fraction of sections (pages) that yielded extractable
    text — not a true OCR score; a real OCR pass can replace it later. ``text_layer`` summarizes it:
    all-text → ``native``, no-text → ``ocr``, some-text → ``mixed``.
    """
    sections = parsed.sections
    page_count = len(sections)
    if page_count == 0:
        return 0, "native", 1.0
    with_text = sum(1 for s in sections if s.text.strip())
    confidence = with_text / page_count
    if with_text == page_count:
        text_layer = "native"
    elif with_text == 0:
        text_layer = "ocr"
    else:
        text_layer = "mixed"
    return page_count, text_layer, confidence


def _list_supported_files(folder: str) -> List[str]:
    """Walk ``folder`` and return absolute paths of files with a registered parser."""
    found: List[str] = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            if is_supported(path):
                found.append(path)
    return sorted(found)


async def ingest_file(
    path: str,
    user_id: int,
    agent_id: Optional[int],
    repo: DocumentChunkRepository,
) -> IngestFileResult:
    """Parse one file into chunks persisted for (user, agent). Returns chunk count + manifest meta.

    Parsing runs in a worker thread so it never blocks the event loop. Raises on a parse error
    so the caller can decide whether to skip (folder ingest) or surface it (single-file sync).
    """
    parsed = await asyncio.to_thread(extract_document, path)
    chunk_datas = chunk_document(parsed)
    models = [
        DocumentChunk(
            user_id=user_id,
            agent_id=agent_id,
            source_path=cd.source_path,
            doc_type=cd.doc_type,
            section=cd.section,
            chunk_index=cd.chunk_index,
            content=cd.content,
            meta={"author": cd.author, "needs_ocr": cd.needs_ocr},
        )
        for cd in chunk_datas
    ]
    # Atomic swap (delete old + insert new in one transaction) so a re-ingest is idempotent and can
    # never leave the document with zero chunks if it is interrupted.
    await repo.replace_source(user_id, agent_id, path, models)
    page_count, text_layer, confidence = derive_manifest_meta(parsed)
    return IngestFileResult(
        chunk_count=len(models),
        page_count=page_count,
        text_layer=text_layer,
        ocr_confidence=confidence,
    )


async def ingest_folder(
    folder: str,
    user_id: int,
    agent_id: Optional[int] = None,
    repo: Optional[DocumentChunkRepository] = None,
) -> IngestionResult:
    """Parse every supported file under ``folder`` into chunks persisted for (user_id, agent_id)."""
    repo = repo or DocumentChunkRepository()
    files = await asyncio.to_thread(_list_supported_files, folder)

    ingested = 0
    skipped = 0
    total_chunks = 0
    for path in files:
        try:
            total_chunks += (await ingest_file(path, user_id, agent_id, repo)).chunk_count
            ingested += 1
        except Exception:  # noqa: BLE001 - one bad file must not abort the whole ingestion
            logger.exception("document_parse_failed", path=path, user_id=user_id, agent_id=agent_id)
            skipped += 1

    logger.info(
        "folder_ingested",
        folder=folder,
        user_id=user_id,
        agent_id=agent_id,
        files=ingested,
        skipped=skipped,
        chunks=total_chunks,
    )
    return IngestionResult(files_ingested=ingested, files_skipped=skipped, chunks=total_chunks)
