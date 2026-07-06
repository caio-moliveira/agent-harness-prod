"""Factory for the per-session Data Agent."""

from typing import Optional

from src.app.agents.data_agent.agent_data import DataAgent, load_system_prompt
from src.app.core.sandbox.registry import SessionResources

__all__ = ["DataAgent", "load_system_prompt", "build_data_agent"]


def build_data_agent(
    resources: SessionResources,
    user_id: Optional[int] = None,
    system_prompt: Optional[str] = None,
    agent_id: Optional[int] = None,
    name: str = "Data Agent",
    web_search: bool = False,
    memory_enabled: bool = True,
) -> DataAgent:
    """Build a Data Agent for a session's live resources and stored agent config.

    Args:
        resources: A ``SessionResources`` with an attached db and/or sandbox backend.
        user_id: The owning user, used for long-term memory tools and retrieval.
        system_prompt: The configured agent's system prompt (falls back to the default).
        agent_id: The configured agent's id, used to isolate long-term memory.
        name: Display name for the agent.
        web_search: When True, attach a host-side web-search tool.
        memory_enabled: When False, disable long-term memory read/write for the agent.

    Returns:
        A compiled DataAgent with SQL tools (if a db is attached), the sandbox backend
        (if a folder is granted), and per-agent memory tools.
    """
    backend = getattr(resources, "sandbox_backend", None)
    return DataAgent(
        name=name,
        db=resources.db,
        backend=backend,
        user_id=user_id,
        system_prompt=system_prompt,
        agent_id=agent_id,
        web_search=web_search,
        memory_enabled=memory_enabled,
    )
