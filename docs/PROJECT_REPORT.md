# Project Report — Agent Harness (production-ready template)

_Generated 2026-07-03. Covers: what changed in this session, what the project is and does, and
where to take it for real, high-ROI use._

---

## 1. What was changed in this session

Goal: migrate the repo off Cursor onto Claude Code, add a first-class Postgres Docker service, and
document everything under `.claude/` and `CLAUDE.md`.

### Added
| Path | Purpose |
|------|---------|
| `CLAUDE.md` | Primary guidance for Claude Code: project map, dev commands, the 10 conventions, how to build an agent, pitfalls. |
| `.claude/settings.json` | Permission allowlist for safe commands (pytest, ruff, make, docker, git read ops); `ask` gates on destructive ops. |
| `.claude/commands/new-agent.md` | `/new-agent` — scaffolds a full agent (package + route + DTO + rate limit). |
| `.claude/commands/plan.md` | `/plan` — writes a checkboxed plan to `.claude/plans/` before coding. |
| `.claude/commands/run.md` | `/run` — brings up DB + API and verifies `/health`. |
| `.claude/commands/db.md` | `/db up\|down\|logs\|psql\|reset` — manage local Postgres. |
| `.claude/agents/harness-reviewer.md` | Subagent that reviews diffs against the repo conventions. |
| `.claude/skills/scaffold-agent/SKILL.md` | Skill encoding the canonical agent structure. |
| `.claude/plans/.gitkeep` | Home for task plans. |
| `scripts/postgres/init/01-init-extensions.sql` | Enables the `pgvector` extension on first DB init. |
| `.env.development` | Local dev config (gitignored) so `make db-up` + `make dev` work out of the box. |
| `docs/PROJECT_REPORT.md` | This report. |

### Modified
- **`docker-compose.yml`** — `db` service rebuilt: named container `agent-harness-db`, env with
  safe defaults (no longer hard-fails without `.env`), pgvector init script mounted, `start_period`
  added to the healthcheck, dropped the obsolete `version:` key.
- **`Makefile`** — new `db-up` (waits for healthy), `db-down`, `db-logs`, `db-reset` targets + help.
- **`.gitignore`** — stopped ignoring all of `/.claude/`; now versions the shared config and
  ignores only `settings.local.json` and `plans/*`.
- **`AGENTS.md`** — plan path updated `.agent/plans/` → `.claude/plans/`.

### Removed
- **`.cursor/`** (rules + gitignore) — Cursor is no longer used; its rules were folded into
  `CLAUDE.md`.

### How to run now
```bash
make install     # uv sync
make db-up       # Postgres + pgvector, waits until healthy
# fill OPENAI_API_KEY and JWT_SECRET_KEY in .env.development
make dev         # API on http://localhost:8000  (Swagger at /docs)
```

---

## 2. What this project is

A **harness for running AI agents in production**. You write the agent's logic; the harness supplies
the infrastructure every serious agent needs but nobody wants to rebuild:

- **API layer** — FastAPI (async, uvloop), JWT auth with multi-session management, per-endpoint
  rate limiting (slowapi), SSE streaming.
- **Memory & state** — long-term semantic memory per user (mem0ai + pgvector) with background,
  non-blocking updates; conversation state persisted via LangGraph `AsyncPostgresSaver` checkpoints.
- **Observability** — Langfuse tracing on every LLM call; Prometheus metrics; pre-built Grafana
  dashboards; structured `structlog` logging with request/session/user context binding.
- **Safety** — guardrails module (content filter, PII detection, safety checks) and an agent
  middleware pipeline (error handling, logging, summarization, message trimming, memory).
- **Tools** — built-in tools (web search, "think") plus MCP client support (multi-server, auto
  reconnect, graceful degradation) and a sample MCP server.
- **Evaluation** — metric-based eval framework driven by Langfuse traces; metrics are just markdown
  files (relevancy, helpfulness, conciseness, hallucination, toxicity).
- **DevOps** — Docker Compose stack (Postgres, Prometheus, Grafana, cAdvisor), per-env configs,
  Makefile, GitHub Actions.

### How it's organized
An agent is a **self-contained directory** under `src/app/agents/`. Three references ship with it:
`chatbot` (simplest), `text_to_sql` (skills + tools), `open_deep_research` (multi-subgraph
supervisor/researcher). The rest of `src/app/core/` is shared infrastructure the agent plugs into.

### Request lifecycle (chat)
```
Client → FastAPI route (JWT auth + rate limit)
       → load long-term memory (mem0/pgvector) → build prompt
       → LangGraph agent (checkpointed state, tools/MCP) with Langfuse tracing
       → stream/return response
       → background: extract & store new memories (asyncio task, non-blocking)
Prometheus scrapes /metrics → Grafana dashboards
```

---

## 3. Where to take it — high-ROI directions

The template's value is that the expensive, boring 80% (auth, memory, tracing, persistence, rate
limiting, evals) is already done. The fastest path to "real and profitable" is to drop a
**domain-specific agent** into `src/app/agents/` and sell the outcome, not the tech. Candidates,
roughly by effort-to-return:

**A. Vertical support / operations copilot (fastest to revenue).**
Point the agent at a company's knowledge base + a few tools (order lookup, ticket creation) via MCP.
The harness already gives you per-user memory (it remembers each customer), tracing (you can prove
quality to the buyer), and guardrails (PII). Sell as a per-seat or per-resolution SaaS. The
`text_to_sql` agent is a working starting point for "ask your data" internal tools.

**B. Analytics / "chat with your database" product.**
`text_to_sql` + the schema-exploration skill is 60% of a self-serve BI assistant. Add row-level
auth and result caching (cache only successful queries — convention #7) and you have a sellable
internal-analytics tool. ROI is high because it replaces analyst back-and-forth.

**C. Research / due-diligence automation.**
`open_deep_research` (supervisor + researcher subgraphs) already does multi-step web research with a
think tool. Wrap it for a niche (legal, market, competitive intel) with citation enforcement via the
existing eval metrics (hallucination, relevancy). Charge per report.

**D. Platform play — multi-tenant agent hosting.**
The harness is close to a "Fly.io for agents." To get there: add tenant isolation (schema-per-tenant
or row-level security on Postgres), usage metering (you already emit Prometheus metrics — bill on
LLM inference duration/tokens), and a self-serve agent registry. Higher effort, but the moat is real.

### Concrete next steps to make any of the above production-real
1. **Cost & billing** — emit token/cost per request into Prometheus and surface a per-tenant cost
   dashboard in Grafana (the plumbing is already there).
2. **Fix the schema story** — `schema.sql` is legacy SQLite DDL; standardize on Alembic migrations
   for the app tables so prod deploys are reproducible.
3. **Eval gate in CI** — run the eval framework on a fixed trace set in GitHub Actions and fail the
   build if hallucination/relevancy regresses. This is what lets you ship agent changes safely.
4. **Harden guardrails** — the guardrail middleware exists; wire it into every agent's pipeline and
   add an output moderation pass before responses reach users.
5. **Load & resilience** — connection-pool tuning is present; add a queue/back-pressure layer for
   LLM calls and circuit breakers (tenacity is already a dependency).

The short version: **don't rebuild the harness — pick one vertical from A–C, ship the outcome, and
use the built-in evals + tracing as your quality and sales proof.**
