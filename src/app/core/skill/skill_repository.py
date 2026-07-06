"""Skill repository for managing skill database operations.

Each method runs in its own short-lived session (``session_scope``) so a failed query rolls back
on its own and never poisons a later request. Ownership checks live in the API layer (403).
"""

from datetime import UTC, datetime
from typing import List, Optional

from sqlmodel import Session as DBSession, select

from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.skill.skill_model import Skill, SkillVersion
from src.app.core.skill.skill_status import DRAFT, can_transition


def _snapshot(session: DBSession, skill: Skill) -> None:
    """Append an immutable version snapshot of ``skill``'s current content."""
    session.add(
        SkillVersion(
            skill_id=skill.id,
            version=skill.version,
            name=skill.name,
            description=skill.description,
            body=skill.body,
            when_to_use=skill.when_to_use,
            sources=skill.sources,
            steps=skill.steps,
            output_format=skill.output_format,
            status=skill.status,
        )
    )


class SkillRepository:
    """Repository class for skill database operations."""

    def __init__(self, session: Optional[DBSession] = None):
        """Accept an optional session for backward compatibility; methods use their own scope."""
        self.session = session

    async def create_skill(
        self,
        user_id: int,
        name: str,
        description: str = "",
        body: str = "",
        source: str = "authored",
        when_to_use: str = "",
        sources: str = "",
        steps: str = "",
        output_format: str = "",
    ) -> Skill:
        """Create a new skill owned by a user."""
        with session_scope() as session:
            skill = Skill(
                user_id=user_id,
                name=name,
                description=description,
                body=body,
                source=source,
                when_to_use=when_to_use,
                sources=sources,
                steps=steps,
                output_format=output_format,
            )
            session.add(skill)
            session.commit()
            session.refresh(skill)
            _snapshot(session, skill)  # version 1
            session.commit()
            logger.info("skill_created", skill_id=skill.id, user_id=user_id, name=name, source=source)
            return skill

    async def get_skill(self, skill_id: int) -> Optional[Skill]:
        """Get a skill by ID (no ownership check — the caller enforces that)."""
        with session_scope() as session:
            return session.get(Skill, skill_id)

    async def get_user_skills(self, user_id: int) -> List[Skill]:
        """Get all skills owned by a user, oldest first."""
        with session_scope() as session:
            statement = select(Skill).where(Skill.user_id == user_id).order_by(Skill.created_at)
            return list(session.exec(statement).all())

    async def get_skills_by_ids(self, skill_ids: List[int]) -> List[Skill]:
        """Get skills for a list of IDs (order not guaranteed)."""
        if not skill_ids:
            return []
        with session_scope() as session:
            statement = select(Skill).where(Skill.id.in_(skill_ids))  # type: ignore[attr-defined]
            return list(session.exec(statement).all())

    async def update_skill(
        self,
        skill_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        body: Optional[str] = None,
        when_to_use: Optional[str] = None,
        sources: Optional[str] = None,
        steps: Optional[str] = None,
        output_format: Optional[str] = None,
    ) -> Optional[Skill]:
        """Update a skill's fields. Omitted (None) fields are left unchanged. None if not found."""
        with session_scope() as session:
            skill = session.get(Skill, skill_id)
            if skill is None:
                return None
            if name is not None:
                skill.name = name
            if description is not None:
                skill.description = description
            if body is not None:
                skill.body = body
            if when_to_use is not None:
                skill.when_to_use = when_to_use
            if sources is not None:
                skill.sources = sources
            if steps is not None:
                skill.steps = steps
            if output_format is not None:
                skill.output_format = output_format
            # A content edit bumps the version and sends the skill back to draft: refinements
            # must be re-approved before they can load again (RF-10/RF-20).
            skill.version += 1
            skill.status = DRAFT
            skill.updated_at = datetime.now(UTC)
            session.add(skill)
            session.commit()
            session.refresh(skill)
            _snapshot(session, skill)
            session.commit()
            logger.info("skill_updated", skill_id=skill_id, version=skill.version)
            return skill

    async def set_status(self, skill_id: int, target: str) -> Optional[Skill]:
        """Transition a skill's approval status. Returns None if not found; raises on illegal move."""
        with session_scope() as session:
            skill = session.get(Skill, skill_id)
            if skill is None:
                return None
            if not can_transition(skill.status, target):
                raise ValueError(f"Transição de status inválida: {skill.status} → {target}")
            skill.status = target
            skill.updated_at = datetime.now(UTC)
            session.add(skill)
            session.commit()
            session.refresh(skill)
            logger.info("skill_status_changed", skill_id=skill_id, status=target)
            return skill

    async def get_versions(self, skill_id: int) -> List[SkillVersion]:
        """Return a skill's version history, newest first."""
        with session_scope() as session:
            statement = (
                select(SkillVersion)
                .where(SkillVersion.skill_id == skill_id)
                .order_by(SkillVersion.version.desc())  # type: ignore[attr-defined]
            )
            return list(session.exec(statement).all())

    async def delete_skill(self, skill_id: int) -> bool:
        """Delete a skill by ID. Returns False if not found."""
        with session_scope() as session:
            skill = session.get(Skill, skill_id)
            if skill is None:
                return False
            session.delete(skill)
            session.commit()
            logger.info("skill_deleted", skill_id=skill_id)
            return True
