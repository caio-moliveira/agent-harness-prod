"""Load test for the streaming turn path (#75) — capacity in numbers, not guesses.

Runs N concurrent chat sessions against the real API (spawned by ``tests/e2e/harness.py`` with the
scripted mock LLM, so it costs nothing and isolates OUR limits from provider latency). Reports
p50/p95/p99 per turn, throughput and failures.

The number this exists to find: **the Postgres pool ceiling**. A streaming turn holds a connection
for its whole duration, so the pool (``POSTGRES_POOL_SIZE`` + ``POSTGRES_MAX_OVERFLOW``, default
20+10) saturates long before CPU does — the real cap on concurrent users is that sum, and it must
be measured rather than assumed.

    uv run python -m tests.load.streaming_load --users 20 --turns 3

Needs Postgres up (``make db-up``). Not part of the CI suite: it is a capacity exercise an operator
runs deliberately, with numbers recorded in docs/operations.md.
"""

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

from tests.e2e.harness import launch_stack

_console = Console()


@dataclass
class Results:
    """Per-turn latencies and failures across the whole run."""

    latencies: list[float] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def wall_seconds(self) -> float:
        """Wall-clock seconds the run took (never zero, so it is safe to divide by)."""
        return max(self.finished_at - self.started_at, 1e-9)

    def percentile(self, p: float) -> float:
        """The ``p``-th percentile of the recorded turn latencies."""
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        idx = min(int(p / 100 * len(ordered)), len(ordered) - 1)
        return ordered[idx]


async def _run_user(client: httpx.AsyncClient, workspace: str, turns: int, results: Results) -> None:
    """One simulated user: create a session, grant the folder, then run `turns` streamed turns."""
    try:
        r = await client.post("/auth/session", headers=client.headers)
        r.raise_for_status()
        sess = r.json()
        sid = sess["session_id"]
        headers = {"Authorization": f"Bearer {sess['token']['access_token']}"}
        await client.post("/data-agent/grant-folder", json={"path": workspace}, headers=headers)

        for _ in range(turns):
            started = time.monotonic()
            terminal = None
            async with client.stream(
                "POST", f"/data-agent/{sid}/query/stream", json={"query": "Liste os arquivos."}, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    results.failures.append(f"HTTP {resp.status_code}")
                    continue
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        terminal = json.loads(line[6:])
            elapsed = time.monotonic() - started
            if terminal and terminal.get("type") == "done":
                results.latencies.append(elapsed)
            else:
                results.failures.append(f"terminal={terminal}")
    except Exception as exc:  # a failed virtual user must not abort the run
        results.failures.append(f"{type(exc).__name__}: {exc}")


async def _main(users: int, turns: int) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="load-"))
    workspace = workdir / "workspace"
    workspace.mkdir()
    (workspace / "vendas.csv").write_text("produto,qtd\nWidget A,18\n")

    _console.print(f"[bold]load test[/bold] users={users} turns/user={turns} (mock LLM, no tokens)")
    stack = launch_stack(workspace, workdir, call_limit=8, turn_timeout=120)
    results = Results()
    try:
        async with httpx.AsyncClient(base_url=stack.base, timeout=180, trust_env=False) as client:
            await client.post(
                "/auth/register", json={"username": "load", "email": "load@test.com", "password": "S3nh@forte123"}
            )
            r = await client.post("/auth/login", data={"username": "load@test.com", "password": "S3nh@forte123"})
            r.raise_for_status()
            client.headers = httpx.Headers({"Authorization": f"Bearer {r.json()['access_token']}"})

            results.started_at = time.monotonic()
            await asyncio.gather(*(_run_user(client, str(workspace), turns, results) for _ in range(users)))
            results.finished_at = time.monotonic()
    finally:
        stack.stop()

    total = len(results.latencies) + len(results.failures)
    table = Table(title=f"streaming load — {users} usuários simultâneos")
    table.add_column("métrica")
    table.add_column("valor", justify="right")
    table.add_row("turnos concluídos", str(len(results.latencies)))
    table.add_row("falhas", str(len(results.failures)))
    table.add_row("throughput (turnos/s)", f"{len(results.latencies) / results.wall_seconds:.2f}")
    if results.latencies:
        table.add_row("p50 (s)", f"{statistics.median(results.latencies):.2f}")
        table.add_row("p95 (s)", f"{results.percentile(95):.2f}")
        table.add_row("p99 (s)", f"{results.percentile(99):.2f}")
        table.add_row("máx (s)", f"{max(results.latencies):.2f}")
    _console.print(table)

    if results.failures:
        _console.print(f"[red]amostra de falhas:[/red] {results.failures[:3]}")
    # A run where more than 1% of turns failed is not a valid capacity measurement.
    return 1 if total and len(results.failures) / total > 0.01 else 0


def main() -> int:
    """Parse arguments and run the load scenario."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--users", type=int, default=10, help="concurrent virtual users")
    parser.add_argument("--turns", type=int, default=2, help="streamed turns per user")
    args = parser.parse_args()
    return asyncio.run(_main(args.users, args.turns))


if __name__ == "__main__":
    sys.exit(main())
