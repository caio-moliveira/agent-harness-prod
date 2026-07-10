"""Incremental folder sync (#15): re-ingest only what changed since last time.

Compares the live files under a folder against per-file content hashes tracked in ``IngestedFile``:
new/changed files are re-parsed and re-chunked (old chunks dropped first), removed files have their
chunks purged, and unchanged files are skipped. New chunks are then embedded. Hashing and parsing
run in worker threads so a large re-sync never blocks active users.
"""

import asyncio
import hashlib
from typing import Optional

from pydantic import BaseModel

import os

from src.app.core.common.logging import logger
from src.app.core.ingestion.chunk_repository import DocumentChunkRepository
from src.app.core.ingestion.describe import describe_file
from src.app.core.ingestion.ingest import _list_supported_files, ingest_file
from src.app.core.ingestion.source_model import IngestedFileStatus
from src.app.core.ingestion.source_repository import IngestedFileRepository
from src.app.core.retrieval.embedding import Embedder
from src.app.core.retrieval.indexing import index_chunks


class SyncResult(BaseModel):
    """Summary of an incremental sync run."""

    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    chunks_indexed: int = 0


_PREVIEW_CHARS = 4000


def _hash_file(path: str) -> str:
    """Content hash of a file (sha256), read in blocks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


async def sync_folder(
    folder: str,
    user_id: int,
    agent_id: Optional[int],
    embedder: Embedder,
    chunk_repo: Optional[DocumentChunkRepository] = None,
    file_repo: Optional[IngestedFileRepository] = None,
) -> SyncResult:
    """Reconcile a folder's current files with what was ingested before, re-indexing the delta."""
    chunk_repo = chunk_repo or DocumentChunkRepository()
    file_repo = file_repo or IngestedFileRepository()

    files = await asyncio.to_thread(_list_supported_files, folder)
    known = await file_repo.get_known(user_id, agent_id)
    current = set(files)
    result = SyncResult()

    for path in files:
        digest = await asyncio.to_thread(_hash_file, path)
        record = known.get(path)
        if record is not None and record.content_hash == digest:
            # Unchanged by hash — but self-heal a wiped corpus: if the manifest says this doc is
            # ingested while its chunks are gone (e.g. an earlier sync was interrupted), re-ingest it
            # instead of skipping it forever.
            if await chunk_repo.count_by_source(user_id, agent_id, path) > 0:
                # Backfill the map description (#23) for files ingested before this feature (or if a
                # previous describe failed) — from their existing chunks, without re-parsing.
                if not record.description:
                    chunks = await chunk_repo.get_chunks_by_source(user_id, agent_id, path)
                    preview = "\n".join(c.content for c in chunks if c.content.strip())[:_PREVIEW_CHARS]
                    desc = await describe_file(os.path.basename(path), preview)
                    if desc:
                        await file_repo.set_description(user_id, agent_id, path, desc)
                result.unchanged += 1
                continue
            logger.warning("sync_repairing_missing_chunks", user_id=user_id, agent_id=agent_id, source_path=path)
        # New, changed, or repairing: (re)ingest. ingest_file swaps this source's chunks atomically,
        # so a parse failure here leaves any existing chunks intact (never a zero-chunk dead state).
        try:
            outcome = await ingest_file(path, user_id, agent_id, chunk_repo)
        except Exception:  # noqa: BLE001 - one bad file must not abort the whole sync
            logger.exception("sync_parse_failed", path=path, user_id=user_id, agent_id=agent_id)
            continue
        # Generate the map description (#23) once, only for this new/changed file (never for the
        # unchanged files skipped above). describe_file never raises — a missing blurb is fine.
        description = await describe_file(os.path.basename(path), outcome.text_preview)
        await file_repo.upsert(
            user_id,
            agent_id,
            path,
            digest,
            outcome.chunk_count,
            page_count=outcome.page_count,
            text_layer=outcome.text_layer,
            ocr_confidence=outcome.ocr_confidence,
            description=description,
            status=IngestedFileStatus.ACTIVE,
            structure=outcome.structure,
        )
        if record is None:
            result.added += 1
        else:
            result.updated += 1

    # Files that vanished from the folder: purge their chunks and tracking — but NEVER on an empty
    # listing while the manifest still has entries. A transiently unreadable/wrong folder returns no
    # files, and treating that as "everything was deleted" would wipe the whole corpus.
    if files or not known:
        for path, record in known.items():
            if path not in current and record.status != IngestedFileStatus.DELETED:
                # Soft-delete: purge the chunks (no longer searchable) but keep the manifest row
                # marked ``deleted`` so the map remembers the file existed.
                await chunk_repo.delete_by_source(user_id, agent_id, path)
                await file_repo.mark_deleted(user_id, agent_id, path)
                result.removed += 1
    else:
        logger.warning("sync_skipped_purge_empty_listing", user_id=user_id, agent_id=agent_id, known=len(known))

    # Embed whatever is now pending (the added/updated chunks); unchanged stay as-is.
    result.chunks_indexed = await index_chunks(user_id, agent_id, embedder, repo=chunk_repo)

    logger.info(
        "folder_synced",
        folder=folder,
        user_id=user_id,
        agent_id=agent_id,
        added=result.added,
        updated=result.updated,
        removed=result.removed,
        unchanged=result.unchanged,
        chunks_indexed=result.chunks_indexed,
    )
    return result
