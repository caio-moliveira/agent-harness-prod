"""Skill approval state machine (#17).

Only ``approved`` skills are loaded into an agent. Editing a skill's content sends it back to
``draft`` (re-approval required) — refinements never reach production unreviewed (RF-10/RF-20).
"""

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from src.app.core.skill.skill_model import Skill

DRAFT = "draft"
IN_REVIEW = "in_review"
APPROVED = "approved"

STATUSES = {DRAFT, IN_REVIEW, APPROVED}

# Allowed transitions: submit for review, approve, or send back / reopen for editing.
_ALLOWED = {
    DRAFT: {IN_REVIEW},
    IN_REVIEW: {APPROVED, DRAFT},
    APPROVED: {DRAFT},
}


def can_transition(current: str, target: str) -> bool:
    """Whether moving a skill from ``current`` to ``target`` status is allowed."""
    if target not in STATUSES:
        return False
    if current == target:
        return False
    return target in _ALLOWED.get(current, set())


def filter_loadable(skills: List["Skill"], owner_id: int) -> List["Skill"]:
    """Return the subset of ``skills`` an agent may actually load: owned by ``owner_id`` and approved.

    The single rule behind both runtime materialization and any listing shown to a user — the two
    must never be able to disagree about which skills are usable.
    """
    return [s for s in skills if s.user_id == owner_id and s.status == APPROVED]
