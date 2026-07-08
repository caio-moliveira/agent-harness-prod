"""Repository for ingested-file tracking records (incremental ingestion, #15)."""

import os
from typing import Dict, Optional

from sqlmodel import delete, select

from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.ingestion.source_model import IngestedFile, derive_doc_id


class IngestedFileRepository:
    """Persistence for per-file ingestion state, scoped to (user_id, agent_id)."""

    async def get_known(self, user_id: int, agent_id: Optional[int]) -> Dict[str, IngestedFile]:
        """Return the user/agent's tracked files keyed by source_path."""
        with session_scope() as session:
            statement = select(IngestedFile).where(IngestedFile.user_id == user_id)
            if agent_id is not None:
                statement = statement.where(IngestedFile.agent_id == agent_id)
            return {f.source_path: f for f in session.exec(statement).all()}

    async def list_all(self, user_id: int, agent_id: Optional[int]) -> list[IngestedFile]:
        """Return the (user, agent) manifest rows, ordered by title — the document catalog."""
        with session_scope() as session:
            statement = select(IngestedFile).where(IngestedFile.user_id == user_id)
            if agent_id is not None:
                statement = statement.where(IngestedFile.agent_id == agent_id)
            statement = statement.order_by(IngestedFile.title)
            return list(session.exec(statement).all())

    async def get_by_doc_id(self, user_id: int, agent_id: Optional[int], doc_id: str) -> Optional[IngestedFile]:
        """Resolve a document by its stable ``doc_id`` within the (user, agent) manifest."""
        with session_scope() as session:
            statement = select(IngestedFile).where(
                IngestedFile.user_id == user_id,
                IngestedFile.doc_id == doc_id,
            )
            if agent_id is not None:
                statement = statement.where(IngestedFile.agent_id == agent_id)
            return session.exec(statement).first()

    async def get_summary(self, user_id: int, agent_id: Optional[int]) -> tuple[int, int]:
        """Return ``(document_count, total_page_count)`` for the (user, agent) manifest."""
        with session_scope() as session:
            statement = select(IngestedFile).where(IngestedFile.user_id == user_id)
            if agent_id is not None:
                statement = statement.where(IngestedFile.agent_id == agent_id)
            rows = session.exec(statement).all()
            return len(rows), sum(r.page_count for r in rows)

    async def upsert(
        self,
        user_id: int,
        agent_id: Optional[int],
        source_path: str,
        content_hash: str,
        chunk_count: int,
        *,
        page_count: int = 0,
        text_layer: str = "native",
        ocr_confidence: float = 1.0,
    ) -> None:
        """Insert or update the tracking + manifest record for one source file.

        ``doc_id`` (from the content hash) and ``title`` (the file name, display-only) are derived
        here so every ingestion path fills the catalog consistently.
        """
        with session_scope() as session:
            statement = select(IngestedFile).where(
                IngestedFile.user_id == user_id,
                IngestedFile.agent_id == agent_id,
                IngestedFile.source_path == source_path,
            )
            record = session.exec(statement).first()
            if record is None:
                record = IngestedFile(user_id=user_id, agent_id=agent_id, source_path=source_path)
            record.content_hash = content_hash
            record.chunk_count = chunk_count
            record.doc_id = derive_doc_id(content_hash)
            record.title = os.path.basename(source_path)
            record.page_count = page_count
            record.text_layer = text_layer
            record.ocr_confidence = ocr_confidence
            session.add(record)
            session.commit()

    async def delete(self, user_id: int, agent_id: Optional[int], source_path: str) -> None:
        """Drop the tracking record for one source file (it was removed from the folder)."""
        with session_scope() as session:
            statement = delete(IngestedFile).where(
                IngestedFile.user_id == user_id,
                IngestedFile.agent_id == agent_id,
                IngestedFile.source_path == source_path,
            )
            session.exec(statement)
            session.commit()
            logger.info("ingested_file_untracked", user_id=user_id, source_path=source_path)
