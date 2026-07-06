"""Skill repository for managing skill database operations.

Mirrors ``AgentRepository``. Ownership checks live in the API layer (403); the repository is
persistence-only.
"""

from datetime import UTC, datetime
from typing import List, Optional

from sqlmodel import Session as DBSession, select

from src.app.core.common.logging import logger
from src.app.core.skill.skill_model import Skill


class SkillRepository:
    """Repository class for skill database operations."""

    def __init__(self, session: DBSession):
        """Initialize skill repository with a database session."""
        self.session = session

    async def create_skill(
        self, user_id: int, name: str, description: str = "", body: str = "", source: str = "authored"
    ) -> Skill:
        """Create a new skill owned by a user."""
        skill = Skill(user_id=user_id, name=name, description=description, body=body, source=source)
        self.session.add(skill)
        self.session.commit()
        self.session.refresh(skill)
        logger.info("skill_created", skill_id=skill.id, user_id=user_id, name=name, source=source)
        return skill

    async def get_skill(self, skill_id: int) -> Optional[Skill]:
        """Get a skill by ID (no ownership check — the caller enforces that)."""
        return self.session.get(Skill, skill_id)

    async def get_user_skills(self, user_id: int) -> List[Skill]:
        """Get all skills owned by a user, oldest first."""
        statement = select(Skill).where(Skill.user_id == user_id).order_by(Skill.created_at)
        return list(self.session.exec(statement).all())

    async def get_skills_by_ids(self, skill_ids: List[int]) -> List[Skill]:
        """Get skills for a list of IDs (order not guaranteed)."""
        if not skill_ids:
            return []
        statement = select(Skill).where(Skill.id.in_(skill_ids))  # type: ignore[attr-defined]
        return list(self.session.exec(statement).all())

    async def update_skill(
        self,
        skill_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        body: Optional[str] = None,
    ) -> Optional[Skill]:
        """Update a skill's fields. Returns None if not found."""
        skill = self.session.get(Skill, skill_id)
        if skill is None:
            return None
        if name is not None:
            skill.name = name
        if description is not None:
            skill.description = description
        if body is not None:
            skill.body = body
        skill.updated_at = datetime.now(UTC)
        self.session.add(skill)
        self.session.commit()
        self.session.refresh(skill)
        logger.info("skill_updated", skill_id=skill_id)
        return skill

    async def delete_skill(self, skill_id: int) -> bool:
        """Delete a skill by ID. Returns False if not found."""
        skill = self.session.get(Skill, skill_id)
        if skill is None:
            return False
        self.session.delete(skill)
        self.session.commit()
        logger.info("skill_deleted", skill_id=skill_id)
        return True
