"""Incremental folder sync (#15): re-ingest only what changed since last time.

Compares the live files under a folder against per-file content hashes tracked in ``IngestedFile``:
new/changed files are re-parsed and their structure tree + located text rebuilt, removed files are
soft-deleted, unchanged files are skipped. Hashing and parsing run in worker threads so a large
re-sync never blocks active users. The corpus is vectorless — no chunks, no embeddings.
"""

import asyncio
import hashlib
import os
from typing import Optional

from pydantic import BaseModel

from src.app.core.common.logging import logger
from src.app.core.ingestion.describe import describe_file
from src.app.core.ingestion.ingest import _list_supported_files, ingest_file
from src.app.core.ingestion.source_model import IngestedFileStatus
from src.app.core.ingestion.source_repository import IngestedFileRepository


class SyncResult(BaseModel):
    """Summary of an incremental sync run."""

    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0


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
    file_repo: Optional[IngestedFileRepository] = None,
) -> SyncResult:
    """Reconcile a folder's current files with what was ingested before, re-indexing the delta."""
    file_repo = file_repo or IngestedFileRepository()

    files = await asyncio.to_thread(_list_supported_files, folder)
    known = await file_repo.get_known(user_id, agent_id)
    current = set(files)
    result = SyncResult()

    for path in files:
        digest = await asyncio.to_thread(_hash_file, path)
        record = known.get(path)
        # Unchanged by hash AND already indexed by the current (content) pipeline → skip. A row from
        # before the content column (content is NULL) is re-ingested so it gets a structure + text.
        if record is not None and record.content_hash == digest and record.content is not None:
            result.unchanged += 1
            continue
        # New, changed, or a pre-content-era row: (re)ingest into the manifest.
        try:
            outcome = await ingest_file(path)
        except Exception:  # noqa: BLE001 - one bad file must not abort the whole sync
            logger.exception("sync_parse_failed", path=path, user_id=user_id, agent_id=agent_id)
            continue
        # Generate the map description (#23) for this new/changed file. describe_file never raises.
        description = await describe_file(os.path.basename(path), outcome.text_preview)
        await file_repo.upsert(
            user_id,
            agent_id,
            path,
            digest,
            page_count=outcome.page_count,
            text_layer=outcome.text_layer,
            ocr_confidence=outcome.ocr_confidence,
            description=description,
            status=IngestedFileStatus.ACTIVE,
            structure=outcome.structure,
            content=outcome.content,
        )
        if record is None:
            result.added += 1
        else:
            result.updated += 1

    # Files that vanished from the folder: soft-delete their manifest row — but NEVER on an empty
    # listing while the manifest still has entries. A transiently unreadable/wrong folder returns no
    # files, and treating that as "everything was deleted" would wipe the whole corpus.
    if files or not known:
        for path, record in known.items():
            if path not in current and record.status != IngestedFileStatus.DELETED:
                await file_repo.mark_deleted(user_id, agent_id, path)
                result.removed += 1
    else:
        logger.warning("sync_skipped_purge_empty_listing", user_id=user_id, agent_id=agent_id, known=len(known))

    logger.info(
        "folder_synced",
        folder=folder,
        user_id=user_id,
        agent_id=agent_id,
        added=result.added,
        updated=result.updated,
        removed=result.removed,
        unchanged=result.unchanged,
    )
    return result
