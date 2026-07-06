"""Integration tests for human-in-the-loop confirmation (#19, RF-16).

An outward-facing action is parked as pending and only executes on explicit confirmation:
  - request() does NOT execute the side-effect;
  - confirm() runs it exactly once and marks it confirmed;
  - reject() cancels it (never executes);
  - a non-owner can neither confirm nor reject.
"""

import uuid

import pytest
from httpx import AsyncClient

from tests.integration.conftest import TEST_PASSWORD

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_token(client: AsyncClient, email: str) -> str:
    resp = await client.post("/api/v1/auth/register", json={"email": email, "password": TEST_PASSWORD})
    assert resp.status_code == 200
    return resp.json()["token"]["access_token"]


class TestHitlService:
    async def test_request_does_not_execute(self, client: AsyncClient):
        from src.app.core.hitl import HitlService, PendingActionRepository, register_executor

        calls = []
        register_executor("test_send", lambda a: _record(calls, a))
        svc = HitlService(PendingActionRepository())
        action = await svc.request(1, "s1", "test_send", {"to": "x@y.z"})
        assert action.status == "pending"
        assert calls == []  # side-effect NOT run yet

    async def test_confirm_executes_once(self, client: AsyncClient):
        from src.app.core.hitl import HitlService, PendingActionRepository, register_executor

        calls = []
        register_executor("test_send", lambda a: _record(calls, a))
        svc = HitlService(PendingActionRepository())
        action = await svc.request(1, "s1", "test_send", {"to": "x@y.z"})

        await svc.confirm(action.id, 1)
        assert len(calls) == 1

        # Re-confirming a settled action is refused (no double send).
        from src.app.core.hitl import ConfirmationError

        with pytest.raises(ConfirmationError):
            await svc.confirm(action.id, 1)
        assert len(calls) == 1

    async def test_reject_never_executes(self, client: AsyncClient):
        from src.app.core.hitl import HitlService, PendingActionRepository, register_executor

        calls = []
        register_executor("test_send", lambda a: _record(calls, a))
        svc = HitlService(PendingActionRepository())
        action = await svc.request(1, "s1", "test_send", {})
        await svc.reject(action.id, 1)
        assert calls == []

        from src.app.core.hitl import ConfirmationError

        with pytest.raises(ConfirmationError):
            await svc.confirm(action.id, 1)  # cannot confirm a rejected action

    async def test_other_user_cannot_confirm(self, client: AsyncClient):
        from src.app.core.hitl import ConfirmationError, HitlService, PendingActionRepository, register_executor

        calls = []
        register_executor("test_send", lambda a: _record(calls, a))
        svc = HitlService(PendingActionRepository())
        action = await svc.request(1, "s1", "test_send", {})
        with pytest.raises(ConfirmationError):
            await svc.confirm(action.id, 999)
        assert calls == []


async def _record(calls, action):
    calls.append(action.id)
    return {"ok": True}


class TestHitlApi:
    async def test_confirm_flow_over_http(self, client: AsyncClient):
        from src.app.init import pending_action_repository

        token = await _register_and_token(client, f"hitl-{uuid.uuid4()}@e.com")
        # Park an export for user 1 (first registered user on the fresh per-test DB).
        action = await pending_action_repository.create(1, "sess", "export_artifact", {"path": "/tmp/r.docx"})

        listed = await client.get("/api/v1/hitl/pending", headers=_auth(token))
        assert listed.status_code == 200
        assert any(a["id"] == action.id for a in listed.json())

        confirmed = await client.post(f"/api/v1/hitl/{action.id}/confirm", headers=_auth(token))
        assert confirmed.status_code == 200
        assert confirmed.json()["confirmed"] is True

        # Now it is settled → confirming again is a 409.
        again = await client.post(f"/api/v1/hitl/{action.id}/confirm", headers=_auth(token))
        assert again.status_code == 409

    async def test_another_user_gets_403(self, client: AsyncClient):
        from src.app.init import pending_action_repository

        await _register_and_token(client, f"owner-{uuid.uuid4()}@e.com")  # user 1
        attacker = await _register_and_token(client, f"attacker-{uuid.uuid4()}@e.com")  # user 2
        action = await pending_action_repository.create(1, "sess", "export_artifact", {})

        resp = await client.post(f"/api/v1/hitl/{action.id}/confirm", headers=_auth(attacker))
        assert resp.status_code == 403

    async def test_missing_action_404(self, client: AsyncClient):
        token = await _register_and_token(client, f"u-{uuid.uuid4()}@e.com")
        resp = await client.post("/api/v1/hitl/999999/reject", headers=_auth(token))
        assert resp.status_code == 404
