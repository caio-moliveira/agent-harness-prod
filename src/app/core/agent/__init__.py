"""Agent module: the persisted, user-owned agent configuration.

An ``Agent`` is a configuration row consumed by the one shared Data Agent runtime — no
code generation, no per-agent deployment. Memory, sessions, sources and skills are
isolated per ``(user_id, agent_id)``.
"""

from src.app.core.agent.agent_model import Agent
from src.app.core.agent.agent_repository import AgentRepository

__all__ = ["Agent", "AgentRepository"]
