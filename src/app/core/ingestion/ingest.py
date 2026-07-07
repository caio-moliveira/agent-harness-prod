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
from src.app.core.ingestion.parsers import extract_document, is_supported


class IngestionResult(BaseModel):
    """Summary of a folder ingestion run."""

    files_ingested: int = 0
    files_skipped: int = 0
    chunks: int = 0


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
) -> int:
    """Parse one file into chunks persisted for (user, agent). Returns the chunk count.

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
    await repo.add_chunks(models)
    return len(models)


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
            total_chunks += await ingest_file(path, user_id, agent_id, repo)
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
