"""In-memory, per-session registry of data sources (DB engine + granted folder + agent).

Credentials live ONLY here, in process memory, keyed by session_id. They are never
persisted to disk, never written to the LangGraph checkpoint/state, and never logged
(the logging layer additionally redacts sensitive keys). A source is torn down on
explicit disconnect; a background reaper evicts idle sessions after a TTL, disposing the
DB engine and freeing the session's resources.

The granted folder is served to the agent's read-only file tools by a per-session
``FilesystemBackend`` (see ``src/app/core/sandbox/backend.py``); the registry only stores the
authorized folder path — there is no per-session container to create or remove.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

from langchain_community.utilities import SQLDatabase

from src.app.core.common.config import settings
from src.app.core.common.logging import logger


@dataclass
class SessionResources:
    """Live resources attached to a single chat session."""

    session_id: str
    db: Optional[SQLDatabase] = None
    db_dialect: Optional[str] = None
    folder: Optional[str] = None  # authorized host folder, served read-only via FilesystemBackend
    agent: Optional[Any] = None  # compiled DataAgent, rebuilt when a source changes
    last_used: float = 0.0

    @property
    def has_source(self) -> bool:
        """True when at least one data source (DB or folder) is attached."""
        return self.db is not None or self.folder is not None


class SessionRegistry:
    """Async-safe registry mapping session_id -> SessionResources."""

    def __init__(self, ttl_seconds: int) -> None:
        """Create an empty registry whose idle sessions are reaped after ``ttl_seconds``."""
        self._items: dict[str, SessionResources] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds

    async def get(self, session_id: str) -> Optional[SessionResources]:
        """Return the resources for a session, if any, refreshing its last-used time."""
        async with self._lock:
            res = self._items.get(session_id)
            if res is not None:
                res.last_used = time.time()
            return res

    async def ensure(self, session_id: str) -> SessionResources:
        """Return the resources for a session, creating an empty entry if needed."""
        async with self._lock:
            return await self._get_or_create(session_id)

    async def _get_or_create(self, session_id: str) -> SessionResources:
        res = self._items.get(session_id)
        if res is None:
            res = SessionResources(session_id=session_id)
            self._items[session_id] = res
        res.last_used = time.time()
        return res

    async def set_database(self, session_id: str, db: SQLDatabase, dialect: str) -> SessionResources:
        """Attach a connected database, disposing any previous engine."""
        async with self._lock:
            res = await self._get_or_create(session_id)
            await self._dispose_db(res)
            res.db = db
            res.db_dialect = dialect
            res.agent = None  # force rebuild with the new source
            return res

    async def set_folder(self, session_id: str, folder: str) -> SessionResources:
        """Attach a granted folder (served read-only via the session's FilesystemBackend)."""
        async with self._lock:
            res = await self._get_or_create(session_id)
            res.folder = folder
            res.agent = None  # force rebuild so the agent picks up the new folder
            return res

    async def disconnect(self, session_id: str) -> None:
        """Tear down all resources for a session (dispose the DB engine, drop the folder)."""
        async with self._lock:
            res = self._items.pop(session_id, None)
        if res is None:
            return
        await self._dispose_db(res)
        res.folder = None
        res.agent = None
        logger.info("session_sources_disconnected", session_id=session_id)

    async def reap_idle(self) -> None:
        """Evict sessions idle for longer than the TTL. Runs in a background loop."""
        now = time.time()
        async with self._lock:
            stale = [sid for sid, res in self._items.items() if now - res.last_used > self._ttl]
        for sid in stale:
            logger.info("session_sources_reaped", session_id=sid)
            await self.disconnect(sid)

    async def _dispose_db(self, res: SessionResources) -> None:
        if res.db is None:
            return
        engine = getattr(res.db, "_engine", None)
        if engine is not None:
            try:
                await asyncio.to_thread(engine.dispose)
            except Exception:  # noqa: BLE001
                logger.warning("db_engine_dispose_failed", session_id=res.session_id)
        res.db = None
        res.db_dialect = None


registry = SessionRegistry(ttl_seconds=settings.SESSION_SOURCE_TTL)


async def reaper_loop() -> None:
    """Background task: periodically reap idle session sources."""
    while True:
        await asyncio.sleep(settings.SESSION_SOURCE_TTL)
        try:
            await registry.reap_idle()
        except Exception:  # noqa: BLE001
            logger.warning("session_reaper_iteration_failed")
