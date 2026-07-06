"""Session endpoints: the episodic event log / audit trail (#10).

Read-only access to what happened in a session — documents consulted, SQL executed, skills used,
artifacts generated — owner-scoped: a user may only read the events of their own sessions.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from src.app.api.security.limiter import limiter
from src.app.api.v1.auth import get_current_user
from src.app.api.v1.dtos.session_events import SessionEventResponse
from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.user.user_model import User
from src.app.init import session_event_repository, session_repository

router = APIRouter()


@router.get("/{session_id}/events", response_model=List[SessionEventResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["session_events"][0])
async def get_session_events(
    request: Request,
    session_id: str,
    user: User = Depends(get_current_user),
) -> List[SessionEventResponse]:
    """List a session's episodic events, oldest first. Owner-scoped.

    Guard clauses: the session must exist (404) and belong to the caller (403) before any
    events are returned.
    """
    session = await session_repository.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != user.id:
        logger.warning("session_events_access_denied", session_id=session_id, user_id=user.id)
        raise HTTPException(status_code=403, detail="Cannot read another user's session events")

    events = await session_event_repository.get_session_events(session_id)
    logger.info("session_events_listed", session_id=session_id, user_id=user.id, count=len(events))
    return [
        SessionEventResponse(
            id=e.id,
            agent_id=e.agent_id,
            session_id=e.session_id,
            event_type=e.event_type,
            payload=e.payload,
            scope=e.scope,
            created_at=e.created_at,
        )
        for e in events
    ]
