"""Subagents the Data Agent delegates to for context-isolated work.

Each subagent runs its noisy, token-heavy loop in a fresh context and returns only the
distilled result to the parent Data Agent (via the deepagents ``task()`` tool), keeping the
parent's context clean.
"""

from src.app.agents.data_agent.subagents.user_sql import make_user_sql_subagent

__all__ = ["make_user_sql_subagent"]
