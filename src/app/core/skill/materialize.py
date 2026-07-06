"""Materialize a user's attached skills into a directory of SKILL.md files for deepagents.

deepagents loads skills from directories (each subdir holds a ``SKILL.md`` with YAML frontmatter
``name``/``description`` plus a markdown body). We write the attached skills to a per-agent
directory under the system temp dir and hand that path to ``create_deep_agent(skills=[...])``.
Rewritten on each build so edits take effect; kept keyed by agent so it is reused, not leaked.
"""

import os
import re
import shutil
import tempfile
from typing import Optional

from src.app.core.skill.skill_model import Skill

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(name: str, fallback: str) -> str:
    """Turn a skill name into a safe directory slug."""
    slug = _SLUG.sub("-", name.lower()).strip("-")
    return slug or fallback


def materialize_skills(agent_id: Optional[int], skills: list[Skill]) -> Optional[str]:
    """Write skills as SKILL.md files and return the base directory, or None if empty.

    The base dir is stable per agent and rewritten each call, so it reflects the current library.
    """
    if not skills:
        return None
    base = os.path.join(tempfile.gettempdir(), "agent_harness_skills", f"agent_{agent_id or 'none'}")
    # Rewrite from scratch so removed/renamed skills do not linger.
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)

    for skill in skills:
        slug = _slug(skill.name, f"skill-{skill.id}")
        skill_dir = os.path.join(base, slug)
        os.makedirs(skill_dir, exist_ok=True)
        # Escape frontmatter-sensitive characters in the one-line description.
        description = (skill.description or "").replace("\n", " ").replace('"', "'")
        content = f"---\nname: {skill.name}\ndescription: {description}\n---\n\n{_render_skill_body(skill)}\n"
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(content)

    return base


def _render_skill_body(skill: Skill) -> str:
    """Compose the SKILL.md body from the structured fields (RF-08), skipping empty ones.

    The free-form ``body`` leads; the structured sections (when to use / sources / steps / output)
    follow as headed sections so the agent can act on them during progressive disclosure.
    """
    parts: list[str] = []
    if skill.body:
        parts.append(skill.body.strip())
    for heading, value in (
        ("Quando usar", getattr(skill, "when_to_use", "")),
        ("Fontes necessárias", getattr(skill, "sources", "")),
        ("Passo a passo", getattr(skill, "steps", "")),
        ("Formato de saída", getattr(skill, "output_format", "")),
    ):
        if value and value.strip():
            parts.append(f"## {heading}\n{value.strip()}")
    return "\n\n".join(parts)
