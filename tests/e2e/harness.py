"""Launcher for the app stack under test: mock LLM (in-process) + API (subprocess) + Postgres.

Shared by the E2E suite (``tests/e2e``) and the golden-eval runner (``evals/run_eval.py``): both
need a real API wired to a scripted OpenAI-compatible mock (zero tokens) or to a real provider.
Postgres must already be reachable (locally via ``make db-up``; in CI via a service container).
"""

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from tests.e2e import mock_llm_server

_REPO_ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    """An OS-assigned free TCP port on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Stack:
    """A running API (+ optional mock LLM) ready to receive requests."""

    base: str  # API base URL, e.g. http://127.0.0.1:8123/api/v1
    workspace: str  # folder the sessions may grant
    log_path: Path
    _proc: subprocess.Popen
    _log_file: object
    _mock: object | None

    def stop(self) -> None:
        """Terminate the API (kill if a stalled connection holds it) and the mock server."""
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=10)
        self._log_file.close()
        if self._mock is not None:
            self._mock.shutdown()


def launch_stack(
    workspace: Path,
    log_dir: Path,
    *,
    call_limit: int = 8,
    turn_timeout: int = 20,
    model: str | None = None,
    use_mock_llm: bool = True,
) -> Stack:
    """Start the API subprocess (and the mock LLM unless ``use_mock_llm=False``).

    With the mock (default), the API is pointed at it via ``MODEL=openai:mock-gpt`` +
    ``OPENAI_BASE_URL`` — the exact provider plumbing production uses, zero tokens spent.
    Without it, ``model`` (or the caller's ``MODEL`` env) selects a real provider whose key must
    be present in the environment.
    """
    mock = None
    provider_env: dict[str, str] = {}
    if use_mock_llm:
        mock = mock_llm_server.start()
        provider_env = {
            "MODEL": "openai:mock-gpt",
            "OPENAI_BASE_URL": f"http://127.0.0.1:{mock.server_address[1]}/v1",
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
        }
    elif model:
        provider_env = {"MODEL": model}

    api_port = free_port()
    env = {
        **os.environ,
        "APP_ENV": "development",
        **provider_env,
        "MODEL_CALL_LIMIT": str(call_limit),
        "TURN_TIMEOUT_SECONDS": str(turn_timeout),
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
    # would fill the 64KB pipe buffer and block the API on write().
    log_path = log_dir / "api.log"
    log_file = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.app.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
        cwd=_REPO_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{api_port}/api/v1"
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
        proc.kill()
        raise RuntimeError(f"API did not become healthy within 60s:\n{log_path.read_text()[-4000:]}")
    return Stack(base=base, workspace=str(workspace), log_path=log_path, _proc=proc, _log_file=log_file, _mock=mock)
