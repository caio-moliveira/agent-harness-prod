"""Skill refinement from correction signals (#20, RF-19/RF-20).

When a generated artifact needs manual correction, that signal can propose a refinement to the
skill that produced it. The refinement is applied as a DRAFT revision — it goes through the #17
approval gate and NEVER reaches production automatically. Approval in one context never carries to
the next: a refined skill must be explicitly re-approved before it loads again.
"""

from typing import Optional

from src.app.core.common.logging import logger
from src.app.core.learning.repository import CorrectionRepository
from src.app.core.skill.skill_model import Skill
from src.app.core.skill.skill_repository import SkillRepository


async def propose_refinement(
    user_id: int,
    agent_id: Optional[int],
    skill_id: int,
    proposed_body: str,
    correction_note: str = "",
    artifact_ref: str = "",
    skill_repo: Optional[SkillRepository] = None,
    correction_repo: Optional[CorrectionRepository] = None,
) -> Optional[Skill]:
    """Record the correction and apply the refinement as a draft revision (gated by approval).

    Returns the drafted skill (``status == 'draft'``, version bumped) or None if the skill is gone.
    """
    skill_repo = skill_repo or SkillRepository()
    correction_repo = correction_repo or CorrectionRepository()

    await correction_repo.create(
        user_id=user_id, agent_id=agent_id, skill_id=skill_id, note=correction_note, artifact_ref=artifact_ref
    )
    # update_skill (#17) bumps the version and resets status to draft — re-approval required.
    refined = await skill_repo.update_skill(skill_id, body=proposed_body)
    if refined is not None:
        logger.info(
            "skill_refinement_proposed",
            skill_id=skill_id,
            user_id=user_id,
            version=refined.version,
            status=refined.status,
        )
    return refined
