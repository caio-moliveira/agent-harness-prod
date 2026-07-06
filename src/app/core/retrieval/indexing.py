"""Indexing: embed a user/agent's not-yet-embedded chunks so they become searchable.

Runs after ingestion (#13). Embeds in batches; skips empty chunks (e.g. scanned pages awaiting
OCR). Kept separate from ingestion so it can run in the background without blocking uploads (#15).
"""

from typing import Optional

from src.app.core.common.logging import logger
from src.app.core.ingestion.chunk_repository import DocumentChunkRepository
from src.app.core.retrieval.embedding import Embedder


async def index_chunks(
    user_id: int,
    agent_id: Optional[int],
    embedder: Embedder,
    repo: Optional[DocumentChunkRepository] = None,
    batch_size: int = 64,
) -> int:
    """Embed the (user, agent)'s pending chunks. Returns how many were embedded."""
    repo = repo or DocumentChunkRepository()
    pending = await repo.get_chunks_without_embedding(user_id, agent_id)
    pending = [c for c in pending if (c.content or "").strip()]
    if not pending:
        return 0

    embedded = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = await embedder.embed_documents([c.content for c in batch])
        for chunk, vector in zip(batch, vectors, strict=True):
            await repo.set_embedding(chunk.id, vector)
            embedded += 1

    logger.info("chunks_indexed", user_id=user_id, agent_id=agent_id, count=embedded)
    return embedded
