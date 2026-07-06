"""Integration tests for airtight per-(user, agent) isolation (#11).

Two pre-agreed seams:
  1. ``AgentRepository.get_owned_agent`` — a QUERY-LEVEL ownership filter (``WHERE id AND user_id``),
     replacing the post-hoc "fetch by id, then compare user_id" pattern on the build path.
  2. ``_build_agent_for_session`` — the single choke point: it must resolve the session's agent
     through the ownership filter, so a session can never materialize another user's folder,
     decrypted DB password, or skills. Fail-closed: a non-owned/absent agent builds plain defaults.

Cross-user 403/404 at the HTTP boundary is already covered by test_agents.py and acts as the
regression net for rerouting ``_owned_agent_or_error`` through the same filter.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from tests.integration.conftest import TEST_PASSWORD

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_agent(client: AsyncClient, token: str, name: str = "Iso Agent") -> dict:
    resp = await client.post(
        "/api/v1/agents",
        json={"name": name, "system_prompt": "Be helpful."},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Seam 1 — query-level ownership filter
# ---------------------------------------------------------------------------

class TestOwnedAgentQuery:
    async def test_owner_gets_agent_non_owner_gets_none(self, client: AsyncClient, user_token):
        from src.app.core.agent.agent_repository import AgentRepository

        created = await _create_agent(client, user_token, name="Private")
        repo = AgentRepository()

        # The first registered user (user_token fixture) has id 1 on a fresh per-test DB.
        owned = await repo.get_owned_agent(created["id"], 1)
        assert owned is not None
        assert owned.id == created["id"]

        # A different user must get nothing back — filtered at the query, not after.
        assert await repo.get_owned_agent(created["id"], 2) is None

    async def test_missing_agent_returns_none(self, client: AsyncClient, user_token):
        from src.app.core.agent.agent_repository import AgentRepository

        assert await AgentRepository().get_owned_agent(999999, 1) is None


# ---------------------------------------------------------------------------
# Seam 2 — session -> agent build choke point
# ---------------------------------------------------------------------------

class TestSessionBuildOwnershipGuard:
    def _session(self, user_id: int, agent_id: int):
        from src.app.core.session.session_model import Session

        return Session(id="sess-x", user_id=user_id, agent_id=agent_id, name="")

    async def test_non_owned_agent_materializes_no_foreign_resources(self):
        from src.app.api.v1 import data_agent as da

        session = self._session(user_id=2, agent_id=99)  # agent 99 not owned by user 2
        res = MagicMock()
        res.db = None
        with (
            patch.object(da.agent_repository, "get_owned_agent", new=AsyncMock(return_value=None)) as owned,
            patch.object(da, "_ensure_agent_folder", new=AsyncMock()) as folder_spy,
            patch.object(da, "_ensure_agent_database", new=AsyncMock()) as db_spy,
            patch.object(da, "_materialize_agent_skills", new=AsyncMock()) as skills_spy,
            patch.object(da, "build_data_agent", new=MagicMock(return_value="AGENT")) as build_spy,
        ):
            result = await da._build_agent_for_session(res, session)

        owned.assert_awaited_once_with(99, 2)
        folder_spy.assert_not_awaited()
        db_spy.assert_not_awaited()
        skills_spy.assert_not_awaited()
        assert result == "AGENT"
        _, kwargs = build_spy.call_args
        assert kwargs["system_prompt"] is None  # built plain, no foreign persona
        assert kwargs["skills_dir"] is None      # no foreign skills

    async def test_owned_agent_materializes_its_resources(self):
        from src.app.api.v1 import data_agent as da
        from src.app.core.agent.agent_model import Agent

        session = self._session(user_id=1, agent_id=5)
        agent = Agent(
            id=5, user_id=1, name="Mine", system_prompt="Persona",
            config={"folder": "/w", "database": {"driver": "postgresql"}, "skills": [1]},
        )
        res = MagicMock()
        res.db = None
        with (
            patch.object(da.agent_repository, "get_owned_agent", new=AsyncMock(return_value=agent)) as owned,
            patch.object(da, "_ensure_agent_folder", new=AsyncMock()) as folder_spy,
            patch.object(da, "_ensure_agent_database", new=AsyncMock()) as db_spy,
            patch.object(da, "_materialize_agent_skills", new=AsyncMock(return_value="/skills")) as skills_spy,
            patch.object(da, "build_data_agent", new=MagicMock(return_value="AGENT")),
        ):
            await da._build_agent_for_session(res, session)

        owned.assert_awaited_once_with(5, 1)
        folder_spy.assert_awaited_once()
        db_spy.assert_awaited_once()
        skills_spy.assert_awaited_once()

    async def test_build_uses_ownership_filter_not_bare_get(self):
        """Regression guard: the build path must NOT use the unfiltered get_agent."""
        from src.app.api.v1 import data_agent as da

        session = self._session(user_id=2, agent_id=99)
        res = MagicMock()
        res.db = None
        with (
            patch.object(da.agent_repository, "get_owned_agent", new=AsyncMock(return_value=None)),
            patch.object(da.agent_repository, "get_agent", new=AsyncMock(return_value="LEAK")) as bare,
            patch.object(da, "build_data_agent", new=MagicMock(return_value="AGENT")),
        ):
            await da._build_agent_for_session(res, session)

        bare.assert_not_awaited()
