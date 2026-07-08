"""Repository for ingested document chunks.

Each method runs in its own short-lived session (``session_scope``) so a failed write rolls back
on its own. Reads are always filtered at the query level by ``(user_id, agent_id)`` — never
post-hoc — keeping a user's corpus isolated per agent (consistent with #11).
"""

from typing import List, Optional

from sqlalchemy import func
from sqlmodel import delete, select

from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.ingestion.chunk_model import DocumentChunk


class DocumentChunkRepository:
    """Persistence for ingested document chunks."""

    async def add_chunks(self, chunks: List[DocumentChunk]) -> int:
        """Persist a batch of chunks. Returns how many were written."""
        if not chunks:
            return 0
        with session_scope() as session:
            for chunk in chunks:
                session.add(chunk)
            session.commit()
            logger.info(
                "document_chunks_added",
                count=len(chunks),
                user_id=chunks[0].user_id,
                agent_id=chunks[0].agent_id,
            )
            return len(chunks)

    async def replace_source(
        self, user_id: int, agent_id: Optional[int], source_path: str, chunks: List[DocumentChunk]
    ) -> int:
        """Atomically swap a document's chunks (delete the old set + insert the new, one transaction).

        If anything fails the transaction rolls back, so a re-ingest can never leave a document with
        zero chunks (the "manifest without chunks" dead state). Idempotent per source.
        """
        with session_scope() as session:
            session.exec(
                delete(DocumentChunk).where(
                    DocumentChunk.user_id == user_id,
                    DocumentChunk.agent_id == agent_id,
                    DocumentChunk.source_path == source_path,
                )
            )
            for chunk in chunks:
                session.add(chunk)
            session.commit()
        logger.info(
            "document_chunks_replaced", count=len(chunks), user_id=user_id, agent_id=agent_id, source_path=source_path
        )
        return len(chunks)

    async def count_by_source(self, user_id: int, agent_id: Optional[int], source_path: str) -> int:
        """Number of chunks currently stored for one document — used to detect a wiped corpus."""
        with session_scope() as session:
            statement = select(func.count()).select_from(DocumentChunk).where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.source_path == source_path,
            )
            if agent_id is not None:
                statement = statement.where(DocumentChunk.agent_id == agent_id)
            return int(session.exec(statement).one())

    async def get_chunks(self, user_id: int, agent_id: Optional[int] = None) -> List[DocumentChunk]:
        """Return a user's chunks, scoped to one agent, oldest first (query-level filter)."""
        with session_scope() as session:
            statement = select(DocumentChunk).where(DocumentChunk.user_id == user_id)
            if agent_id is not None:
                statement = statement.where(DocumentChunk.agent_id == agent_id)
            statement = statement.order_by(DocumentChunk.source_path, DocumentChunk.chunk_index)
            return list(session.exec(statement).all())

    async def get_chunks_by_source(
        self, user_id: int, agent_id: Optional[int], source_path: str
    ) -> List[DocumentChunk]:
        """Return one document's chunks (by source path), ordered by chunk index — page order."""
        with session_scope() as session:
            statement = select(DocumentChunk).where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.source_path == source_path,
            )
            if agent_id is not None:
                statement = statement.where(DocumentChunk.agent_id == agent_id)
            statement = statement.order_by(DocumentChunk.chunk_index)
            return list(session.exec(statement).all())

    async def get_chunks_without_embedding(self, user_id: int, agent_id: Optional[int] = None) -> List[DocumentChunk]:
        """Return a user's not-yet-embedded chunks (indexing work list), scoped to one agent."""
        with session_scope() as session:
            statement = select(DocumentChunk).where(
                DocumentChunk.user_id == user_id, DocumentChunk.embedding.is_(None)
            )
            if agent_id is not None:
                statement = statement.where(DocumentChunk.agent_id == agent_id)
            return list(session.exec(statement).all())

    async def get_embedded_chunks(self, user_id: int, agent_id: Optional[int] = None) -> List[DocumentChunk]:
        """Return a user's embedded chunks for retrieval, scoped to one agent (query-level filter)."""
        with session_scope() as session:
            statement = select(DocumentChunk).where(
                DocumentChunk.user_id == user_id, DocumentChunk.embedding.is_not(None)
            )
            if agent_id is not None:
                statement = statement.where(DocumentChunk.agent_id == agent_id)
            return list(session.exec(statement).all())

    async def set_embedding(self, chunk_id: int, vector: List[float]) -> None:
        """Attach a computed embedding to one chunk."""
        with session_scope() as session:
            chunk = session.get(DocumentChunk, chunk_id)
            if chunk is None:
                return
            chunk.embedding = vector
            session.add(chunk)
            session.commit()

    async def delete_by_source(self, user_id: int, agent_id: Optional[int], source_path: str) -> int:
        """Delete all chunks of one source document for a user/agent (used by re-ingest, #15)."""
        with session_scope() as session:
            statement = delete(DocumentChunk).where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.agent_id == agent_id,
                DocumentChunk.source_path == source_path,
            )
            result = session.exec(statement)
            session.commit()
            deleted = result.rowcount or 0
            logger.info("document_chunks_deleted", count=deleted, user_id=user_id, source_path=source_path)
            return deleted
