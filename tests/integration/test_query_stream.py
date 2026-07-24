"""Integration tests for the data-agent query stream — robustness on failure.

A turn that errors mid-stream must still (a) emit a terminal SSE event (never leave the client
hanging on silence) and (b) persist its partial work — the user message + whatever tokens/steps ran
— so a failed turn is neither invisible nor lost. This guards the P0 fix in ``query_stream``.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _make_session(client: AsyncClient, user_token: str) -> tuple[str, str]:
    resp = await client.post("/api/v1/auth/session", headers=_auth(user_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["session_id"], body["token"]["access_token"]


class TestQueryStreamRobustness:
    """The stream never leaves a turn silent or lost — terminal event + partial persistence."""

    async def test_mid_turn_error_sends_terminal_and_persists_partial(
        self, client: AsyncClient, user_token, monkeypatch
    ):
        """A model error mid-turn still emits a terminal `error` and persists the partial work."""
        from src.app.api.v1 import data_agent as da

        sid, token = await _make_session(client, user_token)

        async def _events(_messages, _session_id, _user_id):
            yield {"type": "tool_start", "name": "read_file", "input": "{'file_path': '/workspace/x.md'}"}
            yield {"type": "tool_end", "name": "read_file", "output": "conteúdo lido"}
            yield {"type": "token", "content": "resposta parcial"}
            raise RuntimeError("modelo falhou no meio do turno")

        agent = AsyncMock()
        agent.astream_query_events = _events
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))

        resp = await client.post(
            f"/api/v1/data-agent/{sid}/query/stream", json={"query": "planeje algo"}, headers=_auth(token)
        )
        assert resp.status_code == 200
        body = resp.text
        assert '"type": "tool_start"' in body  # streamed activity reached the client
        assert '"type": "error"' in body  # and a terminal error — never silent

        # The partial turn survived: user message + assistant (partial text) + the tool step.
        data = (await client.get(f"/api/v1/data-agent/{sid}/messages", headers=_auth(token))).json()["messages"]
        assert [m["role"] for m in data] == ["user", "assistant"]
        assert data[0]["content"] == "planeje algo"
        assert "resposta parcial" in data[1]["content"]
        assert any(s["name"] == "read_file" for s in data[1]["steps"])

    async def test_success_sends_done_and_persists(self, client: AsyncClient, user_token, monkeypatch):
        """A normal turn emits `done` and persists the assistant answer."""
        from src.app.api.v1 import data_agent as da

        sid, token = await _make_session(client, user_token)

        async def _events(_messages, _session_id, _user_id):
            yield {"type": "token", "content": "tudo certo"}

        agent = AsyncMock()
        agent.astream_query_events = _events
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))

        resp = await client.post(
            f"/api/v1/data-agent/{sid}/query/stream", json={"query": "oi"}, headers=_auth(token)
        )
        assert '"type": "done"' in resp.text
        data = (await client.get(f"/api/v1/data-agent/{sid}/messages", headers=_auth(token))).json()["messages"]
        assert data[-1]["content"] == "tudo certo"


class TestHeartbeatSurvivesSlowToolCalls:
    """A gap longer than the heartbeat interval must not kill the turn producing it (regression).

    ``asyncio.wait_for`` cancels the coroutine it's timing, so a naive heartbeat implementation would
    tear down the agent turn itself the moment a tool call (e.g. deep research) ran silently longer
    than the ping interval — the turn would vanish with no terminal event, and the client would be
    stuck forever on the last thing it saw. This tests ``_with_heartbeat`` directly, the same seam
    the real endpoint wraps its event generator with.
    """

    async def test_slow_frame_survives_past_several_heartbeat_intervals(self):
        """A frame arriving after several ping intervals must still be relayed, not dropped."""
        from src.app.api.v1.data_agent import _with_heartbeat

        async def _slow_frames():
            await asyncio.sleep(0.05)  # several multiples of the tiny interval below
            yield "data: real-frame\n\n"

        pings = 0
        received = []
        async for frame in _with_heartbeat(_slow_frames(), interval=0.01):
            if frame == ": ping\n\n":
                pings += 1
                continue
            received.append(frame)

        assert pings >= 1  # at least one ping fired while the slow tool call was still running
        assert received == ["data: real-frame\n\n"]  # and the real frame still arrived intact
