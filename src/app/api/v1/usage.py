"""Usage endpoint: what this account spent today and how much budget is left (#73).

Cost governance is only useful if the user can see it coming — a budget that surfaces for the first
time as a refusal is a support ticket, not a control. The chat UI polls this to show a quiet
indicator, and an operator can answer "how much is user X spending?" without a database session.
"""

from fastapi import APIRouter, Depends, Request

from src.app.api.security.limiter import limiter
from src.app.api.v1.auth import get_current_user
from src.app.api.v1.dtos.usage import UsageResponse
from src.app.core.common.config import settings
from src.app.core.usage import TokenUsageRepository, get_status
from src.app.core.user.user_model import User

router = APIRouter()
_repo = TokenUsageRepository()


@router.get("/usage", response_model=UsageResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["usage"][0])
async def get_usage(request: Request, user: User = Depends(get_current_user)) -> UsageResponse:
    """Return the authenticated user's token usage and budget standing for the current UTC day."""
    row = await _repo.get_usage(user.id)
    status = await get_status(user.id, getattr(user, "token_budget_daily", None))
    return UsageResponse(
        input_tokens=row.input_tokens if row else 0,
        output_tokens=row.output_tokens if row else 0,
        total_tokens=row.total_tokens if row else 0,
        turns=row.turns if row else 0,
        limit=status.limit,
        remaining=status.remaining,
        exceeded=status.exceeded,
        resets_at=status.resets_at,
    )
