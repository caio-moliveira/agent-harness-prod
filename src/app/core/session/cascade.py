"""Cascade deletion for a session: its messages, audit events, parked actions, and generated files.

Deleting a session must remove everything that session produced — Postgres also enforces the FK from
``chat_message``/``session_event`` to ``session``, so an un-cascaded delete would fail outright.
Order: delete the generated artifact files (best-effort, off the loop), then the child rows, then
the session row itself.
"""

import asyncio
import os

from src.app.core.common.logging import logger
from src.app.init import (
    chat_message_repository,
    pending_action_repository,
    session_event_repository,
    session_repository,
)


def _remove_file(path: str) -> None:
    """Best-effort delete of a generated artifact file (missing/permission errors are ignored)."""
    try:
        os.remove(path)
    except OSError:
        pass


async def delete_session_cascade(session_id: str) -> None:
    """Delete a session and everything it produced (messages, events, actions, artifact files)."""
    actions = await pending_action_repository.list_for_session(session_id)
    for action in actions:
        path = (action.payload or {}).get("path")
        if path:
            await asyncio.to_thread(_remove_file, path)

    messages = await chat_message_repository.delete_for_session(session_id)
    events = await session_event_repository.delete_for_session(session_id)
    await pending_action_repository.delete_for_session(session_id)
    await session_repository.delete_session(session_id)

    logger.info(
        "session_cascade_deleted",
        session_id=session_id,
        messages=messages,
        events=events,
        artifacts=len(actions),
    )
