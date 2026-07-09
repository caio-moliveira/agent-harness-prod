"""Write + read the agent's two-tier experience memory (#23).

Writing embeds only the short ``summary`` (tier 1); the ``body``/``refs`` ride along un-embedded and
are read on demand. Searching runs in-memory cosine over the summary vectors — the same brute-force
approach as document retrieval (pgvector + ANN is a later optimization). All functions are
best-effort: a memory failure must never break a turn.
"""

import asyncio
from typing import Optional

from src.app.core.common.logging import logger
from src.app.core.memory.agent_memory_model import AgentMemory, AgentMemoryKind
from src.app.core.memory.agent_memory_repository import AgentMemoryRepository
from src.app.core.retrieval.embedding import Embedder, get_default_embedder
from src.app.core.retrieval.retriever import _cosine

_repo = AgentMemoryRepository()


class ScoredMemory:
    """A search hit: the memory entry plus its similarity score."""

    def __init__(self, memory: AgentMemory, score: float):
        """Bind an entry to its similarity score."""
        self.memory = memory
        self.score = score


async def record_memory(
    user_id: int,
    agent_id: Optional[int],
    session_id: Optional[str],
    kind: str,
    summary: str,
    body: Optional[dict] = None,
    refs: Optional[dict] = None,
    embedder: Optional[Embedder] = None,
) -> Optional[int]:
    """Embed ``summary`` and persist a memory entry; returns its id, or None on failure."""
    if not summary or not summary.strip():
        return None
    try:
        embedder = embedder or get_default_embedder()
        vector = await embedder.embed_query(summary)
        row = AgentMemory(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            kind=kind,
            summary=summary.strip(),
            body=body or {},
            refs=refs or {},
            embedding=vector,
        )
        memory_id = await _repo.add(row)
        logger.info("agent_memory_recorded", user_id=user_id, agent_id=agent_id, kind=kind, memory_id=memory_id)
        return memory_id
    except Exception:
        logger.exception("record_memory_failed", user_id=user_id, agent_id=agent_id, kind=kind)
        return None


def bg_record_memory(
    user_id: int,
    agent_id: Optional[int],
    session_id: Optional[str],
    kind: str,
    summary: str,
    body: Optional[dict] = None,
    refs: Optional[dict] = None,
) -> None:
    """Record a memory in the background (fire-and-forget), off the response path."""
    asyncio.create_task(record_memory(user_id, agent_id, session_id, kind, summary, body, refs))


async def search_memory(
    user_id: int,
    agent_id: Optional[int],
    query: str,
    k: int = 5,
    embedder: Optional[Embedder] = None,
) -> list[ScoredMemory]:
    """Return the top-``k`` entries whose summary is most similar to ``query`` (tier-1 search)."""
    rows = await _repo.get_embedded(user_id, agent_id)
    if not rows:
        return []
    qvec = await (embedder or get_default_embedder()).embed_query(query)
    scored = [ScoredMemory(r, _cosine(qvec, r.embedding or [])) for r in rows]
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:k]


__all__ = ["AgentMemoryKind", "ScoredMemory", "bg_record_memory", "record_memory", "search_memory"]
