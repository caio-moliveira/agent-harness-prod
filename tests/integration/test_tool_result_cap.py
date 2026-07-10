"""Unit tests for ToolResultCapMiddleware (P1 — one-turn context-blowup guard).

An oversized tool result is trimmed to a preview *in the model request only*; small results and
non-tool messages pass through untouched, and the middleware is idempotent. The full result is never
lost (it stays in graph state — not exercised here, which only covers the request-shaping logic).
"""

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from src.app.agents.data_agent.context_middleware import MAX_TOOL_RESULT_CHARS, ToolResultCapMiddleware

pytestmark = pytest.mark.asyncio


class _FakeRequest:
    """A minimal ModelRequest stand-in: exposes .messages and an override() that returns a new one."""

    def __init__(self, messages: list) -> None:
        self.messages = messages

    def override(self, **overrides) -> "_FakeRequest":
        return _FakeRequest(overrides["messages"])


async def _run(messages: list) -> list:
    """Run the middleware over a message list and return what the model handler actually received."""
    captured: dict = {}

    async def handler(request):
        captured["messages"] = request.messages
        return "response"

    result = await ToolResultCapMiddleware().awrap_model_call(_FakeRequest(messages), handler)
    assert result == "response"
    return captured["messages"]


class TestToolResultCap:
    """Oversized tool results are previewed; everything else is left alone."""

    async def test_trims_oversized_tool_message(self):
        """A ToolMessage over the threshold is replaced by a compact preview."""
        big = ToolMessage(content="x" * (MAX_TOOL_RESULT_CHARS + 1), name="read_file", tool_call_id="1")
        seen = await _run([HumanMessage("pergunta"), big])
        assert "truncado" in seen[1].content
        assert len(seen[1].content) < MAX_TOOL_RESULT_CHARS
        assert seen[1].tool_call_id == "1"  # identity preserved so the tool-call pairing holds

    async def test_leaves_small_and_non_tool_messages_untouched(self):
        """A within-budget tool result and human messages pass through unchanged (no override)."""
        small = ToolMessage(content="linhas: 3", name="listar_dados", tool_call_id="2")
        human = HumanMessage("oi")
        seen = await _run([human, small])
        assert seen[0] is human
        assert seen[1] is small

    async def test_idempotent_on_already_trimmed(self):
        """Re-running over an already-previewed (now small) message is a no-op."""
        big = ToolMessage(content="y" * (MAX_TOOL_RESULT_CHARS + 100), name="read_file", tool_call_id="3")
        once = await _run([big])
        twice = await _run(once)
        assert twice[0].content == once[0].content
