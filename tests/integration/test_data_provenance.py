"""Integration tests for the schema-aware, self-validating Data Agent + provenance (#12).

Two pre-agreed seams:
  1. ``Source`` — the reusable provenance model (kind=query today; doc_chunk lands with #14).
  2. The enhanced ``run_sql`` tool — on error it lists the available tables and warns against
     inventing tables/columns (drives the LLM's self-correction instead of a bogus final answer);
     on success it appends provenance (tables + SQL + extraction time). Read-only rejection stays.

We test the deterministic tool/model seams, not the LLM's final wording (which would need a real
model call). "Force schema exploration before run_sql" is prompt-driven and covered there.
"""

import sqlite3

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Seam 1 — Source provenance model
# ---------------------------------------------------------------------------

class TestSourceModel:
    def test_from_query_captures_provenance(self):
        from src.app.core.provenance.source import Source

        s = Source.from_query(sql="SELECT * FROM vendas", tables=["vendas"])
        assert s.kind == "query"
        assert s.tables == ["vendas"]
        assert s.query == "SELECT * FROM vendas"
        assert s.extracted_at is not None

        rendered = s.render()
        assert "vendas" in rendered
        assert "SELECT * FROM vendas" in rendered

    def test_render_is_compact_single_line(self):
        from src.app.core.provenance.source import Source

        s = Source.from_query(sql="SELECT 1", tables=[])
        assert "\n" not in s.render()


# ---------------------------------------------------------------------------
# Seam 2 — schema-aware, self-validating run_sql
# ---------------------------------------------------------------------------

class TestSchemaAwareRunSql:
    def _db(self, tmp_path):
        from langchain_community.utilities import SQLDatabase

        p = tmp_path / "t.db"
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE vendas (mes TEXT, receita REAL)")
        con.execute("INSERT INTO vendas VALUES ('jan', 100.0)")
        con.commit()
        con.close()
        return SQLDatabase.from_uri(f"sqlite:///{p}")

    def _run_sql(self, db):
        from src.app.core.db.readonly import make_readonly_sql_tools

        return {t.name: t for t in make_readonly_sql_tools(db)}["run_sql"]

    def test_valid_query_returns_rows_and_provenance(self, tmp_path):
        run_sql = self._run_sql(self._db(tmp_path))
        out = run_sql.invoke({"query": "SELECT * FROM vendas"})
        assert "jan" in out                       # the actual row
        assert "proveni" in out.lower()           # provenance section present
        assert "vendas" in out                    # the referenced table
        assert "SELECT * FROM vendas" in out      # the exact query

    def test_unknown_table_lists_available_and_warns(self, tmp_path):
        run_sql = self._run_sql(self._db(tmp_path))
        out = run_sql.invoke({"query": "SELECT * FROM naoexiste"})
        assert "vendas" in out                    # available tables surfaced for correction
        assert "invente" in out.lower()           # explicit "não invente" guidance

    def test_write_query_still_rejected(self, tmp_path):
        run_sql = self._run_sql(self._db(tmp_path))
        out = run_sql.invoke({"query": "DELETE FROM vendas"})
        assert "rejeitada" in out.lower()
