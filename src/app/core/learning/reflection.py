"""Reflection (#20, RF-18): derive per-agent preferences from the episodic event log.

A periodic pass over an agent's events extracts recurring patterns — the output format the user
keeps choosing, the skill they lean on — and persists them as ``AgentPreference`` rows (semantic
memory). ``reflect_preferences`` is a pure aggregation so it is trivially testable; ``run_reflection``
fetches, reflects, and persists.
"""

import asyncio
from collections import Counter
from typing import List, Optional

from src.app.core.common.logging import logger
from src.app.core.learning.repository import PreferenceRepository
from src.app.core.session.event_model import SessionEvent, SessionEventType
from src.app.core.session.event_repository import SessionEventRepository


def reflect_preferences(events: List[SessionEvent]) -> dict:
    """Aggregate an agent's events into a preference profile (pure)."""
    formats = Counter(
        e.payload.get("format")
        for e in events
        if e.event_type == SessionEventType.ARTIFACT_GENERATED and e.payload.get("format")
    )
    skills = Counter(
        e.payload.get("skill")
        for e in events
        if e.event_type == SessionEventType.SKILL_USED and e.payload.get("skill")
    )

    profile: dict = {"total_events": len(events)}
    if formats:
        profile["preferred_output_format"] = formats.most_common(1)[0][0]
    if skills:
        profile["most_used_skill"] = skills.most_common(1)[0][0]
    return profile


async def run_reflection(
    user_id: int,
    agent_id: Optional[int],
    event_repo: Optional[SessionEventRepository] = None,
    pref_repo: Optional[PreferenceRepository] = None,
) -> dict:
    """Reflect over the agent's events and persist the derived preferences. Returns the profile."""
    event_repo = event_repo or SessionEventRepository()
    pref_repo = pref_repo or PreferenceRepository()

    events = await event_repo.get_agent_events(user_id, agent_id)
    profile = reflect_preferences(events)
    for key, value in profile.items():
        await pref_repo.upsert(user_id, agent_id, key, str(value))

    logger.info("agent_reflection_done", user_id=user_id, agent_id=agent_id, keys=list(profile.keys()))
    return profile


def bg_run_reflection(user_id: int, agent_id: Optional[int]) -> None:
    """Fire ``run_reflection`` in the background (non-blocking) — used after a new event lands."""
    asyncio.create_task(run_reflection(user_id, agent_id))


async def get_reflected_preferences(
    user_id: int, agent_id: Optional[int], pref_repo: Optional[PreferenceRepository] = None
) -> str:
    """Return the agent's learned preferences formatted for prompt injection (empty if none).

    The bookkeeping ``total_events`` counter is omitted — only actionable preferences are surfaced.
    """
    pref_repo = pref_repo or PreferenceRepository()
    prefs = await pref_repo.get_all(user_id, agent_id)
    lines = [f"- {key}: {value}" for key, value in prefs.items() if key != "total_events"]
    return "\n".join(lines)
