"""Factory for the per-session Data Agent."""

from typing import Optional

from src.app.agents.data_agent.agent_data import DataAgent, load_system_prompt
from src.app.core.sandbox.registry import SessionResources

__all__ = ["DataAgent", "load_system_prompt", "build_data_agent"]


def build_data_agent(resources: SessionResources, user_id: Optional[int] = None) -> DataAgent:
    """Build a Data Agent for a session's live resources.

    Args:
        resources: A ``SessionResources`` with an attached db and/or sandbox backend.
        user_id: The owning user, used for long-term memory tools and retrieval.

    Returns:
        A compiled DataAgent with SQL tools (if a db is attached), the sandbox backend
        (if a folder is granted), and per-user memory tools.
    """
    backend = getattr(resources, "sandbox_backend", None)
    return DataAgent(name="Data Agent", db=resources.db, backend=backend, user_id=user_id)
