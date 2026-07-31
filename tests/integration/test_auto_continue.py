"""Integration tests for automatic turn resumption (#77).

The acceptance criteria of the issue are exactly two, and each has a test here: a legitimate long
task finishes without manual intervention *within the configured budget*, and there is no infinite
auto-resumption loop (the hard ceiling is tested, not assumed).

The rest guards the decisions that make the feature safe rather than merely working: only
``call_limit`` resumes, a parked approval outranks the resumption budget, and the synthetic
"continuar" instruction never reaches the persisted transcript — the user typed one message, so the
conversation must show one message.
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


def _events_of(body: str, etype: str) -> list[dict]:
    """Every SSE frame of ``etype`` in a streamed response body."""
    out = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[len("data: ") :])
        except json.JSONDecodeError:
            continue
        if ev.get("type") == etype:
            out.append(ev)
    return out


def _terminal(body: str) -> dict:
    """The turn's terminal event (`done` or `error`) — what the client acts on."""
    for line in reversed(body.splitlines()):
        if line.startswith("data: "):
            ev = json.loads(line[len("data: ") :])
            if ev.get("type") in ("done", "error"):
                return ev
    raise AssertionError("stream ended without a terminal event")


class _ScriptedAgent:
    """An agent that hits the step cap for its first ``cap_times`` attempts, then finishes.

    Records the ``resume_hint`` it was called with per attempt, which is how we assert the user is
    never shown a "send continuar" hint for an action the server is about to take itself.
    """

    def __init__(self, cap_times: int):
        self.cap_times = cap_times
        self.attempts = 0
        self.resume_hints: list[bool] = []
        self.payloads: list[list] = []

    def astream_query_events(self, messages, _session_id, _user_id, resume_hint: bool = True):
        self.attempts += 1
        self.resume_hints.append(resume_hint)
        self.payloads.append(list(messages))
        capped = self.attempts <= self.cap_times

        async def _gen():
            yield {"type": "token", "content": f"parte {self.attempts} "}
            yield {
                "type": "turn_end",
                "reason": "call_limit" if capped else "completed",
                "input_tokens": 10,
                "output_tokens": 5,
            }

        return _gen()


class TestAutoContinueCompletesLongTask:
    """Acceptance criterion 1 — a long legitimate task finishes with no manual intervention."""

    async def test_capped_turn_resumes_and_completes(self, client: AsyncClient, user_token, monkeypatch):
        """One resumption turns a `call_limit` stop into a `completed` turn, in a single request."""
        from src.app.api.v1 import data_agent as da

        sid, token = await _make_session(client, user_token)
        agent = _ScriptedAgent(cap_times=1)
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))
        monkeypatch.setattr(da.settings, "MAX_AUTO_CONTINUES", 2)

        resp = await client.post(
            f"/api/v1/data-agent/{sid}/query/stream",
            json={"query": "analise tudo", "auto_continue": True},
            headers=_auth(token),
        )

        assert agent.attempts == 2
        # The user is told a resumption happened — a silent 40s gap would read as a frozen UI.
        assert _events_of(resp.text, "auto_continue") == [{"type": "auto_continue", "attempt": 1, "max": 2}]
        # And the turn ends as what it actually is: finished. No amber banner, no user action.
        assert _terminal(resp.text) == {"type": "done", "reason": "completed"}

    async def test_opt_out_is_the_default(self, client: AsyncClient, user_token, monkeypatch):
        """Without the opt-in the capped turn stops and asks the user — the manual button stays default."""
        from src.app.api.v1 import data_agent as da

        sid, token = await _make_session(client, user_token)
        agent = _ScriptedAgent(cap_times=1)
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))
        monkeypatch.setattr(da.settings, "MAX_AUTO_CONTINUES", 2)

        resp = await client.post(
            f"/api/v1/data-agent/{sid}/query/stream",
            json={"query": "analise tudo"},  # no auto_continue field at all
            headers=_auth(token),
        )

        assert agent.attempts == 1
        assert agent.resume_hints == [True]  # the user IS the one who must act, so hint them
        assert _events_of(resp.text, "auto_continue") == []
        assert _terminal(resp.text) == {"type": "done", "reason": "call_limit"}

    async def test_server_ceiling_overrides_client_opt_in(self, client: AsyncClient, user_token, monkeypatch):
        """`MAX_AUTO_CONTINUES=0` disables resumption even for a client that asked for it."""
        from src.app.api.v1 import data_agent as da

        sid, token = await _make_session(client, user_token)
        agent = _ScriptedAgent(cap_times=1)
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))
        monkeypatch.setattr(da.settings, "MAX_AUTO_CONTINUES", 0)

        resp = await client.post(
            f"/api/v1/data-agent/{sid}/query/stream",
            json={"query": "analise tudo", "auto_continue": True},
            headers=_auth(token),
        )

        assert agent.attempts == 1
        assert _terminal(resp.text) == {"type": "done", "reason": "call_limit"}


