"""Fase 1 — the read-only ``text_sql_agent`` subagent over the user's connected database.

Two deterministic seams (no LLM call):
  1. ``make_user_sql_subagent`` — the deepagents ``SubAgent`` spec is well-formed and its tools are
     the read-only SQL toolkit (SELECT works with provenance; writes are rejected).
  2. ``_build_subagents`` — gating: the subagent is registered only when a DB is connected AND the
     ``sql`` capability is on; otherwise the DB is not reachable by any path.
"""

import sqlite3

from src.app.agents.data_agent.agent_data import _build_subagents
from src.app.agents.data_agent.subagents.user_sql import SUBAGENT_NAME, make_user_sql_subagent


def _db(tmp_path):
    from langchain_community.utilities import SQLDatabase

    p = tmp_path / "t.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE vendas (mes TEXT, receita REAL)")
    con.execute("INSERT INTO vendas VALUES ('jan', 100.0)")
    con.commit()
    con.close()
    return SQLDatabase.from_uri(f"sqlite:///{p}")


class TestSubAgentSpec:
    """The deepagents SubAgent spec is well-formed and pins the read-only SQL toolkit."""

    def test_spec_shape(self, tmp_path):
        """name/description/system_prompt are set and tools are exactly the read-only SQL toolkit."""
        spec = make_user_sql_subagent(self._db(tmp_path))
        assert spec["name"] == SUBAGENT_NAME
        assert spec["description"] and spec["system_prompt"]
        tool_names = {t.name for t in spec["tools"]}
        assert tool_names == {"list_tables", "describe_tables", "run_sql"}

    def test_subagent_tools_are_read_only(self, tmp_path):
        """SELECT returns rows with provenance; a write statement is rejected."""
        spec = make_user_sql_subagent(self._db(tmp_path))
        run_sql = {t.name: t for t in spec["tools"]}["run_sql"]

        ok = run_sql.invoke({"query": "SELECT * FROM vendas"})
        assert "jan" in ok
        assert "proveni" in ok.lower()

        rejected = run_sql.invoke({"query": "DELETE FROM vendas"})
        assert "rejeitada" in rejected.lower()

    _db = staticmethod(_db)


class TestSubAgentGating:
    """The subagent is registered only when a DB is connected AND the sql capability is on."""

    def test_registered_when_db_and_sql_on(self, tmp_path):
        """DB present + sql on → the text_sql_agent subagent is registered."""
        subs = _build_subagents(_db(tmp_path), sql_enabled=True)
        assert [s["name"] for s in subs] == [SUBAGENT_NAME]

    def test_not_registered_when_sql_off(self, tmp_path):
        """DB present but sql off → no subagent (DB not reachable)."""
        assert _build_subagents(_db(tmp_path), sql_enabled=False) == []

    def test_not_registered_without_db(self):
        """SQL capability on but no DB connected → no subagent."""
        assert _build_subagents(None, sql_enabled=True) == []
