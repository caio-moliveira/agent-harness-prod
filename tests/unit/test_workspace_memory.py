"""Unit tests for ``WorkspaceMemoryMiddleware`` (RF-24/25/26).

Pure formatting logic, no backend I/O: proves the self-editing guidance baked into deepagents'
stock ``MemoryMiddleware`` system prompt (``edit_file``, ``<memory_guidelines>``) is dropped, and
only the raw file content survives — see ``workspace_memory.py`` for why that distinction matters
(read-only granted folders, no overlap with the existing mem0 reflection pipeline).
"""

from src.app.agents.data_agent.workspace_memory import WorkspaceMemoryMiddleware


def _middleware(sources: list[str]) -> WorkspaceMemoryMiddleware:
    return WorkspaceMemoryMiddleware(backend=None, sources=sources)


def test_returns_file_content_without_self_editing_guidance():
    mw = _middleware(["/workspace/AGENTS.md"])
    formatted = mw._format_agent_memory({"/workspace/AGENTS.md": "Este é o resumo da pasta."})
    assert "Este é o resumo da pasta." in formatted
    assert "edit_file" not in formatted
    assert "memory_guidelines" not in formatted
    assert "agent_memory" not in formatted  # no wrapper tags either — just the raw content


def test_empty_when_no_content_loaded():
    mw = _middleware(["/workspace/AGENTS.md"])
    assert mw._format_agent_memory({}) == ""


def test_multiple_sources_concatenated_in_order():
    mw = _middleware(["/a/AGENTS.md", "/b/AGENTS.md"])
    formatted = mw._format_agent_memory({"/a/AGENTS.md": "primeiro", "/b/AGENTS.md": "segundo"})
    assert formatted.index("primeiro") < formatted.index("segundo")


def test_missing_source_in_contents_is_skipped_not_errored():
    mw = _middleware(["/a/AGENTS.md", "/b/AGENTS.md"])
    formatted = mw._format_agent_memory({"/a/AGENTS.md": "only this one"})
    assert formatted == "only this one"
