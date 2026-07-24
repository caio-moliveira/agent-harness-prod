"""Rate limiting configuration for the application.

This module configures rate limiting using slowapi. Limits are keyed by the authenticated
principal (the bearer token's `sub` claim — a user or session id), not the caller's IP: keying on
IP alone would let every user behind the same NAT/corporate network share one bucket while doing
nothing to stop a single abusive account that rotates IPs. This mirrors how OpenAI/Anthropic/Google
rate-limit their own APIs — the account is the unit of enforcement, IP is only a pre-auth fallback
(login/register/health, where there is no account yet).
"""

import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.app.api.security.auth import verify_token
from src.app.core.common.config import settings


def _rate_limit_key(request: Request) -> str:
    """Key rate limits by the caller's authenticated identity, falling back to IP.

    ``verify_token`` is a pure JWT decode (no DB round-trip), so this stays cheap enough to run on
    every request ahead of the route body. Any failure — missing header, malformed token, expired
    signature — falls back to the remote address, the same treatment pre-auth routes always get.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        try:
            subject = verify_token(token)
        except ValueError:
            subject = None
        if subject:
            return f"user:{subject}"
    return f"ip:{get_remote_address(request)}"


# NOTE: deliberately not using slowapi's `headers_enabled=True` — it makes the `@limiter.limit(...)`
# decorator inject X-RateLimit-* headers into the *decorated function's return value*, which only
# works when that return value is a raw `Response` (or the route declares a FastAPI-injectable
# `response: Response` param). Every route in this app returns a typed Pydantic model instead, so
# `headers_enabled=True` throws on literally every successful rate-limited request. We still want
# the client-facing contract (Retry-After on 429) — built by hand below, only on the error path,
# which already works with a real Response object regardless of that flag.
limiter = Limiter(key_func=_rate_limit_key, default_limits=settings.RATE_LIMIT_DEFAULT)


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 handler with a `Retry-After` the client can actually act on.

    Matches this app's normal error shape (``{"detail": ...}``, same as ``HTTPException``) instead
    of slowapi's default ``{"error": ...}``, so existing client-side error parsing picks it up.
    Header computation is best-effort: if it fails for any reason, the client still gets a correct
    429 with a friendly message, just without the extra headers.
    """
    response = JSONResponse(
        {"detail": "Muitas requisições. Aguarde um instante e tente novamente."},
        status_code=429,
    )
    current_limit = getattr(request.state, "view_rate_limit", None)
    if current_limit is not None:
        try:
            reset_at, remaining = limiter.limiter.get_window_stats(current_limit[0], *current_limit[1])
            response.headers["Retry-After"] = str(max(1, int(reset_at - time.time())))
            response.headers["X-RateLimit-Limit"] = str(current_limit[0].amount)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(reset_at)
        except Exception:  # noqa: BLE001 - headers are a bonus, never fail the 429 over them
            pass
    return response


def setup_rate_limit(app: FastAPI) -> None:
    """Set up rate limiter for the FastAPI application.

    Args:
        app: The FastAPI application instance
    """
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
