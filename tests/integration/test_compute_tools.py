"""Tests for the SQL compute tools (#24): read-only DuckDB over the folder's CSV/TSV/Excel files.

Each data file becomes a table (each Excel sheet its own table); aggregations run in the engine, not
by the LLM. No DB needed — the tools read the granted folder directly. In the test env
``SANDBOX_ALLOWED_ROOTS`` is empty, so the allow-list check is skipped and tmp files load freely.
"""

import duckdb
import pytest

from src.app.agents.data_agent.compute_tools import make_compute_tools

pytestmark = pytest.mark.asyncio


def _duckdb_excel_available() -> bool:
    """Whether DuckDB's `excel` extension can be loaded (it auto-downloads on first use).

    In offline/proxied environments the download fails — the xlsx tests then skip with an
    explicit reason instead of failing on an environment condition the code doesn't control.
    """
    try:
        con = duckdb.connect()
        con.execute("INSTALL excel; LOAD excel;")
        con.close()
        return True
    except Exception:
        return False


requires_duckdb_excel = pytest.mark.skipif(
    not _duckdb_excel_available(),
    reason="DuckDB 'excel' extension unavailable (offline/proxied environment)",
)

USER, AGENT = 1, 7


def _write_xlsx(path, sheets: dict) -> None:
    """Write an xlsx with ``{sheet_name: [rows]}`` (first row is the header)."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    wb.save(str(path))


class TestComputeTools:
    """listar_dados + consultar_dados over CSV and Excel (single- and multi-sheet) files."""

    async def test_csv_aggregation(self, tmp_path):
        """CSV is exposed as a table named by its base name; SUM runs in DuckDB."""
        (tmp_path / "vendas.csv").write_text("mes,receita\njan,1000\nfev,2500\n", encoding="utf-8")
        tools = {t.name: t for t in make_compute_tools(USER, AGENT, str(tmp_path), None)}

        listed = await tools["listar_dados"].ainvoke({})
        assert "vendas" in listed and "receita" in listed
        assert "mes VARCHAR" in listed  # column types shown so the model writes typed SQL

        out = await tools["consultar_dados"].ainvoke({"sql": "SELECT SUM(receita) AS s FROM vendas"})
        assert "3500" in out

    @requires_duckdb_excel
    async def test_xlsx_single_sheet_uses_base_name(self, tmp_path):
        """A single-sheet workbook is queryable under the file's base name."""
        _write_xlsx(tmp_path / "dados.xlsx", {"qualquer": [["a", "b"], [1, 2], [3, 4]]})
        tools = {t.name: t for t in make_compute_tools(USER, AGENT, str(tmp_path), None)}

        listed = await tools["listar_dados"].ainvoke({})
        assert "dados" in listed  # single sheet keeps the file base name

        out = await tools["consultar_dados"].ainvoke({"sql": "SELECT SUM(a) AS s FROM dados"})
        assert "4" in out  # 1 + 3

    @requires_duckdb_excel
    async def test_xlsx_multi_sheet_becomes_multiple_tables(self, tmp_path):
        """A multi-sheet workbook exposes one table per sheet (file base + sheet name)."""
        _write_xlsx(
            tmp_path / "relatorio.xlsx",
            {"Vendas": [["mes", "receita"], ["jan", 1000], ["fev", 2500]], "Metas": [["regiao", "meta"], ["sul", 5000]]},
        )
        tools = {t.name: t for t in make_compute_tools(USER, AGENT, str(tmp_path), None)}

        listed = await tools["listar_dados"].ainvoke({})
        assert "relatorio_vendas" in listed and "relatorio_metas" in listed

        out = await tools["consultar_dados"].ainvoke({"sql": "SELECT SUM(receita) AS s FROM relatorio_vendas"})
        assert "3500" in out
        out2 = await tools["consultar_dados"].ainvoke({"sql": "SELECT meta FROM relatorio_metas WHERE regiao='sul'"})
        assert "5000" in out2
