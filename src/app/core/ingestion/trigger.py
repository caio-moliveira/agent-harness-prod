"""Fire-and-forget folder ingestion: parse + embed a granted folder into the searchable manifest.

Triggered when a folder is granted to a session or bound to an agent, so semantic search and the
document tools have a populated corpus without a manual step. It runs in the background (never
blocks the grant response), is guarded against concurrent runs for the same ``(user, agent)``, and
swallows errors — a failed ingestion degrades to "no corpus", it never breaks the grant flow.

Ingestion is incremental (``sync_folder`` compares content hashes), so re-granting an unchanged
folder is cheap and picks up only new/changed/removed files.
"""

import asyncio
import hashlib
import os
from typing import Optional

from fastapi import BackgroundTasks

from src.app.core.common.logging import logger
from src.app.core.ingestion.ingest import _list_supported_files
from src.app.core.ingestion.sync import sync_folder
from src.app.core.retrieval.embedding import get_default_embedder

# (user_id, agent_id) scopes currently ingesting — so a second grant doesn't launch a racing sync.
# Single-event-loop mutation with no await between check and add, so a plain set is race-free here.
_in_flight: set[tuple[int, Optional[int]]] = set()

# (user_id, agent_id) -> last folder signature synced, so a session-start sync is skipped when the
# folder is unchanged (avoids re-hashing/re-describing every session and racing the doc tools).
_last_signature: dict[tuple[int, Optional[int]], str] = {}


def _folder_signature(folder: str) -> str:
    """A cheap fingerprint of the folder's supported files (path + mtime + size).

    Changes iff a supported file is added, removed, or modified — so it detects exactly the cases a
    sync needs to run for, without reading file contents.
    """
    parts = []
    for path in _list_supported_files(folder):
        try:
            st = os.stat(path)
            parts.append(f"{path}|{st.st_mtime_ns}|{st.st_size}")
        except OSError:
            parts.append(f"{path}|?")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


async def run_folder_ingestion_if_changed(user_id: int, agent_id: Optional[int], folder: str) -> None:
    """Sync only when the folder changed since the last sync for this (user, agent).

    Used on the session-start path: computing the signature is cheap (stat only), so an unchanged
    folder costs nothing and never re-ingests/re-describes or races the document tools. The grant
    path still uses ``run_folder_ingestion`` directly (it always wants to sync).
    """
    key = (user_id, agent_id)
    try:
        signature = await asyncio.to_thread(_folder_signature, folder)
    except Exception:  # noqa: BLE001 - signature failure must never break the turn
        signature = None
    if signature is not None and _last_signature.get(key) == signature:
        return  # unchanged — skip
    await run_folder_ingestion(user_id, agent_id, folder)
    if signature is not None:
        _last_signature[key] = signature


def is_ingesting(user_id: int, agent_id: Optional[int]) -> bool:
    """Whether a background ingestion is currently running for this (user, agent) corpus."""
    return (user_id, agent_id) in _in_flight


async def run_folder_ingestion(user_id: int, agent_id: Optional[int], folder: str) -> None:
    """Sync a granted folder into the (user, agent) corpus. Safe to fire-and-forget."""
    key = (user_id, agent_id)
    if key in _in_flight:
        logger.info("folder_ingestion_skipped_in_flight", user_id=user_id, agent_id=agent_id)
        return
    _in_flight.add(key)
    try:
        result = await sync_folder(folder, user_id, agent_id, get_default_embedder())
        logger.info(
            "folder_ingestion_done",
            user_id=user_id,
            agent_id=agent_id,
            folder=folder,
            added=result.added,
            updated=result.updated,
            removed=result.removed,
            unchanged=result.unchanged,
            chunks_indexed=result.chunks_indexed,
        )
    except Exception:  # noqa: BLE001 - a failed ingestion must never break the grant flow
        logger.exception("folder_ingestion_failed", user_id=user_id, agent_id=agent_id, folder=folder)
    finally:
        _in_flight.discard(key)


def schedule_folder_ingestion(
    background: BackgroundTasks, user_id: int, agent_id: Optional[int], folder: str
) -> None:
    """Queue a background folder ingestion to run after the current request's response is sent."""
    background.add_task(run_folder_ingestion, user_id, agent_id, folder)
