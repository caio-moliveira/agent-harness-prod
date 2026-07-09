"""Fase 5 — the build choke point threads the subagent capabilities from agent config.

Complements test_isolation.py (which covers ownership). Here we verify that
``_build_agent_for_session`` maps ``config["sql"]`` to ``sql_enabled`` and only compiles/injects
the deep-research runnable when web search is on — so the user's DB and web research are reachable
strictly per the per-agent toggles, and never by accident.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _session(user_id: int, agent_id: int):
    from src.app.core.session.session_model import Session

    return Session(id="sess-x", user_id=user_id, agent_id=agent_id, name="")


def _agent(config: dict):
    from src.app.core.agent.agent_model import Agent

    return Agent(id=5, user_id=1, name="Mine", system_prompt="Persona", config=config)


async def _build_with(config: dict):
    """Run _build_agent_for_session for an owned agent with ``config``; return build_data_agent kwargs."""
    from src.app.api.v1 import data_agent as da

    res = MagicMock()
    res.db = None
    res.folder = None
    with (
        patch.object(da.agent_repository, "get_owned_agent", new=AsyncMock(return_value=_agent(config))),
        patch.object(da, "_ensure_agent_folder", new=AsyncMock()),
        patch.object(da, "_ensure_agent_database", new=AsyncMock()),
        patch.object(da, "_materialize_agent_skills", new=AsyncMock(return_value=None)),
        patch.object(da, "get_deep_research_subagent_runnable", new=AsyncMock(return_value="RUNNABLE")) as dr,
        patch.object(da, "build_data_agent", new=MagicMock(return_value="AGENT")) as build_spy,
    ):
        await da._build_agent_for_session(res, _session(user_id=1, agent_id=5))
    _, kwargs = build_spy.call_args
    return kwargs, dr


class TestSqlWiring:
    """config["sql"] maps to sql_enabled (default off when absent)."""

    async def test_sql_on_threads_enabled(self):
        """An agent with sql=True builds with sql_enabled=True."""
        kwargs, _ = await _build_with({"sql": True})
        assert kwargs["sql_enabled"] is True

    async def test_sql_absent_defaults_off(self):
        """An agent without a sql flag builds with sql_enabled=False."""
        kwargs, _ = await _build_with({})
        assert kwargs["sql_enabled"] is False


class TestDeepResearchWiring:
    """The deep-research runnable is compiled/injected only when web search is on."""

    async def test_web_off_skips_compile(self):
        """web_search off: never compile the graph; pass no runnable."""
        kwargs, dr = await _build_with({"web_search": False})
        dr.assert_not_awaited()
        assert kwargs["deep_research_runnable"] is None

    async def test_web_on_compiles_and_passes(self):
        """web_search on: compile once and pass the runnable through."""
        kwargs, dr = await _build_with({"web_search": True})
        dr.assert_awaited_once()
        assert kwargs["deep_research_runnable"] == "RUNNABLE"
