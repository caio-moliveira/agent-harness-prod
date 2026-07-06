"""Agent management API: CRUD over a user's persisted agent configurations.

Every route is scoped to the authenticated user. An agent is owned by exactly one user;
any attempt to read or mutate another user's agent returns 403. Agents are pure
configuration consumed by the shared Data Agent runtime — creating one never provisions
code or a deployment.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from src.app.api.security.limiter import limiter
from src.app.api.v1.auth import get_current_user
from src.app.core.agent.agent_dtos import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    BindDatabaseRequest,
    BindDatabaseResponse,
    BindFolderRequest,
    BindFolderResponse,
    DatabaseSummary,
)
from src.app.core.agent.agent_model import Agent
from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.db.connect import build_db_url, connect_readonly
from src.app.core.sandbox.paths import validate_grantable_folder
from src.app.core.security import encrypt, is_encryption_available
from src.app.core.user.user_model import User
from src.app.init import agent_repository

router = APIRouter()

_RATE = settings.RATE_LIMIT_ENDPOINTS["agents"][0]


def _db_summary(config: dict) -> Optional[DatabaseSummary]:
    """Build the non-secret database summary from an agent's config, or None."""
    db = config.get("database")
    if not db:
        return None
    return DatabaseSummary(
        driver=db["driver"],
        host=db["host"],
        port=int(db["port"]),
        database=db["database"],
        username=db["username"],
        sslmode=db.get("sslmode"),
        password_persisted=bool(db.get("password_encrypted")),
    )


def _to_response(agent: Agent) -> AgentResponse:
    """Map an Agent entity to its API response (never leaks the stored password)."""
    config = agent.config or {}
    # Do not echo the encrypted password back out.
    safe_config = {k: v for k, v in config.items() if k != "database"}
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        system_prompt=agent.system_prompt,
        web_search=bool(config.get("web_search", False)),
        memory=bool(config.get("memory", True)),
        folder=config.get("folder"),
        database=_db_summary(config),
        config=safe_config,
    )


async def _owned_agent_or_error(agent_id: int, user: User) -> Agent:
    """Return the agent if it exists and belongs to the user, else raise 404/403."""
    agent = await agent_repository.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.user_id != user.id:
        logger.warning("agent_access_denied", agent_id=agent_id, user_id=user.id)
        raise HTTPException(status_code=403, detail="Cannot access another user's agent")
    return agent


@router.post("", response_model=AgentResponse)
@limiter.limit(_RATE)
async def create_agent(
    request: Request, body: AgentCreate, user: User = Depends(get_current_user)
) -> AgentResponse:
    """Create a new agent owned by the authenticated user."""
    config = {"web_search": body.web_search, "memory": body.memory}
    agent = await agent_repository.create_agent(user.id, body.name, body.system_prompt, config=config)
    logger.info("agent_api_created", agent_id=agent.id, user_id=user.id)
    return _to_response(agent)


@router.get("", response_model=List[AgentResponse])
@limiter.limit(_RATE)
async def list_agents(request: Request, user: User = Depends(get_current_user)) -> List[AgentResponse]:
    """List all agents owned by the authenticated user."""
    agents = await agent_repository.get_user_agents(user.id)
    return [_to_response(a) for a in agents]


