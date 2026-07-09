"""Fase 3 — streaming labels and delegation auditing for subagent task() calls.

Pure mapping seams (no LLM/stream): the friendly display label for a task() delegation and the
(event_type, scope) classification recorded at the delegation boundary.
"""

from src.app.agents.data_agent.agent_data import _display_for_tool
from src.app.core.session.event_model import SessionEventType
from src.app.core.session.event_recorder import classify_delegation


class TestDisplayForTool:
    """task() delegations get a human label + subagent_type; other tools pass through."""

    def test_sql_delegation_label(self):
        """A task() to text_sql_agent shows the DB label and reports the subagent."""
        name, sub = _display_for_tool("task", {"subagent_type": "text_sql_agent", "description": "q"})
        assert name == "Consultando o banco de dados…"
        assert sub == "text_sql_agent"

    def test_deep_research_delegation_label(self):
        """A task() to deep_research shows the web label and reports the subagent."""
        name, sub = _display_for_tool("task", {"subagent_type": "deep_research", "description": "q"})
        assert name == "Pesquisando na web…"
        assert sub == "deep_research"

    def test_unknown_subagent_generic_label(self):
        """An unknown subagent still gets a generic label (never a raw 'task')."""
        name, sub = _display_for_tool("task", {"subagent_type": "mystery"})
        assert name == "Executando subtarefa…"
        assert sub == "mystery"

    def test_non_task_tool_passes_through(self):
        """A normal tool keeps its name and reports no subagent."""
        assert _display_for_tool("run_sql", "SELECT 1") == ("run_sql", None)


class TestClassifyDelegation:
    """The delegation boundary maps subagent_type to an auditable (event_type, scope)."""

    def test_sql_delegation_event(self):
        """text_sql_agent → query_executed on the database scope."""
        assert classify_delegation("text_sql_agent") == (SessionEventType.QUERY_EXECUTED, "database")

    def test_deep_research_delegation_event(self):
        """deep_research → web_research on the web scope."""
        assert classify_delegation("deep_research") == (SessionEventType.WEB_RESEARCH, "web")

    def test_unknown_delegation_not_auditable(self):
        """An unknown subagent is not auditable."""
        assert classify_delegation("mystery") is None
