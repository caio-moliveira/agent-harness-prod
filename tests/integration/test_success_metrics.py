"""Integration tests for the PRD success metrics (#21).

compute_success_metrics derives adoption / rework / traceability / skill-evolution from the
persisted record (events #10, corrections #20, skills #17). The collector yields them for
Prometheus; the JSON endpoint returns the snapshot.
"""

import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestComputeMetrics:
    async def test_metrics_from_seeded_record(self, client: AsyncClient):
        from src.app.core.learning import CorrectionRepository
        from src.app.core.metrics.success_metrics import compute_success_metrics
        from src.app.core.session.event_repository import SessionEventRepository

        events = SessionEventRepository()
        # Two artifacts fully sourced, one with an unsourced claim.
        await events.record_event(1, f"s-{uuid.uuid4()}", "artifact_generated", agent_id=7, payload={"unsourced": 0})
        await events.record_event(1, f"s-{uuid.uuid4()}", "artifact_generated", agent_id=7, payload={"unsourced": 0})
        await events.record_event(1, f"s-{uuid.uuid4()}", "artifact_generated", agent_id=7, payload={"unsourced": 2})
        # One correction signal → rework.
        await CorrectionRepository().create(1, 7, None, note="fix")

        m = compute_success_metrics()
        assert m["artifacts_total"] == 3
        assert m["fully_traceable_ratio"] == pytest.approx(2 / 3)
        assert m["rework_rate"] == pytest.approx(1 / 3)

    async def test_zero_artifacts_is_safe(self, client: AsyncClient):
        from src.app.core.metrics.success_metrics import compute_success_metrics

        m = compute_success_metrics()
        assert m["artifacts_total"] == 0
        assert m["fully_traceable_ratio"] == 0.0
        assert m["rework_rate"] == 0.0

    async def test_approved_refined_skill_counts(self, client: AsyncClient, user_token):
        from src.app.core.metrics.success_metrics import compute_success_metrics

        created = (
            await client.post("/api/v1/skills", json={"name": "S", "body": "v1"}, headers=_auth(user_token))
        ).json()
        sid = created["id"]
        # Refine (→ v2, draft) then approve.
        await client.patch(f"/api/v1/skills/{sid}", json={"body": "v2"}, headers=_auth(user_token))
        for status in ("in_review", "approved"):
            await client.post(f"/api/v1/skills/{sid}/status", json={"status": status}, headers=_auth(user_token))

        assert compute_success_metrics()["approved_refined_skills"] >= 1


class TestCollector:
    async def test_collector_yields_metric_families(self, client: AsyncClient):
        from src.app.core.metrics.success_metrics import SuccessMetricsCollector

        names = {fam.name for fam in SuccessMetricsCollector().collect()}
        assert "harness_artifacts_total" in names
        assert "harness_fully_traceable_ratio" in names
        assert "harness_rework_rate" in names


class TestMetricsApi:
    async def test_success_endpoint_returns_metrics(self, client: AsyncClient, user_token):
        resp = await client.get("/api/v1/metrics/success", headers=_auth(user_token))
        assert resp.status_code == 200
        body = resp.json()
        assert "artifacts_total" in body
        assert "active_sessions_total" in body

    async def test_success_endpoint_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/metrics/success")
        assert resp.status_code == 401
