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
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.e2e import mock_llm_server

pytestmark = [
    pytest.mark.skipif(os.getenv("RUN_E2E") != "1", reason="E2E: set RUN_E2E=1 (needs Postgres; see module docstring)"),
    pytest.mark.e2e,
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CALL_LIMIT = 8
_TURN_TIMEOUT = 20


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def api(tmp_path_factory):
    """Mock LLM (in-process) + API (subprocess) wired together; yields the API base URL."""
    mock = mock_llm_server.start()
    mock_port = mock.server_address[1]

    workspace = tmp_path_factory.mktemp("e2e-data")
    (workspace / "vendas.csv").write_text("data,produto,quantidade\n2026-07-01,Widget A,18\n")
    (workspace / "notas.md").write_text("# Notas\nWidget B tem margem maior\n")

    api_port = _free_port()
    env = {
        **os.environ,
        "APP_ENV": "development",
        "MODEL": "openai:mock-gpt",
        "OPENAI_BASE_URL": f"http://127.0.0.1:{mock_port}/v1",
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "MODEL_CALL_LIMIT": str(_CALL_LIMIT),
        "TURN_TIMEOUT_SECONDS": str(_TURN_TIMEOUT),
        "LONG_TERM_MEMORY_ENABLED": "false",
        "MCP_ENABLED": "false",
        "JWT_SECRET_KEY": "e2e-secret",
        "SANDBOX_ALLOWED_ROOTS": str(workspace),
        "POSTGRES_HOST": os.getenv("E2E_POSTGRES_HOST", "127.0.0.1"),
        "POSTGRES_PORT": os.getenv("E2E_POSTGRES_PORT", "5432"),
        "POSTGRES_DB": os.getenv("E2E_POSTGRES_DB", "mydb"),
        "POSTGRES_USER": os.getenv("E2E_POSTGRES_USER", "myuser"),
        "POSTGRES_PASSWORD": os.getenv("E2E_POSTGRES_PASSWORD", "mypassword"),
        "LOG_FORMAT": "console",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    # Logs go to a FILE, never a PIPE: nobody drains a pipe during the run, so a chatty scenario
    # (the runaway loop logs a lot) would fill the 64KB pipe buffer and block the API on write().
    log_path = tmp_path_factory.mktemp("e2e-logs") / "api.log"
    log_file = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.app.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
        cwd=_REPO_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{api_port}/api/v1"
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"API process died during startup:\n{log_path.read_text()[-4000:]}")
            try:
                if httpx.get(f"{base}/openapi.json", timeout=2, trust_env=False).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError(f"API did not become healthy within 60s:\n{log_path.read_text()[-4000:]}")
        yield {"base": base, "workspace": str(workspace)}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # A mock handler may still be mid-stall (TRAVE60) holding a connection open.
            proc.kill()
            proc.wait(timeout=10)
        log_file.close()
        mock.shutdown()


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
