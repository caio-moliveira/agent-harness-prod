"""Persistence for the agent's two-tier experience memory (#23), scoped to (user_id, agent_id)."""

from typing import Optional

from sqlmodel import select

from src.app.core.db.database import session_scope
from src.app.core.memory.agent_memory_model import AgentMemory


class AgentMemoryRepository:
    """CRUD for ``AgentMemory`` entries (the summary index + full body)."""

    async def add(self, memory: AgentMemory) -> int:
        """Persist one memory entry; returns its new id."""
        with session_scope() as session:
            session.add(memory)
            session.commit()
            session.refresh(memory)
            return memory.id

    async def list_recent(self, user_id: int, agent_id: Optional[int], limit: int = 12) -> list[AgentMemory]:
        """Return the most recent entries (the briefing's 'work already done' index)."""
        with session_scope() as session:
            statement = select(AgentMemory).where(AgentMemory.user_id == user_id)
            if agent_id is not None:
                statement = statement.where(AgentMemory.agent_id == agent_id)
            statement = statement.order_by(AgentMemory.id.desc()).limit(limit)
            return list(session.exec(statement).all())

    async def get_embedded(self, user_id: int, agent_id: Optional[int]) -> list[AgentMemory]:
        """Return all entries that have an embedding — the search set (tier-1 vectors)."""
        with session_scope() as session:
            statement = select(AgentMemory).where(
                AgentMemory.user_id == user_id,
                AgentMemory.embedding.is_not(None),
            )
            if agent_id is not None:
                statement = statement.where(AgentMemory.agent_id == agent_id)
            return list(session.exec(statement).all())

    async def get_by_id(self, memory_id: int, user_id: int, agent_id: Optional[int]) -> Optional[AgentMemory]:
        """Fetch one entry's full body by id, scoped to the owner (for ``ler_memoria``)."""
        with session_scope() as session:
            statement = select(AgentMemory).where(
                AgentMemory.id == memory_id,
                AgentMemory.user_id == user_id,
            )
            if agent_id is not None:
                statement = statement.where(AgentMemory.agent_id == agent_id)
            return session.exec(statement).first()
