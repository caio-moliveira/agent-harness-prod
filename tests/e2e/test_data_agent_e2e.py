"""E2E: real API + real Postgres + scripted mock LLM — the product's core flows, no tokens spent.

Requires ``RUN_E2E=1`` and a reachable Postgres (see env vars below; defaults match the CI
service container). The session fixture starts the mock LLM in-process and the API as a
subprocess pointed at it via ``MODEL=openai:mock-gpt`` + ``OPENAI_BASE_URL`` — the exact
provider plumbing production uses.

Covered scenarios (each also asserts the SSE terminal taxonomy from the turn-limit policy):
- auth → session → grant-folder → streamed query with a real ``ls`` tool round → ``done{completed}``
- runaway tool loop → ends gracefully at exactly ``MODEL_CALL_LIMIT`` calls → ``done{call_limit}``
- hung provider → wall-clock timeout → ``done{timeout}`` with the turn persisted
- artifact + HITL: gerar_artefato → pending action → confirm → downloadable .docx
- Prometheus terminations counter incremented per reason
"""

import json
import os
import time

import httpx
import pytest

from tests.e2e.harness import launch_stack

pytestmark = [
    pytest.mark.skipif(os.getenv("RUN_E2E") != "1", reason="E2E: set RUN_E2E=1 (needs Postgres; see module docstring)"),
    pytest.mark.e2e,
]

_CALL_LIMIT = 8
_TURN_TIMEOUT = 20


@pytest.fixture(scope="session")
def api(tmp_path_factory):
    """Mock LLM (in-process) + API (subprocess) wired together; yields the API base URL."""
    workspace = tmp_path_factory.mktemp("e2e-data")
    (workspace / "vendas.csv").write_text("data,produto,quantidade\n2026-07-01,Widget A,18\n")
    (workspace / "notas.md").write_text("# Notas\nWidget B tem margem maior\n")

    stack = launch_stack(
        workspace,
        tmp_path_factory.mktemp("e2e-logs"),
        call_limit=_CALL_LIMIT,
        turn_timeout=_TURN_TIMEOUT,
    )
    try:
        yield {"base": stack.base, "workspace": stack.workspace}
    finally:
        stack.stop()


@pytest.fixture(scope="session")
def client(api):
    """HTTP client + registered user with an active session and granted folder."""
    c = httpx.Client(base_url=api["base"], timeout=90, trust_env=False)
    c.post("/auth/register", json={"username": "e2e", "email": "e2e@test.com", "password": "S3nh@forte123"})
    r = c.post("/auth/login", data={"username": "e2e@test.com", "password": "S3nh@forte123"})
    r.raise_for_status()
    user_token = r.json()["access_token"]
    yield {"http": c, "user_token": user_token, "api": api}
    c.close()


def _new_session(client) -> tuple[str, dict]:
    r = client["http"].post("/auth/session", headers={"Authorization": f"Bearer {client['user_token']}"})
    r.raise_for_status()
    sess = r.json()
    sid = sess["session_id"]
    headers = {"Authorization": f"Bearer {sess['token']['access_token']}"}
    r = client["http"].post("/data-agent/grant-folder", json={"path": client["api"]["workspace"]}, headers=headers)
    r.raise_for_status()
    return sid, headers


