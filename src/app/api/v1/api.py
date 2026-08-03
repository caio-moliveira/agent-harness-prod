"""API v1 router configuration.

This module sets up the main API router and includes all sub-routers for different
endpoints like authentication and the data agent.
"""

import asyncio
from datetime import datetime

from fastapi import APIRouter
from fastapi import (
    Request,
    status,
)
from sqlmodel import select
from starlette.responses import JSONResponse

from src.app.core.db.database import session_scope
from src.app.api.security.limiter import (
    limiter,
)
from src.app.api.v1.agents import router as agents_router
from src.app.api.v1.auth import router as auth_router
from src.app.api.v1.data_agent import router as data_agent_router
from src.app.api.v1.deep_research import router as deep_research_router
from src.app.api.v1.hitl import router as hitl_router
from src.app.api.v1.metrics import router as metrics_router
from src.app.api.v1.sessions import router as sessions_router
from src.app.api.v1.skills import router as skills_router
from src.app.api.v1.usage import router as usage_router
from src.app.api.v1.text_to_sql import router as text_to_sql_router
from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.init import user_repository

api_router = APIRouter()

# Include routers
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(agents_router, prefix="/agents", tags=["agents"])
api_router.include_router(sessions_router, prefix="/sessions", tags=["sessions"])
api_router.include_router(hitl_router, prefix="/hitl", tags=["hitl"])
api_router.include_router(metrics_router, prefix="/metrics", tags=["metrics"])
api_router.include_router(skills_router, prefix="/skills", tags=["skills"])
api_router.include_router(deep_research_router, prefix="/deep-research", tags=["deep-research"])
api_router.include_router(text_to_sql_router, prefix="/text-to-sql", tags=["text-to-sql"])
api_router.include_router(data_agent_router, prefix="/data-agent", tags=["data-agent"])
api_router.include_router(usage_router, prefix="/me", tags=["usage"])


async def _database_reachable() -> bool:
    """Whether the database answers a trivial query right now.

    Deliberately runs a real ``SELECT 1`` in a thread: the repositories are ``async def`` wrappers
    around sync sessions, and calling one WITHOUT awaiting (the previous implementation) only built
    a coroutine — the database was never touched, so the probe could not fail and always reported
    healthy. A health check that cannot fail is worse than none: the orchestrator keeps routing
    traffic to a broken instance.
    """
    try:
        await asyncio.to_thread(_ping_database)
        return True
    except Exception as exc:
        logger.warning("database_health_check_failed", error=str(exc))
        return False


def _ping_database() -> None:
    """Blocking ``SELECT 1`` against the pool (runs in a worker thread)."""
    with session_scope() as session:
        session.exec(select(1)).first()


@api_router.get("/health/live")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["health"][0])
async def liveness(request: Request) -> JSONResponse:
    """Liveness: is this process alive and serving?

    Never touches dependencies — a liveness probe that fails on a database blip would make the
    orchestrator RESTART a perfectly healthy process, turning a recoverable outage into a crash
    loop. Use ``/health/ready`` to decide whether to send traffic.
    """
    return JSONResponse(content={"status": "alive", "version": settings.VERSION}, status_code=status.HTTP_200_OK)


@api_router.get("/health/ready")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["health"][0])
async def readiness(request: Request) -> JSONResponse:
    """Readiness: can this instance serve real traffic (dependencies included)?

    Returns 503 when the database is unreachable so the load balancer takes this instance out of
    rotation while leaving the process running.
    """
    db_healthy = await _database_reachable()
    payload = {
        "status": "ready" if db_healthy else "not_ready",
        "components": {"database": "healthy" if db_healthy else "unhealthy"},
    }
    code = status.HTTP_200_OK if db_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=payload, status_code=code)


@api_router.get("/health")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["health"][0])
async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint with environment-specific information.

    Returns:
        Dict[str, Any]: Health status information
    """
    logger.info("health_check_called")
    db_healthy = await _database_reachable()

    response = {
        "status": "healthy" if db_healthy else "degraded",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT.value,
        "components": {"api": "healthy", "database": "healthy" if db_healthy else "unhealthy"},
        "timestamp": datetime.now().isoformat(),
    }

    status_code = status.HTTP_200_OK if db_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=response, status_code=status_code)


@api_router.get("/")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["root"][0])
async def root(request: Request):
    """Root endpoint returning basic API information."""
    logger.info("root_endpoint_called")
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "healthy",
        "environment": settings.ENVIRONMENT.value,
        "swagger_url": "/docs",
        "redoc_url": "/redoc",
    }
