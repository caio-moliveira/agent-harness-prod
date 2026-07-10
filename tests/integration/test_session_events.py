"""Integration tests for the session episodic event log + audit trail (#10).

Three pre-agreed seams:
  1. ``SessionEventRepository`` — persistence: record + list, scoped to a session.
  2. ``classify_tool_event`` / ``record_tool_event`` — runtime write-point mapping (pure + persisted).
  3. ``GET /api/v1/sessions/{session_id}/events`` — the HTTP boundary, owner-scoped.

The runtime streaming hook itself is not a separate seam: it is exercised indirectly through
seam 2, so we never drive the full agent (no real OpenAI calls) to prove the log fills up.
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


# ---------------------------------------------------------------------------
# Seam 1 — SessionEventRepository (persistence)
# ---------------------------------------------------------------------------

class TestSessionEventRepository:
    async def test_record_and_list_scoped_to_session(self, client: AsyncClient):
        from src.app.core.session.event_repository import SessionEventRepository

        repo = SessionEventRepository()
        sid = f"sess-{uuid.uuid4()}"
        other = f"sess-{uuid.uuid4()}"

        await repo.record_event(
            user_id=1, session_id=sid, event_type="query_executed",
            agent_id=42, payload={"sql": "SELECT 1"}, scope="db:sales",
        )
        await repo.record_event(
            user_id=1, session_id=sid, event_type="document_read",
            agent_id=42, payload={"path": "/workspace/a.csv"},
        )
        # An event on a different session must not leak into this session's log.
        await repo.record_event(user_id=1, session_id=other, event_type="skill_used")

        events = await repo.get_session_events(sid)
        assert [e.event_type for e in events] == ["query_executed", "document_read"]
        first = events[0]
        assert first.user_id == 1
        assert first.agent_id == 42
        assert first.scope == "db:sales"
        assert first.payload["sql"] == "SELECT 1"

    async def test_empty_session_has_no_events(self, client: AsyncClient):
        from src.app.core.session.event_repository import SessionEventRepository

        events = await SessionEventRepository().get_session_events(f"sess-{uuid.uuid4()}")
        assert events == []


# ---------------------------------------------------------------------------
# Seam 2 — runtime write-point mapping
# ---------------------------------------------------------------------------

class TestClassifyToolEvent:
    def test_maps_known_tools(self):
        from src.app.core.session.event_recorder import classify_tool_event

        assert classify_tool_event("run_sql") == "query_executed"
        assert classify_tool_event("read_file") == "document_read"
        assert classify_tool_event("grep") == "document_read"
        assert classify_tool_event("glob") == "document_read"
        assert classify_tool_event("write_file") == "file_written"
        assert classify_tool_event("edit_file") == "file_written"

    def test_unauditable_tool_returns_none(self):
        from src.app.core.session.event_recorder import classify_tool_event

        assert classify_tool_event("buscar_memoria") is None
        assert classify_tool_event("something_else") is None


class TestRecordToolEvent:
    async def test_auditable_tool_is_recorded(self, client: AsyncClient):
        from src.app.core.session.event_recorder import record_tool_event
        from src.app.core.session.event_repository import SessionEventRepository

        repo = SessionEventRepository()
        sid = f"sess-{uuid.uuid4()}"
        await record_tool_event(
            repo, user_id=1, agent_id=7, session_id=sid,
            tool_name="run_sql", tool_input="SELECT * FROM sales", scope="db:sales",
        )
        events = await repo.get_session_events(sid)
        assert len(events) == 1
        assert events[0].event_type == "query_executed"
        assert "SELECT * FROM sales" in events[0].payload["input"]

    async def test_unauditable_tool_records_nothing(self, client: AsyncClient):
        from src.app.core.session.event_recorder import record_tool_event
        from src.app.core.session.event_repository import SessionEventRepository

        repo = SessionEventRepository()
        sid = f"sess-{uuid.uuid4()}"
        await record_tool_event(repo, user_id=1, agent_id=7, session_id=sid, tool_name="buscar_memoria")
        assert await repo.get_session_events(sid) == []

    async def test_no_user_records_nothing(self, client: AsyncClient):
        from src.app.core.session.event_recorder import record_tool_event
        from src.app.core.session.event_repository import SessionEventRepository

        repo = SessionEventRepository()
        sid = f"sess-{uuid.uuid4()}"
        await record_tool_event(repo, user_id=None, agent_id=7, session_id=sid, tool_name="run_sql")
        assert await repo.get_session_events(sid) == []


# ---------------------------------------------------------------------------
# Seam 3 — GET /api/v1/sessions/{session_id}/events (owner-scoped HTTP boundary)
# ---------------------------------------------------------------------------

class TestSessionEventsEndpoint:
    async def _make_session(self, client: AsyncClient, token: str) -> str:
        resp = await client.post("/api/v1/auth/session", headers=_auth(token))
        assert resp.status_code == 200
        return resp.json()["session_id"]

    async def test_lists_events_for_own_session(self, client: AsyncClient, user_token):
        from src.app.core.session.event_repository import SessionEventRepository

        session_id = await self._make_session(client, user_token)
        # The first registered user (user_token fixture) has id 1 on a fresh per-test DB.
        await SessionEventRepository().record_event(
            user_id=1, session_id=session_id, event_type="query_executed", payload={"sql": "SELECT 1"}
        )
        resp = await client.get(f"/api/v1/sessions/{session_id}/events", headers=_auth(user_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 1
        assert body[0]["event_type"] == "query_executed"
        assert body[0]["payload"]["sql"] == "SELECT 1"

    async def test_requires_auth(self, client: AsyncClient, user_token):
        session_id = await self._make_session(client, user_token)
        resp = await client.get(f"/api/v1/sessions/{session_id}/events")
        assert resp.status_code == 401

    async def test_cannot_read_another_users_session_events(self, client: AsyncClient, user_token):
        session_id = await self._make_session(client, user_token)
        attacker = await _register_and_token(client, "events-attacker@example.com")
        resp = await client.get(f"/api/v1/sessions/{session_id}/events", headers=_auth(attacker))
        assert resp.status_code == 403

    async def test_nonexistent_session_404(self, client: AsyncClient, user_token):
        resp = await client.get(f"/api/v1/sessions/{uuid.uuid4()}/events", headers=_auth(user_token))
        assert resp.status_code == 404
