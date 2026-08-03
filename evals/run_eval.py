"""Non-interactive golden-eval runner (issue #70): objective, deterministic scoring with gates.

Drives the REAL API (spawned via ``tests/e2e/harness.py``) over the versioned golden dataset and
scores each case against its explicit rubric — termination reason, tools used, answer content,
artifact generation/provenance, duration. Aggregates are compared to ``evals/config.yaml``
thresholds; any breach exits 1, which is the CI gate.

Modes:
- ``--mode harness``  scripted mock LLM (zero tokens). Runs the ``smoke``-tagged cases by default —
  validates the eval machinery + the API/provider plumbing on every PR.
- ``--mode live``     the real provider configured via ``MODEL`` (+ its key) in the environment.
  Runs the full dataset by default — the number that answers "did quality regress?".

Usage:
    uv run python -m evals.run_eval --mode harness
    MODEL=anthropic:claude-sonnet-5 uv run python -m evals.run_eval --mode live
    uv run python -m evals.run_eval --mode live --tags analysis,docs --report out.json

Postgres must be reachable (``make db-up`` locally; a service container in CI). The complementary
Langfuse trace evaluator (``src/evals``, ``make eval``) scores production traffic after the fact;
this runner is the pre-merge/nightly regression gate.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from rich.console import Console
from rich.table import Table

from tests.e2e.harness import launch_stack

_EVALS_DIR = Path(__file__).resolve().parent
_console = Console()


def _load_dataset(tags: list[str] | None) -> list[dict]:
    cases = json.loads((_EVALS_DIR / "golden_set.json").read_text())
    for case in cases:
        if not case.get("id") or not case.get("turns") or "criteria" not in case:
            raise ValueError(f"golden_set.json: case missing id/turns/criteria: {case}")
    if tags:
        cases = [c for c in cases if any(t in c.get("tags", []) for t in tags)]
    if not cases:
        raise ValueError(f"no cases match tags={tags}")
    return cases


def _new_session(client: httpx.Client, user_token: str, workspace: str) -> tuple[str, dict]:
    r = client.post("/auth/session", headers={"Authorization": f"Bearer {user_token}"})
    r.raise_for_status()
    sess = r.json()
    headers = {"Authorization": f"Bearer {sess['token']['access_token']}"}
    client.post("/data-agent/grant-folder", json={"path": workspace}, headers=headers).raise_for_status()
    return sess["session_id"], headers


def _stream(client: httpx.Client, sid: str, headers: dict, query: str) -> list[dict]:
    events = []
    with client.stream("POST", f"/data-agent/{sid}/query/stream", json={"query": query}, headers=headers) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _check_artifact(
    client: httpx.Client, user_token: str, sid: str, headers: dict, spec: dict, failures: list[str]
) -> None:
    """Confirm the parked artifact and verify format + (optionally) claim provenance."""
    user_headers = {"Authorization": f"Bearer {user_token}"}
    pending = [
        a for a in client.get("/hitl/pending", headers=user_headers).json()
        if a["session_id"] == sid and a["action_type"] == "export_artifact"
    ]
    if not pending:
        failures.append("artifact: no export_artifact pending action was parked")
        return
    action = pending[0]
    payload = action.get("payload") or {}
    if payload.get("fmt") != spec.get("format"):
        failures.append(f"artifact: format {payload.get('fmt')!r} != expected {spec.get('format')!r}")
    if spec.get("sources_required"):
        claims = [c for s in (payload.get("spec", {}).get("sections") or []) for c in (s.get("claims") or [])]
        unsourced = [c["text"][:40] for c in claims if not c.get("source")]
        if not claims:
            failures.append("artifact: no claims in the spec")
        elif unsourced:
            failures.append(f"artifact: {len(unsourced)} claim(s) without source, e.g. {unsourced[:2]}")
    r = client.post(f"/hitl/{action['id']}/confirm", headers=user_headers)
    if r.status_code != 200 or not r.json().get("result", {}).get("exported"):
        failures.append(f"artifact: confirm failed ({r.status_code})")
        return
    r = client.get(f"/data-agent/{sid}/artifacts/{action['id']}/download", headers=headers)
    if r.status_code != 200 or len(r.content) < 5_000:
        failures.append(f"artifact: download failed ({r.status_code}, {len(r.content)} bytes)")


def _run_case(client: httpx.Client, user_token: str, workspace: str, case: dict) -> dict:
    criteria = case["criteria"]
    failures: list[str] = []
    started = time.monotonic()
    sid, headers = _new_session(client, user_token, workspace)

    answer, tools_used, termination = "", set(), None
    for query in case["turns"]:
        events = _stream(client, sid, headers, query)
        answer = "".join(e.get("content", "") for e in events if e.get("type") == "token")
        tools_used |= {e.get("name", "") for e in events if e.get("type") == "tool_start"}
        terminal = events[-1] if events else {}
        termination = terminal.get("reason", "error") if terminal.get("type") == "done" else "error"
        if termination == "error":
            break
    elapsed = time.monotonic() - started

    expected_termination = criteria.get("termination", "completed")
    if termination != expected_termination:
        failures.append(f"termination: {termination!r} != expected {expected_termination!r}")
    for tool in criteria.get("expect_tools", []):
        if tool not in tools_used:
            failures.append(f"tool not used: {tool!r} (used: {sorted(tools_used)})")
    lowered = answer.lower()
    for group in criteria.get("answer_contains_any", []):
        if not any(opt.lower() in lowered for opt in group):
            failures.append(f"answer missing all of: {group}")
    for banned in criteria.get("answer_not_contains", []):
        if banned.lower() in lowered:
            failures.append(f"answer contains banned text: {banned!r}")
    if criteria.get("artifact"):
        _check_artifact(client, user_token, sid, headers, criteria["artifact"], failures)
    if elapsed > criteria.get("max_seconds", 600):
        failures.append(f"too slow: {elapsed:.0f}s > {criteria.get('max_seconds')}s")

    return {
        "id": case["id"],
        "tags": case.get("tags", []),
        "passed": not failures,
        "completed": termination == "completed",
        "termination": termination,
        "elapsed_seconds": round(elapsed, 1),
        "tools_used": sorted(tools_used),
        "failures": failures,
        "answer_preview": answer[:300],
    }


def main() -> int:
    """Run the golden evals; return the process exit code (0 = above thresholds)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["harness", "live"], required=True)
    parser.add_argument("--tags", help="comma-separated tag filter (default: smoke for harness, all for live)")
    parser.add_argument("--report", help="report path (default: evals/reports/<mode>-<timestamp>.json)")
    args = parser.parse_args()

    config = yaml.safe_load((_EVALS_DIR / "config.yaml").read_text())
    profile = config[args.mode]
    tags = args.tags.split(",") if args.tags else (["smoke"] if args.mode == "harness" else None)
    cases = _load_dataset(tags)

    # The granted folder is a throwaway copy of the committed fixture, so runs never mutate it.
    workdir = Path(tempfile.mkdtemp(prefix="eval-ws-"))
    workspace = workdir / "workspace"
    shutil.copytree(_EVALS_DIR / "workspace", workspace)

    _console.print(f"[bold]golden evals[/bold] mode={args.mode} cases={len(cases)} tags={tags or 'all'}")
    stack = launch_stack(
        workspace,
        workdir,
        call_limit=profile["call_limit"],
        turn_timeout=profile["turn_timeout_seconds"],
        use_mock_llm=(args.mode == "harness"),
    )
    results = []
    try:
        client = httpx.Client(base_url=stack.base, timeout=profile["turn_timeout_seconds"] + 60, trust_env=False)
        client.post("/auth/register", json={"username": "eval", "email": "eval@test.com", "password": "Ev@l12345!"})
        r = client.post("/auth/login", data={"username": "eval@test.com", "password": "Ev@l12345!"})
        r.raise_for_status()
        user_token = r.json()["access_token"]
        for case in cases:
            result = _run_case(client, user_token, str(workspace), case)
            results.append(result)
            mark = "[green]PASS[/green]" if result["passed"] else "[red]FAIL[/red]"
            _console.print(f"  {mark} {case['id']} ({result['elapsed_seconds']}s)")
            for failure in result["failures"]:
                _console.print(f"       [yellow]{failure}[/yellow]")
    finally:
        stack.stop()

    completion_rate = sum(r["completed"] for r in results) / len(results)
    pass_rate = sum(r["passed"] for r in results) / len(results)
    thresholds = config["thresholds"]
    breaches = []
    if completion_rate < thresholds["min_completion_rate"]:
        breaches.append(f"completion_rate {completion_rate:.2f} < {thresholds['min_completion_rate']}")
    if pass_rate < thresholds["min_pass_rate"]:
        breaches.append(f"pass_rate {pass_rate:.2f} < {thresholds['min_pass_rate']}")

    report = {
        "mode": args.mode,
        "model": "mock" if args.mode == "harness" else os.getenv("MODEL", "(env MODEL)"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cases": len(results),
        "completion_rate": round(completion_rate, 3),
        "pass_rate": round(pass_rate, 3),
        "thresholds": thresholds,
        "breaches": breaches,
        "results": results,
    }
    report_path = Path(
        args.report
        or _EVALS_DIR / "reports" / f"{args.mode}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    table = Table(title="golden evals")
    table.add_column("métrica")
    table.add_column("valor")
    table.add_column("piso")
    table.add_row("completion_rate", f"{completion_rate:.2f}", str(thresholds["min_completion_rate"]))
    table.add_row("pass_rate", f"{pass_rate:.2f}", str(thresholds["min_pass_rate"]))
    _console.print(table)
    _console.print(f"report: {report_path}")
    if breaches:
        _console.print(f"[bold red]THRESHOLD BREACH:[/bold red] {'; '.join(breaches)}")
        return 1
    _console.print("[bold green]above thresholds[/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