@router.get("/{agent_id}", response_model=AgentResponse)
@limiter.limit(_RATE)
async def get_agent(request: Request, agent_id: int, user: User = Depends(get_current_user)) -> AgentResponse:
    """Get one of the authenticated user's agents."""
    agent = await _owned_agent_or_error(agent_id, user)
    return _to_response(agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
@limiter.limit(_RATE)
async def update_agent(
    request: Request, agent_id: int, body: AgentUpdate, user: User = Depends(get_current_user)
) -> AgentResponse:
    """Update an agent's name, system prompt, and/or capability toggles."""
    await _owned_agent_or_error(agent_id, user)
    updated = await agent_repository.update_agent(agent_id, name=body.name, system_prompt=body.system_prompt)
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if body.web_search is not None:
        updated = await agent_repository.set_config_value(agent_id, "web_search", body.web_search)
    if body.memory is not None:
        updated = await agent_repository.set_config_value(agent_id, "memory", body.memory)
    logger.info("agent_api_updated", agent_id=agent_id, user_id=user.id)
    return _to_response(updated)


@router.put("/{agent_id}/folder", response_model=BindFolderResponse)
@limiter.limit(_RATE)
async def bind_folder(
    request: Request, agent_id: int, body: BindFolderRequest, user: User = Depends(get_current_user)
) -> BindFolderResponse:
    """Bind a sandboxed folder to an agent (persisted; re-validated on every use).

    The path is validated against ``SANDBOX_ALLOWED_ROOTS`` here and again when the sandbox is
    materialized at chat time. Binding stores only the path — no container is created yet.
    """
    await _owned_agent_or_error(agent_id, user)
    path = validate_grantable_folder(body.path)
    updated = await agent_repository.set_config_value(agent_id, "folder", path)
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    logger.info("agent_folder_bound", agent_id=agent_id, user_id=user.id, folder=path)
    return BindFolderResponse(id=agent_id, folder=path)


@router.delete("/{agent_id}/folder", response_model=BindFolderResponse)
@limiter.limit(_RATE)
async def unbind_folder(
    request: Request, agent_id: int, user: User = Depends(get_current_user)
) -> BindFolderResponse:
    """Remove an agent's bound folder."""
    await _owned_agent_or_error(agent_id, user)
    updated = await agent_repository.set_config_value(agent_id, "folder", None)
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    logger.info("agent_folder_unbound", agent_id=agent_id, user_id=user.id)
    return BindFolderResponse(id=agent_id, folder=None)


@router.put("/{agent_id}/database", response_model=BindDatabaseResponse)
@limiter.limit(_RATE)
async def bind_database(
    request: Request, agent_id: int, body: BindDatabaseRequest, user: User = Depends(get_current_user)
) -> BindDatabaseResponse:
    """Bind a read-only database to an agent, persisting the password encrypted at rest.

    The connection is tested before it is stored. When ``ENCRYPTION_KEY`` is unset the password is
    NOT persisted (secure fallback): the binding keeps only metadata and the user must re-enter the
    password per session via ``/data-agent/connect-db``.
    """
    await _owned_agent_or_error(agent_id, user)

    url = build_db_url(body.driver, body.username, body.password, body.host, body.port, body.database, body.sslmode)
    try:
        await connect_readonly(url)
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_db_bind_connect_failed", agent_id=agent_id, error_type=type(e).__name__)
        raise HTTPException(
            status_code=400,
            detail="Falha ao conectar ao banco. Verifique host, porta, credenciais e conectividade.",
        )

    persisted = is_encryption_available()
    db_conf = {
        "driver": body.driver,
        "host": body.host,
        "port": body.port,
        "database": body.database,
        "username": body.username,
        "sslmode": body.sslmode,
        "password_encrypted": encrypt(body.password) if persisted else None,
    }
    updated = await agent_repository.set_config_value(agent_id, "database", db_conf)
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    logger.info("agent_database_bound", agent_id=agent_id, user_id=user.id, password_persisted=persisted)
    return BindDatabaseResponse(id=agent_id, database=_db_summary(updated.config or {}), password_persisted=persisted)


@router.delete("/{agent_id}/database", response_model=BindDatabaseResponse)
@limiter.limit(_RATE)
async def unbind_database(
    request: Request, agent_id: int, user: User = Depends(get_current_user)
) -> BindDatabaseResponse:
    """Remove an agent's bound database."""
    await _owned_agent_or_error(agent_id, user)
    updated = await agent_repository.set_config_value(agent_id, "database", None)
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    logger.info("agent_database_unbound", agent_id=agent_id, user_id=user.id)
    return BindDatabaseResponse(id=agent_id, database=None, password_persisted=False)


@router.delete("/{agent_id}")
@limiter.limit(_RATE)
async def delete_agent(request: Request, agent_id: int, user: User = Depends(get_current_user)) -> dict:
    """Delete one of the authenticated user's agents."""
    await _owned_agent_or_error(agent_id, user)
    await agent_repository.delete_agent(agent_id)
    logger.info("agent_api_deleted", agent_id=agent_id, user_id=user.id)
    return {"deleted": True}
