"""Success-metrics API (#21): the PRD success metrics as JSON, for dashboards or quick checks.

The same values are exported to Prometheus (and thus Grafana) via the collector registered in
``setup_metrics``; this endpoint is a convenient authenticated JSON view of the current snapshot.
"""

from fastapi import APIRouter, Depends, Request

from src.app.api.security.limiter import limiter
from src.app.api.v1.auth import get_current_user
from src.app.core.common.config import settings
from src.app.core.metrics.success_metrics import compute_success_metrics
from src.app.core.user.user_model import User

router = APIRouter()


@router.get("/success")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["success_metrics"][0])
async def success_metrics(request: Request, user: User = Depends(get_current_user)) -> dict:
    """Return the current PRD success metrics (adoption, rework, traceability, skill evolution)."""
    return compute_success_metrics()
