"""Shared fixtures for integration tests.

The environment bootstrap (test env vars, in-memory SQLite engine interception, Langfuse
mocking) lives in the root ``tests/conftest.py`` — pytest imports it before this module and
before any application code, for the whole suite (unit + integration + e2e alike). This module
keeps only the integration-specific fixtures: the ASGI test client and the mocked agents.
"""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import get_shared_engine

# ---------------------------------------------------------------------------
# Safe to import application code: the root conftest already installed the
# SQLite engine interception, so importing the app triggers DatabaseFactory()
# against in-memory SQLite.
# ---------------------------------------------------------------------------

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel

from src.app.core.common.model.message import Message
from src.app.main import app as _app  # triggers engine + table creation

TEST_PASSWORD = "TestPass123!"
TEST_EMAIL = "testuser@example.com"


# ---------------------------------------------------------------------------
# Mock agent helpers (no real OpenAI calls)
# ---------------------------------------------------------------------------


def _make_mock_deep_research_agent():
    agent = AsyncMock()
    agent.name = "Deep Research"
    agent.agent_invoke = AsyncMock(
        return_value=[Message(role="assistant", content="Here is your research report.")]
    )

    async def _fake_stream(*_args, **_kwargs):
        for chunk in ["Research", " report", " streaming"]:
            yield chunk

    agent.agent_invoke_stream = _fake_stream
    return agent


def _make_mock_text_sql_agent():
    agent = AsyncMock()
    agent.name = "Text-to-SQL"
    agent.agent_invoke = AsyncMock(
        return_value=[Message(role="assistant", content="SELECT * FROM users;")]
    )
    return agent


# ---------------------------------------------------------------------------
# Application & client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Build a fully-patched ASGI test client.

    For every test a fresh set of DB tables, repositories, and mock agents is
    created so tests remain isolated from each other.
    """
    SQLModel.metadata.drop_all(get_shared_engine())
    SQLModel.metadata.create_all(get_shared_engine())

    db_session = Session(get_shared_engine())

    from src.app.core.session import SessionRepository
    from src.app.core.user import UserRepository

    test_user_repo = UserRepository(db_session)
    test_session_repo = SessionRepository(db_session)

    # auth.py calls session-related methods on user_repository; bridge them
    test_user_repo.update_session_name = test_session_repo.update_session_name
    test_user_repo.delete_session = test_session_repo.delete_session
    test_user_repo.get_user_sessions = test_session_repo.get_user_sessions

    from src.app.api.security.limiter import limiter

    limiter.reset()

    with (
        patch("src.app.api.v1.api.user_repository", test_user_repo),
        patch("src.app.api.v1.auth.user_repository", test_user_repo),
        patch("src.app.api.v1.auth.session_repository", test_session_repo),
        patch(
            "src.app.api.v1.deep_research.get_deep_research_agent",
            new_callable=AsyncMock,
            return_value=_make_mock_deep_research_agent(),
        ),
        patch(
            "src.app.api.v1.text_to_sql.get_text_sql_agent",
            new_callable=AsyncMock,
            return_value=_make_mock_text_sql_agent(),
        ),
    ):
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac

    db_session.close()


# ---------------------------------------------------------------------------
# Auth helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def registered_user(client: AsyncClient) -> dict:
    """Register a test user and return the response payload."""
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture()
async def user_token(registered_user: dict) -> str:
    """Return the bearer token for the registered test user."""
    return registered_user["token"]["access_token"]


@pytest.fixture()
async def session_with_token(client: AsyncClient, user_token: str) -> dict:
    """Create a chat session and return its response payload."""
    response = await client.post(
        "/api/v1/auth/session",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture()
def session_token(session_with_token: dict) -> str:
    """Return the bearer token scoped to a chat session."""
    return session_with_token["token"]["access_token"]


@pytest.fixture()
def auth_headers(session_token: str) -> dict:
    """Return Authorization headers for a chat-session-scoped token."""
    return {"Authorization": f"Bearer {session_token}"}
