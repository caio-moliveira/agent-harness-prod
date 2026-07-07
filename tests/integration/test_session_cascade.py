"""Integration tests for cascade session deletion.

Deleting a session must remove everything it produced — messages, audit events, parked actions, and
generated artifact files — and (on Postgres) not FK-block on its children. The delete endpoint is
owner/session-scoped: only the session's own bearer token may delete it.
"""

import os

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _session(client: AsyncClient, token: str) -> dict:
    resp = await client.post("/api/v1/auth/session", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestSessionCascade:
    async def test_delete_removes_children_and_files(self, client: AsyncClient, user_token, tmp_path):
        from src.app.core.hitl.pending_model import PendingActionStatus
        from src.app.init import (
            chat_message_repository,
            pending_action_repository,
            session_event_repository,
        )

        session = await _session(client, user_token)
        sid = session["session_id"]

        await chat_message_repository.add_message(sid, user_id=1, role="user", content="oi")
        await chat_message_repository.add_message(sid, user_id=1, role="assistant", content="olá")
        await session_event_repository.record_event(user_id=1, session_id=sid, event_type="query_executed")
        artifact = tmp_path / "rel.docx"
        artifact.write_bytes(b"x")
        action = await pending_action_repository.create(
            1, sid, "export_artifact", {"path": str(artifact), "fmt": "docx"}
        )
        await pending_action_repository.set_status(action.id, PendingActionStatus.CONFIRMED)

        resp = await client.delete(f"/api/v1/auth/session/{sid}", headers=_auth(session["token"]["access_token"]))
        assert resp.status_code == 200, resp.text

        # Everything the session produced is gone...
        assert await chat_message_repository.count(sid) == 0
        assert await session_event_repository.get_session_events(sid) == []
        assert await pending_action_repository.list_for_session(sid) == []
        assert not os.path.isfile(str(artifact))  # ...including the generated file

    async def test_cannot_delete_another_users_session(self, client: AsyncClient, user_token):
        session = await _session(client, user_token)
        attacker = await client.post(
            "/api/v1/auth/register", json={"email": "cascade-attacker@example.com", "password": "TestPass123!"}
        )
        attacker_token = attacker.json()["token"]["access_token"]
        attacker_session = await _session(client, attacker_token)

        # The attacker's token only authorizes deleting the attacker's own session.
        resp = await client.delete(
            f"/api/v1/auth/session/{session['session_id']}",
            headers=_auth(attacker_session["token"]["access_token"]),
        )
        assert resp.status_code == 403
