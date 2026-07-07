"""Integration tests for inline approval surfacing + artifact download (F1/F4).

Two seams:
  1. ``_hitl_event`` / ``_new_hitl_events`` — turning a parked action into the inline SSE card,
     scoped to the current session and de-duplicated against already-seen actions.
  2. ``GET /api/v1/data-agent/artifacts/{id}/download`` — owner-scoped, confirmed-only file serve.
"""

import uuid
from types import SimpleNamespace

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


async def _session(client: AsyncClient, token: str) -> dict:
    resp = await client.post("/api/v1/auth/session", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Seam 1 — inline approval event shaping
# ---------------------------------------------------------------------------


class TestHitlRequestEvent:
    def test_event_shape_from_artifact_action(self):
        from src.app.api.v1.data_agent import _hitl_event

        action = SimpleNamespace(
            id=7, action_type="export_artifact", payload={"spec": {"title": "Relatório"}, "fmt": "docx"}
        )
        assert _hitl_event(action) == {
            "type": "hitl_request",
            "id": 7,
            "action_type": "export_artifact",
            "title": "Relatório",
            "format": "docx",
        }

    async def test_new_events_scoped_to_session_and_unseen(self, client: AsyncClient):
        from src.app.api.v1.data_agent import _new_hitl_events
        from src.app.init import pending_action_repository

        sid = f"sess-{uuid.uuid4()}"
        a1 = await pending_action_repository.create(1, sid, "export_artifact", {"spec": {"title": "A"}, "fmt": "docx"})
        # An action parked in a different session must never surface in this session's stream.
        await pending_action_repository.create(1, f"sess-{uuid.uuid4()}", "export_artifact", {"spec": {}, "fmt": "pptx"})

        session = SimpleNamespace(id=sid, user_id=1)
        events = await _new_hitl_events(session, known_ids=set())
        assert [e["id"] for e in events] == [a1.id]
        # Already-seen actions are not re-emitted.
        assert await _new_hitl_events(session, known_ids={a1.id}) == []


# ---------------------------------------------------------------------------
# Seam 2 — artifact download endpoint
# ---------------------------------------------------------------------------


class TestArtifactDownload:
    async def _confirmed_action(self, session_id: str, path: str, user_id: int = 1):
        from src.app.core.hitl.pending_model import PendingActionStatus
        from src.app.init import pending_action_repository

        action = await pending_action_repository.create(
            user_id, session_id, "export_artifact", {"path": path, "fmt": "docx"}
        )
        await pending_action_repository.set_status(action.id, PendingActionStatus.CONFIRMED)
        return action

    async def test_downloads_confirmed_artifact(self, client: AsyncClient, user_token, tmp_path):
        session = await _session(client, user_token)
        f = tmp_path / "relatorio.docx"
        f.write_bytes(b"PK-fake-docx")
        action = await self._confirmed_action(session["session_id"], str(f))

        resp = await client.get(
            f"/api/v1/data-agent/artifacts/{action.id}/download",
            headers=_auth(session["token"]["access_token"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.content == b"PK-fake-docx"

    async def test_pending_artifact_is_not_downloadable(self, client: AsyncClient, user_token, tmp_path):
        from src.app.init import pending_action_repository

        session = await _session(client, user_token)
        f = tmp_path / "relatorio.docx"
        f.write_bytes(b"x")
        action = await pending_action_repository.create(
            1, session["session_id"], "export_artifact", {"path": str(f), "fmt": "docx"}
        )
        resp = await client.get(
            f"/api/v1/data-agent/artifacts/{action.id}/download",
            headers=_auth(session["token"]["access_token"]),
        )
        assert resp.status_code == 409, resp.text

    async def test_missing_action_404(self, client: AsyncClient, user_token):
        session = await _session(client, user_token)
        resp = await client.get(
            "/api/v1/data-agent/artifacts/999999/download",
            headers=_auth(session["token"]["access_token"]),
        )
        assert resp.status_code == 404

    async def test_other_user_cannot_download(self, client: AsyncClient, user_token, tmp_path):
        owner_session = await _session(client, user_token)
        f = tmp_path / "relatorio.docx"
        f.write_bytes(b"secret")
        action = await self._confirmed_action(owner_session["session_id"], str(f))

        attacker = await _register_and_token(client, "artifact-attacker@example.com")
        attacker_session = await _session(client, attacker)
        resp = await client.get(
            f"/api/v1/data-agent/artifacts/{action.id}/download",
            headers=_auth(attacker_session["token"]["access_token"]),
        )
        assert resp.status_code == 403
