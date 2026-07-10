"""Unit tests for the intra-session read-ledger (P1 — stop the re-read spiral).

The ledger turns a session's persisted tool steps into a compact "already read this conversation"
index injected before each turn, so the agent reuses prior reads instead of re-running them and
re-inflating the context (the failure mode seen in the degraded traces).
"""

from types import SimpleNamespace

from src.app.agents.data_agent.agent_data import _LEDGER_MAX, _ledger_label, _read_ledger


def _step(name: str, tool_input: str | None) -> SimpleNamespace:
    """A minimal stand-in for a persisted ChatMessageStep (only name/input are read)."""
    return SimpleNamespace(name=name, input=tool_input)


class TestLedgerLabel:
    """A step collapses to a compact one-line label — its target file/doc, or an input preview."""

    def test_extracts_file_path(self):
        """A read_file step labels by its file path."""
        assert _ledger_label("read_file", "{'file_path': '/workspace/metas.md'}") == "read_file: /workspace/metas.md"

    def test_extracts_doc_id(self):
        """A document step labels by its doc id."""
        assert _ledger_label("read_document", '{"doc_id": "rep-986639"}') == "read_document: rep-986639"

    def test_falls_back_to_input_preview_for_sql(self):
        """A query with no path/id target labels by an input preview."""
        label = _ledger_label("consultar_dados", "SELECT mes, SUM(valor) FROM vendas GROUP BY mes")
        assert label.startswith("consultar_dados: SELECT mes")

    def test_bare_name_when_no_input(self):
        """A step with no input labels by its bare name."""
        assert _ledger_label("listar_dados", None) == "listar_dados"


class TestReadLedger:
    """The ledger dedupes read-ish steps, ignores writes, and stays bounded."""

    def test_dedupes_repeated_reads_and_ignores_writes(self):
        """Duplicate reads collapse to one line; write/edit steps never enter the ledger."""
        steps = [
            _step("read_file", "{'file_path': '/workspace/a.md'}"),
            _step("write_file", "{'file_path': '/workspace/out.md'}"),  # a write — never re-read
            _step("read_file", "{'file_path': '/workspace/a.md'}"),  # duplicate — collapses
            _step("consultar_dados", "SELECT 1"),
        ]
        ledger = _read_ledger(steps)
        lines = ledger.splitlines()
        assert lines == ["- read_file: /workspace/a.md", "- consultar_dados: SELECT 1"]

    def test_empty_when_no_read_steps(self):
        """A session with only writes yields no ledger."""
        assert _read_ledger([_step("write_file", "x"), _step("edit_file", "y")]) == ""

    def test_capped_at_ledger_max(self):
        """The ledger never grows past the cap even on a long session."""
        steps = [_step("read_file", f"{{'file_path': '/workspace/f{i}.md'}}") for i in range(_LEDGER_MAX + 20)]
        assert len(_read_ledger(steps).splitlines()) == _LEDGER_MAX
