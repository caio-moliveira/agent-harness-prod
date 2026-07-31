"""Daily token budget: the enforcement layer over :mod:`usage_repository` (#73).

Policy, deliberately simple and explicit:

- The budget is checked **at the start of a turn**, against what the user already spent today. A
  turn is never killed mid-flight for budget — the user would lose work for a limit they cannot
  see coming, and the cost is already sunk anyway. The turn that crosses the line is allowed to
  finish; the *next* one is refused.
- ``TOKEN_BUDGET_DAILY = 0`` disables enforcement entirely (the default): a self-hosted, single-user
  deployment or one on a local Ollama has no per-user cost to govern.
- A per-user override on the ``User`` row beats the global default, so one account can be raised
  without loosening the policy for everyone.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

from src.app.core.common.config import settings
from src.app.core.common.logging import logger
from src.app.core.metrics.metrics import user_token_budget_exhausted_total
from src.app.core.usage.usage_repository import TokenUsageRepository

_repo = TokenUsageRepository()


@dataclass(frozen=True)
class BudgetStatus:
    """Where a user stands against their daily token budget."""

    limit: int  # 0 = unlimited
    used: int
    remaining: int  # 0 when exhausted; equals `limit - used` clamped at 0
    exceeded: bool
    resets_at: datetime  # next UTC midnight

    @property
    def enabled(self) -> bool:
        """Whether a budget is being enforced at all."""
        return self.limit > 0


def _next_utc_midnight() -> datetime:
    now = datetime.now(UTC)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def effective_limit(user_limit: Optional[int]) -> int:
    """The limit that applies to a user: their override when set, else the global default."""
    if user_limit is not None and user_limit > 0:
        return user_limit
    if user_limit == 0:
        # An explicit 0 on the user means "unlimited for this account", overriding the global cap.
        return 0
    return settings.TOKEN_BUDGET_DAILY


async def get_status(user_id: int, user_limit: Optional[int] = None) -> BudgetStatus:
    """Compute this user's budget standing for the current UTC day."""
    limit = effective_limit(user_limit)
    used = await _repo.total_for_day(user_id)
    if limit <= 0:
        return BudgetStatus(limit=0, used=used, remaining=0, exceeded=False, resets_at=_next_utc_midnight())
    remaining = max(0, limit - used)
    return BudgetStatus(
        limit=limit,
        used=used,
        remaining=remaining,
        exceeded=used >= limit,
        resets_at=_next_utc_midnight(),
    )


async def check_budget(user_id: int, user_limit: Optional[int] = None) -> BudgetStatus:
    """Budget standing, counting an exhausted budget in the metric so it is visible in Grafana."""
    status = await get_status(user_id, user_limit)
    if status.exceeded:
        user_token_budget_exhausted_total.inc()
        logger.info("token_budget_exhausted", user_id=user_id, used=status.used, limit=status.limit)
    return status


async def record_turn_usage(user_id: int, input_tokens: int, output_tokens: int) -> None:
    """Persist one turn's token consumption. Never raises — accounting must not break a turn."""
    if input_tokens <= 0 and output_tokens <= 0:
        return
    try:
        await _repo.add_usage(user_id, input_tokens, output_tokens)
    except Exception:
        logger.exception("token_usage_persist_failed", user_id=user_id)
