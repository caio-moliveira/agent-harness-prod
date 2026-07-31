"""Integration tests for the liveness/readiness probes (#75).

These exist because the previous health check could not fail: it called an ``async`` repository
method without awaiting it, so the database was never queried and the endpoint always answered
"healthy". A probe that cannot fail is worse than no probe — the orchestrator keeps routing traffic
to a broken instance. The decisive test here is the one that breaks the database on purpose.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class TestLiveness:
    """Liveness answers for the PROCESS only — never for its dependencies."""

    async def test_alive_when_the_process_is_up(self, client: AsyncClient):
        """A running process answers the liveness probe."""
        resp = await client.get("/api/v1/health/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    async def test_stays_alive_even_when_the_database_is_down(self, client: AsyncClient):
        """A DB outage must NOT fail liveness.

        Failing here would make the orchestrator restart healthy processes, turning a recoverable
        outage into a crash loop.
        """
        with patch("src.app.api.v1.api._ping_database", side_effect=OSError("connection refused")):
            resp = await client.get("/api/v1/health/live")
        assert resp.status_code == 200


class TestReadiness:
    """Readiness answers "can I serve traffic?" — dependencies included."""

    async def test_ready_when_the_database_answers(self, client: AsyncClient):
        """A reachable database means this instance can take traffic."""
        resp = await client.get("/api/v1/health/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["components"]["database"] == "healthy"

    async def test_not_ready_when_the_database_is_unreachable(self, client: AsyncClient):
        """503 so the load balancer drains this instance while the process keeps running."""
        with patch("src.app.api.v1.api._ping_database", side_effect=OSError("connection refused")):
            resp = await client.get("/api/v1/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["components"]["database"] == "unhealthy"


class TestHealthSummary:
    """The human/dashboard endpoint reports degraded instead of lying."""

    async def test_healthy_with_a_working_database(self, client: AsyncClient):
        """The summary endpoint reports healthy when the database answers."""
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    async def test_degraded_when_the_database_fails(self, client: AsyncClient):
        """The summary endpoint reports degraded (503) instead of a comforting lie."""
        # The regression this whole module guards: with the unawaited call, this returned 200
        # "healthy" with the database on fire.
        with patch("src.app.api.v1.api._ping_database", side_effect=OSError("connection refused")):
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"
        assert resp.json()["components"]["database"] == "unhealthy"
