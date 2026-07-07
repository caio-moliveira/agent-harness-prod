"""Integration tests for durable conversation history (chat message persistence).

Two seams:
  1. ``ChatMessageRepository`` — append-only persistence, chronological reads, session-scoped,
     cursor pagination.
  2. ``GET /api/v1/data-agent/messages`` — the HTTP boundary, returns the caller session's history.
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
# Seam 1 — ChatMessageRepository (persistence)
# ---------------------------------------------------------------------------


class TestChatMessageRepository:
    async def test_appends_and_reads_chronologically_scoped(self, client: AsyncClient):
        from src.app.core.session.message_repository import ChatMessageRepository

        repo = ChatMessageRepository()
        sid = f"sess-{uuid.uuid4()}"
        other = f"sess-{uuid.uuid4()}"

        await repo.add_message(sid, user_id=1, role="user", content="Quantas vendas em 2024?")
        await repo.add_message(sid, user_id=1, role="assistant", content="Foram 1.234 vendas.")
        # A message in another session must not leak into this session's history.
        await repo.add_message(other, user_id=1, role="user", content="outra conversa")

        rows = await repo.get_messages(sid)
        assert [(r.role, r.content) for r in rows] == [
            ("user", "Quantas vendas em 2024?"),
            ("assistant", "Foram 1.234 vendas."),
        ]
        assert await repo.count(sid) == 2

    async def test_empty_session_has_no_history(self, client: AsyncClient):
        from src.app.core.session.message_repository import ChatMessageRepository

        repo = ChatMessageRepository()
        sid = f"sess-{uuid.uuid4()}"
        assert await repo.get_messages(sid) == []
        assert await repo.count(sid) == 0

    async def test_cursor_pagination_returns_older_page(self, client: AsyncClient):
        from src.app.core.session.message_repository import ChatMessageRepository

        repo = ChatMessageRepository()
        sid = f"sess-{uuid.uuid4()}"
        created = []
        for i in range(5):
            created.append(await repo.add_message(sid, user_id=1, role="user", content=f"msg {i}"))

        # Newest 2 by loading the latest page, then step back with the cursor.
        latest = await repo.get_messages(sid, limit=2)
        assert [r.content for r in latest] == ["msg 3", "msg 4"]
        older = await repo.get_messages(sid, limit=2, before_id=latest[0].id)
        assert [r.content for r in older] == ["msg 1", "msg 2"]


# ---------------------------------------------------------------------------
# Seam 2 — GET /api/v1/data-agent/messages (session-scoped HTTP boundary)
# ---------------------------------------------------------------------------


class TestDataAgentMessagesEndpoint:
    async def _make_session(self, client: AsyncClient, token: str) -> dict:
        resp = await client.post("/api/v1/auth/session", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        return resp.json()

    async def test_returns_own_session_history(self, client: AsyncClient, user_token):
        from src.app.core.session.message_repository import ChatMessageRepository

        session = await self._make_session(client, user_token)
        # The first registered user (user_token fixture) has id 1 on a fresh per-test DB.
        repo = ChatMessageRepository()
        await repo.add_message(session["session_id"], user_id=1, role="user", content="oi")
        await repo.add_message(session["session_id"], user_id=1, role="assistant", content="olá!")

        sid = session["session_id"]
        resp = await client.get(f"/api/v1/data-agent/{sid}/messages", headers=_auth(session["token"]["access_token"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [(m["role"], m["content"]) for m in body["messages"]] == [
            ("user", "oi"),
            ("assistant", "olá!"),
        ]

    async def test_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/data-agent/any-session/messages")
        assert resp.status_code == 401

    async def test_new_session_history_is_empty(self, client: AsyncClient, user_token):
        session = await self._make_session(client, user_token)
        sid = session["session_id"]
        resp = await client.get(f"/api/v1/data-agent/{sid}/messages", headers=_auth(session["token"]["access_token"]))
        assert resp.status_code == 200, resp.text
        assert resp.json()["messages"] == []

    def test_agent_window_appends_new_and_skips_empty(self):
        from types import SimpleNamespace

        from src.app.api.v1.data_agent import _agent_messages

        history = [
            SimpleNamespace(role="user", content="quanto vendemos?"),
            SimpleNamespace(role="assistant", content="R$ 1.234"),
            SimpleNamespace(role="assistant", content="   "),  # tool-only turn — no text, skipped
        ]
        msgs = _agent_messages(history, "e em fevereiro?")
        assert [(m.role, m.content) for m in msgs] == [
            ("user", "quanto vendemos?"),
            ("assistant", "R$ 1.234"),
            ("user", "e em fevereiro?"),
        ]

    async def test_history_carries_per_turn_tool_activity(self, client: AsyncClient, user_token):
        from src.app.init import chat_message_repository, chat_message_step_repository

        session = await self._make_session(client, user_token)
        sid = session["session_id"]
        await chat_message_repository.add_message(sid, 1, "user", "quantas vendas?")
        assistant = await chat_message_repository.add_message(sid, 1, "assistant", "Foram 1.234.")
        await chat_message_step_repository.add_steps(
            sid,
            assistant.id,
            [
                {"name": "run_sql", "input": "SELECT count(*) FROM vendas", "output": "1234"},
                {"name": "gerar_planilha", "input": None, "output": None},
            ],
        )

        resp = await client.get(f"/api/v1/data-agent/{sid}/messages", headers=_auth(session["token"]["access_token"]))
        assert resp.status_code == 200, resp.text
        msgs = resp.json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["steps"] == []  # the user turn has no activity
        assert [s["name"] for s in msgs[1]["steps"]] == ["run_sql", "gerar_planilha"]
        assert msgs[1]["steps"][0]["output"] == "1234"
