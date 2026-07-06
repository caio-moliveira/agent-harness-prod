"""Skill library API: CRUD over a user's reusable instruction documents (SKILL.md).

Every route is scoped to the authenticated user; skills are private to their owner. A skill is an
instruction document — never executable code — attachable to that user's agents.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from pydantic import BaseModel

from src.app.api.security.limiter import limiter
from src.app.api.v1.auth import get_current_user
from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.skill.registry import fetch_registry_index, fetch_registry_skill, is_registry_enabled
from src.app.core.skill.skill_dtos import SkillCreate, SkillResponse, SkillUpdate
from src.app.core.skill.skill_model import Skill
from src.app.core.user.user_model import User
from src.app.init import skill_repository


class FetchSkillRequest(BaseModel):
    """Request body to import a skill from the vetted registry by slug."""

    slug: str

router = APIRouter()

_RATE = settings.RATE_LIMIT_ENDPOINTS["skills"][0]


def _to_response(skill: Skill) -> SkillResponse:
    """Map a Skill entity to its API response."""
    return SkillResponse(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        body=skill.body,
        when_to_use=skill.when_to_use,
        sources=skill.sources,
        steps=skill.steps,
        output_format=skill.output_format,
        source=skill.source,
    )


async def _owned_skill_or_error(skill_id: int, user: User) -> Skill:
    """Return the skill if it exists and belongs to the user, else raise 404/403."""
    skill = await skill_repository.get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.user_id != user.id:
        logger.warning("skill_access_denied", skill_id=skill_id, user_id=user.id)
        raise HTTPException(status_code=403, detail="Cannot access another user's skill")
    return skill


@router.post("", response_model=SkillResponse)
@limiter.limit(_RATE)
async def create_skill(
    request: Request, body: SkillCreate, user: User = Depends(get_current_user)
) -> SkillResponse:
    """Author a new skill in the user's library."""
    skill = await skill_repository.create_skill(
        user.id,
        body.name,
        body.description,
        body.body,
        source="authored",
        when_to_use=body.when_to_use,
        sources=body.sources,
        steps=body.steps,
        output_format=body.output_format,
    )
    logger.info("skill_api_created", skill_id=skill.id, user_id=user.id)
    return _to_response(skill)


@router.get("", response_model=List[SkillResponse])
@limiter.limit(_RATE)
async def list_skills(request: Request, user: User = Depends(get_current_user)) -> List[SkillResponse]:
    """List all skills in the user's library."""
    skills = await skill_repository.get_user_skills(user.id)
    return [_to_response(s) for s in skills]


@router.get("/registry", response_model=List[dict])
@limiter.limit(_RATE)
async def list_registry(request: Request, user: User = Depends(get_current_user)) -> List[dict]:
    """List skills available in the vetted registry (empty when fetch is disabled)."""
    if not is_registry_enabled():
        raise HTTPException(status_code=503, detail="Registro de skills não configurado (SKILL_REGISTRY_URL).")
    try:
        return await fetch_registry_index()
    except Exception as e:  # noqa: BLE001
        logger.warning("skill_registry_index_failed", error_type=type(e).__name__)
        raise HTTPException(status_code=502, detail="Falha ao consultar o registro de skills.")


@router.post("/fetch", response_model=SkillResponse)
@limiter.limit(_RATE)
async def fetch_skill(
    request: Request, body: FetchSkillRequest, user: User = Depends(get_current_user)
) -> SkillResponse:
    """Import a skill from the vetted registry, saved as the user's own copy.

    Only the configured registry may be fetched; the imported text is stored as an independent
    copy (``source='fetched'``) so a later upstream change never alters the user's skill.
    """
    if not is_registry_enabled():
        raise HTTPException(status_code=503, detail="Registro de skills não configurado (SKILL_REGISTRY_URL).")
    try:
        fetched = await fetch_registry_skill(body.slug)
    except ValueError:
        raise HTTPException(status_code=400, detail="Slug de skill inválido.")
    except Exception as e:  # noqa: BLE001
        logger.warning("skill_fetch_failed", error_type=type(e).__name__)
        raise HTTPException(status_code=502, detail="Falha ao buscar a skill no registro.")
    if fetched is None:
        raise HTTPException(status_code=404, detail="Skill não encontrada no registro.")

    skill = await skill_repository.create_skill(
        user.id, fetched["name"], fetched["description"], fetched["body"], source="fetched"
    )
    logger.info("skill_fetched", skill_id=skill.id, user_id=user.id, slug=body.slug)
    return _to_response(skill)


@router.get("/{skill_id}", response_model=SkillResponse)
@limiter.limit(_RATE)
async def get_skill(request: Request, skill_id: int, user: User = Depends(get_current_user)) -> SkillResponse:
    """Get one of the user's skills."""
    skill = await _owned_skill_or_error(skill_id, user)
    return _to_response(skill)


@router.patch("/{skill_id}", response_model=SkillResponse)
@limiter.limit(_RATE)
async def update_skill(
    request: Request, skill_id: int, body: SkillUpdate, user: User = Depends(get_current_user)
) -> SkillResponse:
    """Update one of the user's skills."""
    await _owned_skill_or_error(skill_id, user)
    updated = await skill_repository.update_skill(
        skill_id,
        name=body.name,
        description=body.description,
        body=body.body,
        when_to_use=body.when_to_use,
        sources=body.sources,
        steps=body.steps,
        output_format=body.output_format,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    logger.info("skill_api_updated", skill_id=skill_id, user_id=user.id)
    return _to_response(updated)


@router.delete("/{skill_id}")
@limiter.limit(_RATE)
async def delete_skill(request: Request, skill_id: int, user: User = Depends(get_current_user)) -> dict:
    """Delete one of the user's skills."""
    await _owned_skill_or_error(skill_id, user)
    await skill_repository.delete_skill(skill_id)
    logger.info("skill_api_deleted", skill_id=skill_id, user_id=user.id)
    return {"deleted": True}
