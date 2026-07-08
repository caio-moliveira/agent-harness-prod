"""Fire-and-forget folder ingestion: parse + embed a granted folder into the searchable manifest.

Triggered when a folder is granted to a session or bound to an agent, so semantic search and the
document tools have a populated corpus without a manual step. It runs in the background (never
blocks the grant response), is guarded against concurrent runs for the same ``(user, agent)``, and
swallows errors — a failed ingestion degrades to "no corpus", it never breaks the grant flow.

Ingestion is incremental (``sync_folder`` compares content hashes), so re-granting an unchanged
folder is cheap and picks up only new/changed/removed files.
"""

from typing import Optional

from fastapi import BackgroundTasks

from src.app.core.common.logging import logger
from src.app.core.ingestion.sync import sync_folder
from src.app.core.retrieval.embedding import get_default_embedder

# (user_id, agent_id) scopes currently ingesting — so a second grant doesn't launch a racing sync.
# Single-event-loop mutation with no await between check and add, so a plain set is race-free here.
_in_flight: set[tuple[int, Optional[int]]] = set()


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
