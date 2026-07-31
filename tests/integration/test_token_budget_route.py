"""Integration tests for token accounting and budget enforcement (#73).

Two things must hold end to end: usage is billed to the account that spent it (including tokens
burned by subagents), and an exhausted budget refuses the NEXT turn with a message the user can
act on — never a generic error, and never by killing a turn already in flight.
"""

import json
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


def _install_agent(monkeypatch, input_tokens: int = 0, output_tokens: int = 0):
    """An agent whose turn reports the given token usage via the turn_end marker."""
    from src.app.api.v1 import data_agent as da

    async def _events(_messages, _session_id, _user_id):
        yield {"type": "token", "content": "resposta"}
        yield {
            "type": "turn_end",
            "reason": "completed",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    agent = AsyncMock()
    agent.astream_query_events = _events
    monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))
    return agent


class TestUsageEndpoint:
    """`GET /me/usage` is how the user (and an operator) sees cost before it becomes a refusal."""

    async def test_reports_zero_for_a_fresh_account(self, client: AsyncClient, user_token):
        """A user who has not spent anything sees zeros, not an error."""
        resp = await client.get("/api/v1/me/usage", headers=_auth(user_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_tokens"] == 0 and body["turns"] == 0
        assert body["exceeded"] is False

    async def test_reports_usage_after_a_turn(self, client: AsyncClient, user_token, monkeypatch):
        """A turn's tokens are billed to the account and visible on the endpoint."""
        _install_agent(monkeypatch, input_tokens=120, output_tokens=30)
        sid, token = await _make_session(client, user_token)
        await client.post(
            f"/api/v1/data-agent/{sid}/query/stream", json={"query": "analise"}, headers=_auth(token)
        )
        body = (await client.get("/api/v1/me/usage", headers=_auth(user_token))).json()
        assert body["input_tokens"] == 120
        assert body["output_tokens"] == 30
        assert body["total_tokens"] == 150
        assert body["turns"] == 1

    async def test_usage_accumulates_across_turns(self, client: AsyncClient, user_token, monkeypatch):
        """Consecutive turns add up — the daily total is what the budget is checked against."""
        _install_agent(monkeypatch, input_tokens=100, output_tokens=10)
        sid, token = await _make_session(client, user_token)
        for _ in range(3):
            await client.post(
                f"/api/v1/data-agent/{sid}/query/stream", json={"query": "de novo"}, headers=_auth(token)
            )
        body = (await client.get("/api/v1/me/usage", headers=_auth(user_token))).json()
        assert body["total_tokens"] == 330 and body["turns"] == 3


class TestBudgetEnforcement:
    """An exhausted budget stops the next turn, politely and legibly."""

    async def test_turn_is_refused_when_the_budget_is_exhausted(
        self, client: AsyncClient, user_token, monkeypatch
    ):
        """Over budget: the agent is never invoked and the user is told when it resets."""
        from src.app.api.v1 import data_agent as da
        from src.app.core.usage import budget as budget_module

        monkeypatch.setattr(budget_module.settings, "TOKEN_BUDGET_DAILY", 100, raising=False)
        _install_agent(monkeypatch, input_tokens=500, output_tokens=0)
        sid, token = await _make_session(client, user_token)

        # First turn runs and blows past the limit (a turn in flight is never killed for budget).
        first = await client.post(
            f"/api/v1/data-agent/{sid}/query/stream", json={"query": "primeira"}, headers=_auth(token)
        )
        assert '"reason": "completed"' in first.text

        # The next one is refused before the agent is reached.
        called = {"ran": False}

        async def _must_not_run(_messages, _session_id, _user_id):
            called["ran"] = True
            yield {"type": "token", "content": "não deveria"}

        agent = AsyncMock()
        agent.astream_query_events = _must_not_run
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))

        second = await client.post(
            f"/api/v1/data-agent/{sid}/query/stream", json={"query": "segunda"}, headers=_auth(token)
        )
        assert called["ran"] is False
        assert '"reason": "budget_exhausted"' in second.text
        assert '"type": "error"' not in second.text  # a budget stop is not a failure
        # The refusal must be actionable: how much, how much was used, and when it resets.
        streamed = " ".join(
            json.loads(line[6:]).get("content", "")
            for line in second.text.splitlines()
            if line.startswith("data: ")
        )
        assert "limite diário" in streamed and "100" in streamed and "UTC" in streamed

    async def test_disabled_budget_never_refuses(self, client: AsyncClient, user_token, monkeypatch):
        """With the budget off (the default), heavy usage still flows."""
        from src.app.core.usage import budget as budget_module

        monkeypatch.setattr(budget_module.settings, "TOKEN_BUDGET_DAILY", 0, raising=False)
        _install_agent(monkeypatch, input_tokens=1_000_000, output_tokens=0)
        sid, token = await _make_session(client, user_token)

        for _ in range(2):
            resp = await client.post(
                f"/api/v1/data-agent/{sid}/query/stream", json={"query": "pesado"}, headers=_auth(token)
            )
            assert '"reason": "completed"' in resp.text
