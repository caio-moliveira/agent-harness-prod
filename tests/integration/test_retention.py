"""Integration tests for retention and LGPD erasure (#75).

"We can delete your data" is a claim that must be true, not aspirational — so erasure is tested
against a user with real rows in every table it touches. Retention is tested for the property that
matters most in the opposite direction: with the windows disabled (the default), a purge must
delete NOTHING. Silent data loss from a default nobody chose is the worse failure.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session as SQLSession
from sqlmodel import select

from src.app.core.db.database import session_scope
from src.app.core.memory.agent_memory_model import AgentMemory
from src.app.core.retention import delete_user_data, purge_expired
from src.app.core.session.message_model import ChatMessage
from src.app.core.session.session_model import Session
from src.app.core.usage.usage_model import TokenUsage
from src.app.core.user.user_model import User

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_user_data(client: AsyncClient, user_token: str) -> tuple[int, str]:
    """Create a session with a message, a memory and a usage row; return (user_id, session_id)."""
    resp = await client.post("/api/v1/auth/session", headers=_auth(user_token))
    session_id = resp.json()["session_id"]

    with session_scope() as db:
        session_row = db.get(Session, session_id)
        user_id = session_row.user_id
        db.add(ChatMessage(session_id=session_id, user_id=user_id, role="user", content="olá"))
        db.add(AgentMemory(user_id=user_id, agent_id=None, summary="lembrete", body="detalhe"))
        db.add(TokenUsage(user_id=user_id, day=datetime.now(UTC).date(), input_tokens=10, output_tokens=5, turns=1))
        db.commit()
    return user_id, session_id


def _counts(db: SQLSession, user_id: int) -> dict[str, int]:
    return {
        "sessions": len(db.exec(select(Session).where(Session.user_id == user_id)).all()),
        "messages": len(db.exec(select(ChatMessage).where(ChatMessage.user_id == user_id)).all()),
        "memories": len(db.exec(select(AgentMemory).where(AgentMemory.user_id == user_id)).all()),
        "usage": len(db.exec(select(TokenUsage).where(TokenUsage.user_id == user_id)).all()),
    }


class TestErasure:
    """LGPD: everything belonging to a user goes, in the order the foreign keys require."""

    async def test_erasure_removes_every_trace_of_the_user(self, client: AsyncClient, user_token):
        """Every user-owned table is emptied and the account row disappears."""
        user_id, _ = await _seed_user_data(client, user_token)
        with session_scope() as db:
            before = _counts(db, user_id)
        assert all(v > 0 for v in before.values()), f"fixture did not seed all tables: {before}"

        report = await delete_user_data(user_id)

        with session_scope() as db:
            after = _counts(db, user_id)
            assert after == {"sessions": 0, "messages": 0, "memories": 0, "usage": 0}
            assert db.get(User, user_id) is None  # the account itself is gone
        assert report.sessions >= 1 and report.memories >= 1 and report.users == 1
        assert not report.errors

    async def test_keep_account_wipes_history_but_leaves_the_login(self, client: AsyncClient, user_token):
        """"Erase my history" is a different request from "delete my account"."""
        user_id, _ = await _seed_user_data(client, user_token)

        report = await delete_user_data(user_id, delete_account=False)

        with session_scope() as db:
            assert _counts(db, user_id) == {"sessions": 0, "messages": 0, "memories": 0, "usage": 0}
            assert db.get(User, user_id) is not None  # can still log in
        assert report.users == 0

    async def test_erasure_of_a_user_without_data_is_a_no_op(self, client: AsyncClient, user_token):
        """A user who never used the product erases cleanly instead of erroring."""
        resp = await client.post("/api/v1/auth/session", headers=_auth(user_token))
        with session_scope() as db:
            user_id = db.get(Session, resp.json()["session_id"]).user_id
            db.delete(db.get(Session, resp.json()["session_id"]))
            db.commit()

        report = await delete_user_data(user_id)
        assert report.sessions == 0 and not report.errors


class TestRetention:
    """Time-based housekeeping — off by default, and precise when on."""

    async def test_disabled_windows_delete_nothing(self, client: AsyncClient, user_token, monkeypatch):
        """The default configuration must never remove a user's data behind their back."""
        from src.app.core.retention import purge as purge_module

        monkeypatch.setattr(purge_module.settings, "RETENTION_MESSAGES_DAYS", 0, raising=False)
        monkeypatch.setattr(purge_module.settings, "RETENTION_USAGE_DAYS", 0, raising=False)
        monkeypatch.setattr(purge_module.settings, "RETENTION_ARTIFACTS_DAYS", 0, raising=False)
        user_id, _ = await _seed_user_data(client, user_token)

        report = await purge_expired()

        with session_scope() as db:
            after = _counts(db, user_id)
        assert report.sessions == 0 and report.usage_rows == 0
        assert after["sessions"] == 1 and after["messages"] == 1

    async def test_expired_sessions_are_removed_whole(self, client: AsyncClient, user_token, monkeypatch):
        """A session past the window goes with its messages — never a half-deleted conversation."""
        from src.app.core.retention import purge as purge_module

        monkeypatch.setattr(purge_module.settings, "RETENTION_MESSAGES_DAYS", 30, raising=False)
        user_id, session_id = await _seed_user_data(client, user_token)
        with session_scope() as db:  # age the session past the window
            row = db.get(Session, session_id)
            row.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=45)
            db.add(row)
            db.commit()

        report = await purge_expired()

        with session_scope() as db:
            after = _counts(db, user_id)
        assert report.sessions == 1
        assert after["sessions"] == 0 and after["messages"] == 0

    async def test_recent_sessions_survive_the_purge(self, client: AsyncClient, user_token, monkeypatch):
        """Only data past the window is touched — today's conversation stays."""
        from src.app.core.retention import purge as purge_module

        monkeypatch.setattr(purge_module.settings, "RETENTION_MESSAGES_DAYS", 30, raising=False)
        user_id, _ = await _seed_user_data(client, user_token)

        report = await purge_expired()

        with session_scope() as db:
            assert _counts(db, user_id)["sessions"] == 1
        assert report.sessions == 0
