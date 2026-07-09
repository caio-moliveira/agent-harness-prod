"""Fase 2 — the ``deep_research`` subagent gating and spec (no graph compile / LLM call).

The compiled graph needs OPENAI_API_KEY and network, so we don't build it here. We test the
deterministic seams: the ``CompiledSubAgent`` spec shape and the ``_build_subagents`` gating that
registers the subagent only when web search is on AND a runnable is available.
"""

import sqlite3

from src.app.agents.data_agent.agent_data import _build_subagents
from src.app.agents.data_agent.subagents.deep_research import (
    SUBAGENT_NAME,
    make_deep_research_subagent_spec,
)
from src.app.agents.data_agent.subagents.user_sql import SUBAGENT_NAME as SQL_SUBAGENT_NAME


class _DummyRunnable:
    """Stands in for the compiled deep-research graph; never invoked in these tests."""


def _db(tmp_path):
    from langchain_community.utilities import SQLDatabase

    p = tmp_path / "t.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE vendas (mes TEXT, receita REAL)")
    con.commit()
    con.close()
    return SQLDatabase.from_uri(f"sqlite:///{p}")


class TestDeepResearchSpec:
    """The CompiledSubAgent spec carries name/description and the runnable as-is."""

    def test_spec_shape(self):
        """Spec is name/description/runnable with the given runnable passed through."""
        runnable = _DummyRunnable()
        spec = make_deep_research_subagent_spec(runnable)
        assert spec["name"] == SUBAGENT_NAME
        assert spec["description"]
        assert spec["runnable"] is runnable


class TestDeepResearchGating:
    """The deep_research subagent is registered only with web search on AND a runnable present."""

    def test_registered_when_web_on_and_runnable(self):
        """web_search on + runnable available → deep_research subagent registered."""
        subs = _build_subagents(None, sql_enabled=False, web_search=True, deep_research_runnable=_DummyRunnable())
        assert [s["name"] for s in subs] == [SUBAGENT_NAME]

    def test_not_registered_when_runnable_missing(self):
        """web_search on but runnable None (e.g. no OPENAI_API_KEY) → not registered."""
        assert _build_subagents(None, sql_enabled=False, web_search=True, deep_research_runnable=None) == []

    def test_not_registered_when_web_off(self):
        """web_search off → not registered even if a runnable is available."""
        subs = _build_subagents(None, sql_enabled=False, web_search=False, deep_research_runnable=_DummyRunnable())
        assert subs == []

    def test_both_subagents_when_all_enabled(self, tmp_path):
        """DB+sql and web+runnable → both the text_sql_agent and deep_research subagents register."""
        subs = _build_subagents(
            _db(tmp_path), sql_enabled=True, web_search=True, deep_research_runnable=_DummyRunnable()
        )
        assert {s["name"] for s in subs} == {SQL_SUBAGENT_NAME, SUBAGENT_NAME}
