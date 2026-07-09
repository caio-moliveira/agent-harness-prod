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
    skills_dir: Optional[str] = None,
    workspace_context: str = "",
    folder_writable: bool = False,
    session_id: Optional[str] = None,
    sql_enabled: bool = False,
) -> DataAgent:
    """Build a Data Agent for a session's live resources and stored agent config.

    Args:
        resources: A ``SessionResources`` with an attached db and/or granted folder.
        user_id: The owning user, used for long-term memory tools and retrieval.
        system_prompt: The configured agent's system prompt (falls back to the default).
        agent_id: The configured agent's id, used to isolate long-term memory.
        name: Display name for the agent.
        web_search: When True, attach a host-side web-search tool.
        memory_enabled: When False, disable long-term memory read/write for the agent.
        skills_dir: Optional directory of SKILL.md files to load via progressive disclosure.
        workspace_context: Optional briefing of attached sources, prepended to the system prompt.
        folder_writable: When True, the granted folder allows writes (still confined to it);
            defaults to read-only.
        session_id: The session id, bound to the artifact tool for episodic-log attribution.
        sql_enabled: When True (and a db is attached), the user's database is reachable through the
            isolated read-only ``text_sql_agent`` subagent; defaults off (DB not queryable).

    Returns:
        A compiled DataAgent with a per-session FilesystemBackend over the granted folder (if any),
        per-agent memory tools, and — when ``sql_enabled`` and a db is attached — the read-only
        ``text_sql_agent`` subagent over the connected database.
    """
    return DataAgent(
        name=name,
        db=resources.db,
        root_dir=resources.folder,
        user_id=user_id,
        system_prompt=system_prompt,
        agent_id=agent_id,
        web_search=web_search,
        memory_enabled=memory_enabled,
        skills_dir=skills_dir,
        workspace_context=workspace_context,
        folder_writable=folder_writable,
        session_id=session_id,
        sql_enabled=sql_enabled,
    )
