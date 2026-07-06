"""Skill module: a per-user library of reusable instruction documents (SKILL.md).

Skills are markdown guidance documents (name + description + body) — never executable code.
They live in the owning user's library and can be attached to any of that user's agents; the
runtime materializes attached skills and loads them via deepagents' progressive disclosure.
"""

from src.app.core.skill.skill_model import Skill
from src.app.core.skill.skill_repository import SkillRepository

__all__ = ["Skill", "SkillRepository"]
