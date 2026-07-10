"""The parent stream must not leak a subagent's private work (Issue B).

A task() delegation runs a subagent whose model tokens (its raw report) and internal tool calls are
its own business. Streaming them would dump the subagent's report into the parent's answer — mixing
languages and ballooning the reply — and clutter the timeline with inner tools. Only the delegation's
boundary (its "Pesquisando na web…" card and distilled result) should reach the client.
"""

from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.agents.data_agent import agent_data as ad
from src.app.core.common.model.message import Message

pytestmark = pytest.mark.asyncio


def _chunk(text: str) -> SimpleNamespace:
    """A stand-in for a streamed chat-model chunk (only .content is read)."""
    return SimpleNamespace(content=text)


async def _events(*evs):
    """An async generator matching self.agent.astream_events(payload, config=, version=)."""

    async def gen(_payload, config=None, version=None):
        for e in evs:
            yield e

    return gen


async def _run(monkeypatch, evs) -> list[dict]:
    """Drive DataAgent.astream_query_events over a scripted event sequence via a stand-in self."""
    monkeypatch.setattr(ad._step_repo, "get_for_session", AsyncMock(return_value=[]))
    agent = SimpleNamespace(astream_events=await _events(*evs))
    fake_self = SimpleNamespace(
        agent=agent, name="Data Agent", root_dir=None, agent_id=1, memory_enabled=False, _checkpointer=None
    )
    # Bind the real methods the streamer calls on self, so we exercise the actual event loop.
    fake_self._invoke_config = MethodType(ad.DataAgent._invoke_config, fake_self)
    fake_self._compose_payload = MethodType(ad.DataAgent._compose_payload, fake_self)
    out: list[dict] = []
    async for ev in ad.DataAgent.astream_query_events(fake_self, [Message(role="user", content="q")], "sess", None):
        out.append(ev)
    return out


class TestDelegationStreaming:
    """Subagent tokens and inner tools are suppressed; the delegation boundary is surfaced."""

    async def test_subagent_report_never_reaches_the_answer(self, monkeypatch):
        """The subagent's model tokens are dropped; only the parent's tokens stream."""
        events = await _run(
            monkeypatch,
            [
                {"event": "on_chat_model_stream", "data": {"chunk": _chunk("Vou pesquisar. ")}},
                {"event": "on_tool_start", "name": "task", "data": {"input": {"subagent_type": "deep_research"}}},
                {"event": "on_chat_model_stream", "data": {"chunk": _chunk("RELATORIO GIGANTE DO SUBAGENTE")}},
                {"event": "on_tool_start", "name": "web_search", "data": {"input": {"q": "x"}}},
                {"event": "on_tool_end", "name": "web_search", "data": {"output": "hits"}},
                {"event": "on_tool_end", "name": "task", "data": {"output": "resumo destilado"}},
                {"event": "on_chat_model_stream", "data": {"chunk": _chunk("Com base nisso, concluo.")}},
            ],
        )
        tokens = "".join(e["content"] for e in events if e["type"] == "token")
        assert "Vou pesquisar. " in tokens
        assert "Com base nisso, concluo." in tokens
        assert "RELATORIO GIGANTE" not in tokens  # the subagent's raw report never leaks

    async def test_only_the_delegation_boundary_is_on_the_timeline(self, monkeypatch):
        """The task card is surfaced; the subagent's inner tools are not."""
        deleg = {"subagent_type": "deep_research"}
        events = await _run(
            monkeypatch,
            [
                {"event": "on_tool_start", "name": "task", "data": {"input": deleg}},
                {"event": "on_tool_start", "name": "web_search", "data": {"input": {"q": "x"}}},
                {"event": "on_tool_end", "name": "web_search", "data": {"input": {"q": "x"}, "output": "hits"}},
                {"event": "on_tool_end", "name": "task", "data": {"input": deleg, "output": "resumo"}},
            ],
        )
        starts = [e["name"] for e in events if e["type"] == "tool_start"]
        ends = [e["name"] for e in events if e["type"] == "tool_end"]
        assert starts == ["Pesquisando na web…"]  # only the delegation, not web_search
        assert ends == ["Pesquisando na web…"]

    async def test_top_level_tools_still_stream_normally(self, monkeypatch):
        """A normal (non-delegated) tool and the parent's tokens are surfaced as before."""
        events = await _run(
            monkeypatch,
            [
                {"event": "on_tool_start", "name": "read_file", "data": {"input": {"file_path": "/a.md"}}},
                {"event": "on_tool_end", "name": "read_file", "data": {"output": "conteudo"}},
                {"event": "on_chat_model_stream", "data": {"chunk": _chunk("resposta")}},
            ],
        )
        assert [e["name"] for e in events if e["type"] == "tool_start"] == ["read_file"]
        assert "".join(e["content"] for e in events if e["type"] == "token") == "resposta"
