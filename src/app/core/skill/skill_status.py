"""Skill approval state machine (#17).

Only ``approved`` skills are loaded into an agent. Editing a skill's content sends it back to
``draft`` (re-approval required) — refinements never reach production unreviewed (RF-10/RF-20).
"""

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
