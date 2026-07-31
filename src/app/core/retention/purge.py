"""Data retention and the LGPD right to erasure (#75).

Two operations that look similar and must not be confused:

- :func:`purge_expired` — **time-based housekeeping**. Deletes data older than the configured
  windows so the database doesn't grow without bound. Every window defaults to 0 (keep forever):
  silently deleting a user's conversations because a default said so would be worse than a large
  table, so retention is something an operator turns on deliberately.
- :func:`delete_user_data` — **erasure on request**. Removes everything belonging to one user, in
  the order the foreign keys require. This is the routine that answers an LGPD deletion request,
  and it is exercised by tests precisely because "we can delete your data" is a claim that must be
  true, not aspirational.

Both build on :func:`delete_session_cascade`, which already knows how to take a session apart
(messages, steps, events, parked actions, artifact files, checkpoint thread).
"""

import asyncio
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlmodel import delete, select

from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.memory.agent_memory_model import AgentMemory
from src.app.core.session.cascade import delete_session_cascade
from src.app.core.session.session_model import Session
from src.app.core.usage.usage_model import TokenUsage


@dataclass
class PurgeReport:
    """What a purge/erasure run actually removed — logged and returned for the operator."""

    sessions: int = 0
    memories: int = 0
    usage_rows: int = 0
    artifact_dirs: int = 0
    users: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """Flat dict for structured logging."""
        return {
            "sessions": self.sessions,
            "memories": self.memories,
            "usage_rows": self.usage_rows,
            "artifact_dirs": self.artifact_dirs,
            "users": self.users,
            "errors": len(self.errors),
        }


def _cutoff(days: int) -> datetime | None:
    """The timestamp before which data expires, or None when the window is disabled (0)."""
    return datetime.now(UTC) - timedelta(days=days) if days > 0 else None


def _expired_session_ids(cutoff: datetime) -> list[str]:
    with session_scope() as db:
        rows = db.exec(select(Session.id).where(Session.created_at < cutoff)).all()
        return [r for r in rows]


def _delete_expired_usage(cutoff: datetime) -> int:
    with session_scope() as db:
        result = db.exec(delete(TokenUsage).where(TokenUsage.day < cutoff.date()))
        db.commit()
        return result.rowcount or 0


def _purge_artifact_dirs(older_than_days: int) -> int:
    """Remove per-session artifact directories untouched for longer than the window.

    Artifacts written to the managed root outlive their session's rows only by accident (a failed
    cascade, a crash mid-delete). Sweeping by mtime keeps the volume from filling with orphans —
    the failure mode behind the "disk full" runbook.
    """
    root = settings.ARTIFACT_STORAGE_ROOT
    if not os.path.isdir(root):
        return 0
    deadline = time.time() - older_than_days * 86_400
    removed = 0
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < deadline:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


async def purge_expired() -> PurgeReport:
    """Delete data past its retention window. A disabled window (0) is skipped entirely."""
    report = PurgeReport()

    message_cutoff = _cutoff(settings.RETENTION_MESSAGES_DAYS)
    if message_cutoff:
        # Conversations are removed whole (via the session cascade) rather than by trimming
        # messages: a session whose messages vanished but whose row remains would show up in the
        # sidebar as an empty, unexplained conversation.
        for session_id in await asyncio.to_thread(_expired_session_ids, message_cutoff):
            try:
                await delete_session_cascade(session_id)
                report.sessions += 1
            except Exception as exc:  # keep going: one bad session must not stop the sweep
                report.errors.append(f"session {session_id}: {exc}")
                logger.warning("retention_session_purge_failed", session_id=session_id, error=str(exc))

    usage_cutoff = _cutoff(settings.RETENTION_USAGE_DAYS)
    if usage_cutoff:
        report.usage_rows = await asyncio.to_thread(_delete_expired_usage, usage_cutoff)

    if settings.RETENTION_ARTIFACTS_DAYS > 0:
        report.artifact_dirs = await asyncio.to_thread(
            _purge_artifact_dirs, settings.RETENTION_ARTIFACTS_DAYS
        )

    logger.info("retention_purge_completed", **report.as_dict())
    return report


async def delete_user_data(user_id: int, delete_account: bool = True) -> PurgeReport:
    """Erase everything belonging to one user (LGPD right to erasure).

    Order matters: sessions cascade first (they own messages, events, actions, artifacts and the
    checkpoint thread), then the user-scoped tables, then the account row itself. Pass
    ``delete_account=False`` to wipe the data while keeping the login (an "erase my history"
    request rather than a full account deletion).
    """
    report = PurgeReport()

    sessions = await asyncio.to_thread(_user_session_ids, user_id)
    for session_id in sessions:
        try:
            await delete_session_cascade(session_id)
            report.sessions += 1
        except Exception as exc:
            report.errors.append(f"session {session_id}: {exc}")
            logger.warning("erasure_session_failed", session_id=session_id, error=str(exc))

    report.memories = await asyncio.to_thread(_delete_user_rows, AgentMemory, user_id)
    report.usage_rows = await asyncio.to_thread(_delete_user_rows, TokenUsage, user_id)
    report.artifact_dirs = await asyncio.to_thread(_remove_session_artifact_dirs, sessions)

    if delete_account:
        report.users = await asyncio.to_thread(_delete_user_row, user_id)

    logger.info("user_data_erased", user_id=user_id, delete_account=delete_account, **report.as_dict())
    return report


def _user_session_ids(user_id: int) -> list[str]:
    with session_scope() as db:
        return list(db.exec(select(Session.id).where(Session.user_id == user_id)).all())


def _delete_user_rows(model, user_id: int) -> int:
    """Delete every row of ``model`` owned by the user (model must have ``user_id``)."""
    with session_scope() as db:
        result = db.exec(delete(model).where(model.user_id == user_id))
        db.commit()
        return result.rowcount or 0


def _remove_session_artifact_dirs(session_ids: list[str]) -> int:
    """Remove the managed artifact directory of each session (files outside it are untouched)."""
    removed = 0
    for session_id in session_ids:
        path = os.path.join(settings.ARTIFACT_STORAGE_ROOT, str(session_id))
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def _delete_user_row(user_id: int) -> int:
    from src.app.core.user.user_model import User

    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            return 0
        db.delete(user)
        db.commit()
        return 1
