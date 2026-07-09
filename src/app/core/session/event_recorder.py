"""Maps agent-runtime tool calls to episodic-log events and records them.

The streaming loop calls ``bg_record_tool_event`` on each tool call; it schedules the write in
the background (fire-and-forget) so auditing never blocks the response — mirroring how long-term
memory is written. ``classify_tool_event`` is the pure mapping from a tool name to an event type
(or ``None`` when the tool is not auditable), kept separate so it is trivially testable.
"""

import asyncio
from typing import Optional

from src.app.core.common.logging import logger
from src.app.core.session.event_model import SessionEventType
from src.app.core.session.event_repository import SessionEventRepository

# Tool name -> auditable event type. Tools not listed here are never recorded.
_TOOL_EVENT_MAP = {
    "run_sql": SessionEventType.QUERY_EXECUTED,
    "read_file": SessionEventType.DOCUMENT_READ,
    "grep": SessionEventType.DOCUMENT_READ,
    "glob": SessionEventType.DOCUMENT_READ,
}

# task() subagent_type -> (event type, scope). Recorded at the delegation boundary (the parent's
# task() call, which always fires) so the audit trail is robust regardless of whether the
# subagent's own nested tool events propagate to the parent stream.
_DELEGATION_EVENT_MAP = {
    "text_sql_agent": (SessionEventType.QUERY_EXECUTED, "database"),
    "deep_research": (SessionEventType.WEB_RESEARCH, "web"),
}


def classify_tool_event(tool_name: str) -> Optional[str]:
    """Return the event type for a runtime tool, or ``None`` if it is not auditable."""
    return _TOOL_EVENT_MAP.get(tool_name)


def classify_delegation(subagent_type: str) -> Optional[tuple[str, str]]:
    """Return ``(event_type, scope)`` for a task() delegation, or ``None`` if not auditable."""
    return _DELEGATION_EVENT_MAP.get(subagent_type)


async def record_tool_event(
    repo: SessionEventRepository,
    user_id: Optional[int],
    agent_id: Optional[int],
    session_id: str,
    tool_name: str,
    tool_input: Optional[str] = None,
    scope: str = "",
) -> None:
    """Record a tool call as an episodic event, if it is auditable.

    Guard clauses first: skip unauditable tools and anonymous (no-user) runs. Any failure is
    swallowed (logged) so the audit trail can never break the agent's response stream.
    """
    event_type = classify_tool_event(tool_name)
    if event_type is None or user_id is None:
        return
    try:
        await repo.record_event(
            user_id=user_id,
            session_id=session_id,
            event_type=event_type,
            agent_id=agent_id,
            payload={"tool": tool_name, "input": (tool_input or "")[:1000]},
            scope=scope,
        )
    except Exception:
        logger.exception("session_event_record_failed", tool=tool_name, session_id=session_id)


def bg_record_tool_event(
    repo: SessionEventRepository,
    user_id: Optional[int],
    agent_id: Optional[int],
    session_id: str,
    tool_name: str,
    tool_input: Optional[str] = None,
    scope: str = "",
) -> None:
    """Fire-and-forget wrapper around ``record_tool_event`` so auditing never blocks streaming."""
    if classify_tool_event(tool_name) is None or user_id is None:
        return
    asyncio.create_task(
        record_tool_event(repo, user_id, agent_id, session_id, tool_name, tool_input, scope)
    )


async def record_delegation_event(
    repo: SessionEventRepository,
    user_id: Optional[int],
    agent_id: Optional[int],
    session_id: str,
    subagent_type: str,
    task: Optional[str] = None,
) -> None:
    """Record a task() delegation to a subagent as an episodic event, if it is auditable.

    Guard clauses first: skip unknown subagents and anonymous runs. Any failure is swallowed
    (logged) so the audit trail can never break the agent's response stream.
    """
    mapping = classify_delegation(subagent_type)
    if mapping is None or user_id is None:
        return
    event_type, scope = mapping
    try:
        await repo.record_event(
            user_id=user_id,
            session_id=session_id,
            event_type=event_type,
            agent_id=agent_id,
            payload={"delegated_to": subagent_type, "task": (task or "")[:1000]},
            scope=scope,
        )
    except Exception:
        logger.exception("session_delegation_record_failed", subagent=subagent_type, session_id=session_id)


def bg_record_delegation_event(
    repo: SessionEventRepository,
    user_id: Optional[int],
    agent_id: Optional[int],
    session_id: str,
    subagent_type: str,
    task: Optional[str] = None,
) -> None:
    """Fire-and-forget wrapper around ``record_delegation_event`` so auditing never blocks streaming."""
    if classify_delegation(subagent_type) is None or user_id is None:
        return
    asyncio.create_task(
        record_delegation_event(repo, user_id, agent_id, session_id, subagent_type, task)
    )