class TestAutoContinueCannotLoop:
    """Acceptance criterion 2 — no infinite loop; the hard ceiling is tested, not assumed."""

    async def test_stops_at_the_ceiling_and_hands_back_to_the_user(
        self, client: AsyncClient, user_token, monkeypatch
    ):
        """An agent that caps forever runs exactly 1 + MAX_AUTO_CONTINUES times, then yields."""
        from src.app.api.v1 import data_agent as da

        sid, token = await _make_session(client, user_token)
        agent = _ScriptedAgent(cap_times=99)  # never finishes on its own
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))
        monkeypatch.setattr(da.settings, "MAX_AUTO_CONTINUES", 2)

        resp = await client.post(
            f"/api/v1/data-agent/{sid}/query/stream",
            json={"query": "loop infinito", "auto_continue": True},
            headers=_auth(token),
        )

        assert agent.attempts == 3  # the original turn + exactly two resumptions
        assert [e["attempt"] for e in _events_of(resp.text, "auto_continue")] == [1, 2]
        # Exhausted, it hands control back with the recoverable reason — the manual path still works.
        assert _terminal(resp.text) == {"type": "done", "reason": "call_limit"}

    async def test_hint_is_suppressed_only_while_resumptions_remain(
        self, client: AsyncClient, user_token, monkeypatch
    ):
        """The "envie continuar" hint appears only on the attempt the user must actually act on."""
        from src.app.api.v1 import data_agent as da

        sid, token = await _make_session(client, user_token)
        agent = _ScriptedAgent(cap_times=99)
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))
        monkeypatch.setattr(da.settings, "MAX_AUTO_CONTINUES", 2)

        await client.post(
            f"/api/v1/data-agent/{sid}/query/stream",
            json={"query": "loop", "auto_continue": True},
            headers=_auth(token),
        )

        # False while the server still intends to resume; True on the last attempt, where the
        # hint is finally honest.
        assert agent.resume_hints == [False, False, True]


class TestAutoContinueOnlyForTheRightReason:
    """Resuming the wrong termination would burn tokens or walk past a gate."""

    @pytest.mark.parametrize("reason", ["timeout", "recursion_backstop", "completed"])
    async def test_other_reasons_never_resume(self, client: AsyncClient, user_token, monkeypatch, reason):
        """Only `call_limit` resumes: a timeout spent the deadline, a backstop signals a bug."""
        from src.app.api.v1 import data_agent as da

        sid, token = await _make_session(client, user_token)

        attempts = {"n": 0}

        def _stream(_messages, _session_id, _user_id, resume_hint: bool = True):
            attempts["n"] += 1

            async def _gen():
                yield {"type": "token", "content": "parcial"}
                yield {"type": "turn_end", "reason": reason, "input_tokens": 1, "output_tokens": 1}

            return _gen()

        agent = AsyncMock()
        agent.astream_query_events = _stream
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))
        monkeypatch.setattr(da.settings, "MAX_AUTO_CONTINUES", 2)

        resp = await client.post(
            f"/api/v1/data-agent/{sid}/query/stream",
            json={"query": "x", "auto_continue": True},
            headers=_auth(token),
        )

        assert attempts["n"] == 1
        assert _terminal(resp.text) == {"type": "done", "reason": reason}


class TestAutoContinueKeepsTheTranscriptHonest:
    """The transcript must show what the user did, not what the server did on their behalf."""

    async def test_synthetic_continue_is_never_persisted(self, client: AsyncClient, user_token, monkeypatch):
        """One user message in, one user message stored — resumptions add no "continuar" bubbles."""
        from src.app.api.v1 import data_agent as da

        sid, token = await _make_session(client, user_token)
        agent = _ScriptedAgent(cap_times=2)
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))
        monkeypatch.setattr(da.settings, "MAX_AUTO_CONTINUES", 2)

        await client.post(
            f"/api/v1/data-agent/{sid}/query/stream",
            json={"query": "analise tudo", "auto_continue": True},
            headers=_auth(token),
        )

        messages = (await client.get(f"/api/v1/data-agent/{sid}/messages", headers=_auth(token))).json()[
            "messages"
        ]
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "analise tudo"
        # All three attempts read as ONE continuous answer, which is the point of transparency.
        assert messages[1]["content"] == "parte 1 parte 2 parte 3"
        # The instruction did reach the model, though — otherwise the agent would restart the task.
        assert agent.payloads[1][-1].content == da._CONTINUE_INSTRUCTION
        assert agent.payloads[1][-1].role == "user"

    async def test_resumption_carries_the_partial_answer_forward(
        self, client: AsyncClient, user_token, monkeypatch
    ):
        """The stateless path must resume WITH the work so far, or the agent redoes the analysis."""
        from src.app.api.v1 import data_agent as da

        sid, token = await _make_session(client, user_token)
        agent = _ScriptedAgent(cap_times=1)
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))
        monkeypatch.setattr(da.settings, "MAX_AUTO_CONTINUES", 2)

        await client.post(
            f"/api/v1/data-agent/{sid}/query/stream",
            json={"query": "analise tudo", "auto_continue": True},
            headers=_auth(token),
        )

        second_payload = agent.payloads[1]
        assistant_parts = [m.content for m in second_payload if m.role == "assistant"]
        assert "parte 1" in " ".join(assistant_parts)


class TestAutoContinueRespectsApprovalGate:
    """A parked approval outranks the resumption budget — the agent is waiting on the user."""

    async def test_parked_hitl_stops_resumption(self, client: AsyncClient, user_token, monkeypatch):
        """When the attempt parks an approval, the server hands control back instead of resuming."""
        from src.app.api.v1 import data_agent as da

        sid, token = await _make_session(client, user_token)
        agent = _ScriptedAgent(cap_times=99)
        monkeypatch.setattr(da, "_get_or_build_agent", AsyncMock(return_value=agent))
        monkeypatch.setattr(da.settings, "MAX_AUTO_CONTINUES", 2)
        # The first attempt parks an approval card.
        monkeypatch.setattr(
            da,
            "_new_hitl_events",
            AsyncMock(return_value=[{"type": "hitl_request", "id": 1, "title": "Aprovar plano"}]),
        )

        resp = await client.post(
            f"/api/v1/data-agent/{sid}/query/stream",
            json={"query": "gere o relatório", "auto_continue": True},
            headers=_auth(token),
        )

        assert agent.attempts == 1  # no resumption walked past the approval gate
        assert len(_events_of(resp.text, "hitl_request")) == 1
        assert _terminal(resp.text) == {"type": "done", "reason": "call_limit"}
