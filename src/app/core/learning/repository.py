"""Repositories for correction signals and reflected agent preferences (#20)."""

from typing import List, Optional

from sqlmodel import select

from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.learning.models import AgentPreference, CorrectionSignal


class CorrectionRepository:
    """Persistence for artifact correction signals."""

    async def create(
        self,
        user_id: int,
        agent_id: Optional[int],
        skill_id: Optional[int],
        note: str = "",
        artifact_ref: str = "",
    ) -> CorrectionSignal:
        """Record a correction signal."""
        with session_scope() as session:
            signal = CorrectionSignal(
                user_id=user_id, agent_id=agent_id, skill_id=skill_id, note=note, artifact_ref=artifact_ref
            )
            session.add(signal)
            session.commit()
            session.refresh(signal)
            logger.info("correction_signal_recorded", signal_id=signal.id, user_id=user_id, skill_id=skill_id)
            return signal

    async def list_for_agent(self, user_id: int, agent_id: Optional[int]) -> List[CorrectionSignal]:
        """List correction signals for a user/agent, oldest first (query-level scoped)."""
        with session_scope() as session:
            statement = select(CorrectionSignal).where(CorrectionSignal.user_id == user_id)
            if agent_id is not None:
                statement = statement.where(CorrectionSignal.agent_id == agent_id)
            return list(session.exec(statement.order_by(CorrectionSignal.created_at)).all())


class PreferenceRepository:
    """Persistence for reflected per-agent preferences (upsert by key)."""

    async def upsert(self, user_id: int, agent_id: Optional[int], key: str, value: str) -> None:
        """Insert or update one preference for a user/agent."""
        with session_scope() as session:
            statement = select(AgentPreference).where(
                AgentPreference.user_id == user_id,
                AgentPreference.agent_id == agent_id,
                AgentPreference.key == key,
            )
            pref = session.exec(statement).first()
            if pref is None:
                pref = AgentPreference(user_id=user_id, agent_id=agent_id, key=key)
            pref.value = value
            session.add(pref)
            session.commit()

    async def get_all(self, user_id: int, agent_id: Optional[int]) -> dict:
        """Return an agent's reflected preferences as a plain dict."""
        with session_scope() as session:
            statement = select(AgentPreference).where(AgentPreference.user_id == user_id)
            if agent_id is not None:
                statement = statement.where(AgentPreference.agent_id == agent_id)
            return {p.key: p.value for p in session.exec(statement).all()}
