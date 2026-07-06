"""Agent management API: CRUD over a user's persisted agent configurations.

Every route is scoped to the authenticated user. An agent is owned by exactly one user;
any attempt to read or mutate another user's agent returns 403. Agents are pure
configuration consumed by the shared Data Agent runtime — creating one never provisions
code or a deployment.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from src.app.api.security.limiter import limiter
from src.app.api.v1.auth import get_current_user
from src.app.core.agent.agent_dtos import AgentCreate, AgentResponse, AgentUpdate
from src.app.core.agent.agent_model import Agent
from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.user.user_model import User
from src.app.init import agent_repository

router = APIRouter()

_RATE = settings.RATE_LIMIT_ENDPOINTS["agents"][0]


def _to_response(agent: Agent) -> AgentResponse:
    """Map an Agent entity to its API response."""
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        system_prompt=agent.system_prompt,
        config=agent.config or {},
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
    agent = await agent_repository.create_agent(user.id, body.name, body.system_prompt)
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
    """Update an agent's name and/or system prompt."""
    await _owned_agent_or_error(agent_id, user)
    updated = await agent_repository.update_agent(agent_id, name=body.name, system_prompt=body.system_prompt)
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    logger.info("agent_api_updated", agent_id=agent_id, user_id=user.id)
    return _to_response(updated)


@router.delete("/{agent_id}")
@limiter.limit(_RATE)
async def delete_agent(request: Request, agent_id: int, user: User = Depends(get_current_user)) -> dict:
    """Delete one of the authenticated user's agents."""
    await _owned_agent_or_error(agent_id, user)
    await agent_repository.delete_agent(agent_id)
    logger.info("agent_api_deleted", agent_id=agent_id, user_id=user.id)
    return {"deleted": True}
