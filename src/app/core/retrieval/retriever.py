"""Semantic retrieval over a user/agent's embedded document chunks.

Scoping is query-level by ``(user_id, agent_id)`` — a search can only ever see the caller's own
corpus for the agent in play (consistent with #11). Every hit carries a ``Source`` (kind=doc_chunk)
so the answer can cite the document + section it came from.
"""

import math
from typing import List, Optional

from pydantic import BaseModel

from src.app.core.ingestion.chunk_repository import DocumentChunkRepository
from src.app.core.provenance import Source
from src.app.core.retrieval.embedding import Embedder

_EXCERPT_CHARS = 240


class RetrievedChunk(BaseModel):
    """One retrieval hit: its text, similarity score, and provenance."""

    content: str
    score: float
    source: Source


def _cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors; 0 when either is empty or zero-length."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def retrieve(
    query: str,
    user_id: int,
    agent_id: Optional[int],
    embedder: Embedder,
    repo: Optional[DocumentChunkRepository] = None,
    k: int = 5,
) -> List[RetrievedChunk]:
    """Return the top-``k`` chunks most similar to ``query`` within the (user, agent) corpus."""
    repo = repo or DocumentChunkRepository()
    chunks = await repo.get_embedded_chunks(user_id, agent_id)
    if not chunks:
        return []

    qvec = await embedder.embed_query(query)
    hits = [
        RetrievedChunk(
            content=chunk.content,
            score=_cosine(qvec, chunk.embedding or []),
            source=Source(
                kind="doc_chunk",
                document=chunk.source_path,
                section=chunk.section,
                excerpt=(chunk.content or "")[:_EXCERPT_CHARS],
            ),
        )
        for chunk in chunks
    ]
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]