def _stream(client, sid: str, headers: dict, query: str) -> list[dict]:
    events = []
    with client["http"].stream(
        "POST", f"/data-agent/{sid}/query/stream", json={"query": query}, headers=headers
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _terminal(events: list[dict]) -> dict:
    assert events, "stream produced no events"
    return events[-1]


class TestCoreFlow:
    """The product's happy path over the real provider plumbing."""

    def test_streamed_query_runs_tools_and_completes(self, client):
        """A query streams a real ls tool round against the granted folder and completes."""
        sid, headers = _new_session(client)
        events = _stream(client, sid, headers, "Liste os arquivos da pasta.")
        tools = [e for e in events if e.get("type") == "tool_start"]
        assert any(e.get("name") == "ls" for e in tools)
        answer = "".join(e.get("content", "") for e in events if e.get("type") == "token")
        assert "vendas.csv" in answer
        assert _terminal(events) == {"type": "done", "reason": "completed"}

    def test_history_is_rebuilt_on_second_turn(self, client):
        """The second turn carries the first turn's tool results (no tool round needed)."""
        sid, headers = _new_session(client)
        _stream(client, sid, headers, "Liste os arquivos da pasta.")
        events = _stream(client, sid, headers, "E o que mais você viu?")
        assert _terminal(events)["type"] == "done"
        r = client["http"].get(f"/data-agent/{sid}/messages", headers=headers)
        assert r.status_code == 200 and len(r.json()["messages"]) == 4


class TestTurnLimits:
    """The turn-limit policy end to end: every boundary ends politely, never a generic error."""

    def test_runaway_loop_ends_gracefully_at_call_cap(self, client):
        """A tool loop stops at exactly MODEL_CALL_LIMIT with done{call_limit} — no recursion crash."""
        sid, headers = _new_session(client)
        events = _stream(client, sid, headers, "SEMPREFERRAMENTA use ferramentas sem parar")
        tools = [e for e in events if e.get("type") == "tool_start"]
        assert len(tools) == _CALL_LIMIT
        assert _terminal(events) == {"type": "done", "reason": "call_limit"}
        assert not any(e.get("type") == "error" for e in events)

    def test_hung_provider_times_out_gracefully(self, client):
        """A hung provider trips the wall-clock timeout with done{timeout} and a persisted turn."""
        sid, headers = _new_session(client)
        start = time.monotonic()
        events = _stream(client, sid, headers, "TRAVE60 analise isso")
        elapsed = time.monotonic() - start
        assert _terminal(events) == {"type": "done", "reason": "timeout"}
        assert elapsed < _TURN_TIMEOUT + 15  # ended at the ceiling, not at the mock's 60s stall
        r = client["http"].get(f"/data-agent/{sid}/messages", headers=headers)
        assert r.status_code == 200 and len(r.json()["messages"]) >= 1

    def test_terminations_metric_counts_each_reason(self, client):
        """The Prometheus counter carries completed/call_limit/timeout after the scenarios above."""
        base_root = client["api"]["base"].rsplit("/api/v1", 1)[0]
        text = httpx.get(f"{base_root}/metrics", timeout=10, trust_env=False).text
        for reason in ("completed", "call_limit", "timeout"):
            assert f'agent_turn_terminations_total{{agent="data_agent",reason="{reason}"}}' in text


class TestArtifactHitl:
    """gerar_artefato parks an approval; confirming generates a downloadable .docx."""

    def test_artifact_flow_generates_downloadable_docx(self, client):
        """hitl_request streams inline; confirm produces the file; download serves real docx bytes."""
        sid, headers = _new_session(client)
        events = _stream(client, sid, headers, "GEREARTEFATO gere o relatório em docx")
        hitl = [e for e in events if e.get("type") == "hitl_request"]
        assert hitl and hitl[0]["action_type"] == "export_artifact"

        user_headers = {"Authorization": f"Bearer {client['user_token']}"}
        r = client["http"].get("/hitl/pending", headers=user_headers)
        r.raise_for_status()
        mine = [a for a in r.json() if a["session_id"] == sid]
        assert mine, "pending action for this session not listed"
        action_id = mine[0]["id"]

        r = client["http"].post(f"/hitl/{action_id}/confirm", headers=user_headers)
        assert r.status_code == 200 and r.json()["result"]["exported"] is True

        r = client["http"].get(f"/data-agent/{sid}/artifacts/{action_id}/download", headers=headers)
        assert r.status_code == 200
        assert "wordprocessingml" in r.headers.get("content-type", "")
        assert len(r.content) > 10_000  # a real .docx, not an error payload
