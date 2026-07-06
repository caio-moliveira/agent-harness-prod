"""Agent repository for managing agent database operations.

Each method runs in its own short-lived session (``session_scope``) so a failed query rolls back
on its own and never poisons a later request. Ownership checks live in the API layer (403); the
repository stays persistence-only.
"""

from datetime import UTC, datetime
from typing import List, Optional

from sqlmodel import Session as DBSession, select

from src.app.core.agent.agent_model import Agent
from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope


class AgentRepository:
    """Repository class for agent database operations."""

    def __init__(self, session: Optional[DBSession] = None):
        """Accept an optional session for backward compatibility; methods use their own scope."""
        self.session = session

    async def create_agent(
        self, user_id: int, name: str, system_prompt: str = "", config: Optional[dict] = None
    ) -> Agent:
        """Create a new agent owned by a user."""
        with session_scope() as session:
            agent = Agent(user_id=user_id, name=name, system_prompt=system_prompt, config=config or {})
            session.add(agent)
            session.commit()
            session.refresh(agent)
            logger.info("agent_created", agent_id=agent.id, user_id=user_id, name=name)
            return agent

    async def get_agent(self, agent_id: int) -> Optional[Agent]:
        """Get an agent by ID (no ownership check — the caller enforces that)."""
        with session_scope() as session:
            return session.get(Agent, agent_id)

    async def get_owned_agent(self, agent_id: int, user_id: int) -> Optional[Agent]:
        """Get an agent only if it belongs to ``user_id``, else None.

        Ownership is enforced as a QUERY-LEVEL filter (``WHERE id AND user_id``), not a post-hoc
        comparison after fetching — so a non-owner can never even load the row. This is the
        isolation choke point the session-build path uses.
        """
        with session_scope() as session:
            statement = select(Agent).where(Agent.id == agent_id, Agent.user_id == user_id)
            return session.exec(statement).first()

    async def get_user_agents(self, user_id: int) -> List[Agent]:
        """Get all agents owned by a user, oldest first."""
        with session_scope() as session:
            statement = select(Agent).where(Agent.user_id == user_id).order_by(Agent.created_at)
            return list(session.exec(statement).all())

    async def update_agent(
        self,
        agent_id: int,
        name: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Optional[Agent]:
        """Update an agent's name and/or system prompt. Returns None if not found."""
        with session_scope() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return None
            if name is not None:
                agent.name = name
            if system_prompt is not None:
                agent.system_prompt = system_prompt
            agent.updated_at = datetime.now(UTC)
            session.add(agent)
            session.commit()
            session.refresh(agent)
            logger.info("agent_updated", agent_id=agent_id)
            return agent

    async def set_config_value(self, agent_id: int, key: str, value: object) -> Optional[Agent]:
        """Set (or clear, when value is None) one key in an agent's JSON config.

        Reassigns the dict so SQLAlchemy detects the change on the JSON column.
        """
        with session_scope() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return None
            config = dict(agent.config or {})
            if value is None:
                config.pop(key, None)
            else:
                config[key] = value
            agent.config = config
            agent.updated_at = datetime.now(UTC)
            session.add(agent)
            session.commit()
            session.refresh(agent)
            logger.info("agent_config_updated", agent_id=agent_id, key=key)
            return agent

    async def delete_agent(self, agent_id: int) -> bool:
        """Delete an agent by ID. Returns False if not found."""
        with session_scope() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return False
            session.delete(agent)
            session.commit()
            logger.info("agent_deleted", agent_id=agent_id)
            return True
