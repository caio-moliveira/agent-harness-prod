"""Read-only `AGENTS.md` context for the folder a session is granted (RF-24/25/26).

deepagents' stock ``MemoryMiddleware`` implements the https://agents.md/ convention, but its
baked-in system prompt instructs the agent to write learnings back into the file via
``edit_file`` (self-updating memory) — not configurable away. That doesn't fit here: the granted
folder is read-only by default (writes are an explicit, HITL-gated capability), and "the agent
learns from the user" is already covered end-to-end by long-term memory + reflection (RF-29/31).
Using the stock prompt as-is would instruct writes that silently fail on read-only folders, or
spam the overwrite-confirmation gate on writable ones, for a concern we already handle elsewhere.

This subclass keeps the loading mechanics (backend-routed, missing file skipped gracefully,
loaded once per thread) and drops the self-editing guidance — the user edits ``AGENTS.md`` by
hand in their own folder; the agent only ever reads it.
"""

from deepagents import MemoryMiddleware


class WorkspaceMemoryMiddleware(MemoryMiddleware):
    """`MemoryMiddleware` with the self-editing guidance stripped — read-only context."""

    def _format_agent_memory(self, contents: dict[str, str]) -> str:
        """Concatenate loaded file contents in source order; no `<memory_guidelines>` block."""
        sections = [contents[path] for path in self.sources if contents.get(path)]
        return "\n\n".join(sections)
