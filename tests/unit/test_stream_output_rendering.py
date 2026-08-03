"""Unit tests for how tool output is rendered on the streamed timeline (#76).

Found in the 2026-07-31 E2E: ``write_file`` returns a LangGraph ``Command`` whose repr is the whole
state update, and it was streamed verbatim — the user saw
``Command(update={'files': {'/relatorio.md': {'content': [...], 'created_at': ...}}})`` as the
"output" of their tool. Internal representations must never reach the UI.
"""

from types import SimpleNamespace

from src.app.agents.data_agent.agent_data import _short


class TestCommandRendering:
    """A file-writing Command is summarized; everything else passes through untouched."""

    def test_command_with_files_becomes_a_readable_sentence(self):
        """A file-write Command renders as the paths it touched, not as its state update."""
        command = SimpleNamespace(
            update={
                "files": {
                    "/relatorio.md": {"content": ["# Relatório", "linha"], "created_at": "2026-07-31T00:00:00Z"}
                }
            }
        )
        rendered = _short(command)
        assert "/relatorio.md" in rendered
        assert "Command(" not in rendered
        assert "created_at" not in rendered  # no internal bookkeeping leaks

    def test_multiple_files_are_all_listed(self):
        """Every written path is named, so the user knows exactly what happened."""
        command = SimpleNamespace(update={"files": {"/b.md": {}, "/a.md": {}}})
        rendered = _short(command)
        assert "/a.md" in rendered and "/b.md" in rendered

    def test_plain_string_output_is_unchanged(self):
        """Ordinary tool output passes through verbatim."""
        assert _short("['/workspace/vendas.csv']") == "['/workspace/vendas.csv']"

    def test_unrelated_command_is_left_alone(self):
        """A Command that is not a file write keeps its default rendering — no silent swallowing."""
        command = SimpleNamespace(update={"messages": ["algo"]})
        assert "messages" in _short(command)

    def test_output_is_still_truncated(self):
        """The length cap still applies after the readability pass."""
        assert len(_short("x" * 5000)) == 1500
