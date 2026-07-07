"""Repository for ingested-file tracking records (incremental ingestion, #15)."""

from typing import Dict, Optional

from sqlmodel import delete, select

from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.ingestion.source_model import IngestedFile


class IngestedFileRepository:
    """Persistence for per-file ingestion state, scoped to (user_id, agent_id)."""

    async def get_known(self, user_id: int, agent_id: Optional[int]) -> Dict[str, IngestedFile]:
        """Return the user/agent's tracked files keyed by source_path."""
        with session_scope() as session:
            statement = select(IngestedFile).where(IngestedFile.user_id == user_id)
            if agent_id is not None:
                statement = statement.where(IngestedFile.agent_id == agent_id)
            return {f.source_path: f for f in session.exec(statement).all()}

    async def upsert(
        self, user_id: int, agent_id: Optional[int], source_path: str, content_hash: str, chunk_count: int
    ) -> None:
        """Insert or update the tracking record for one source file."""
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
