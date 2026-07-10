# CLAUDE.md

Guidance for Claude Code when working in this repository. Read this before making changes.

## What this project is

A **production-ready harness for AI agents**. You write the agent logic; the harness provides
authentication, long-term memory, state persistence, rate limiting, guardrails, observability,
and evaluation out of the box.

Stack: **LangGraph** (agent workflows) · **FastAPI** (async API) · **Langfuse** (LLM tracing) ·
**PostgreSQL + pgvector** (memory + checkpoints) · **mem0ai** (long-term memory) · **MCP** (tools) ·
**Prometheus + Grafana** (monitoring).

An agent is a self-contained directory under `src/app/agents/`. Everything else is shared infra.

## Repository map

```
src/
├── app/
│   ├── main.py                # FastAPI app + lifespan (startup/shutdown)
│   ├── init.py                # Langfuse, MCP, repositories bootstrap
│   ├── agents/                # ← YOUR AGENTS LIVE HERE
│   │   ├── chatbot/           #   reference agent (simplest)
│   │   ├── text_to_sql/       #   reference agent (skills + tools)
│   │   ├── open_deep_research/#   reference agent (multi-subgraph)
│   │   └── tools/             #   shared tools (search, think)
│   ├── api/
│   │   ├── v1/                # versioned routes (auth, chatbot, deep_research, text_to_sql)
│   │   │   └── dtos/          # Pydantic request/response models
│   │   ├── security/          # JWT auth + rate limiter
│   │   └── metrics/           # Prometheus HTTP metrics middleware
│   └── core/
│       ├── common/config.py   # Settings (env-driven, single source of truth)
│       ├── checkpoint/        # LangGraph AsyncPostgresSaver wiring
│       ├── context/           # context manager + summarizer
│       ├── db/                # SQLModel engine + async connection pool
│       ├── guardrails/        # content filter, PII, safety checks
│       ├── llm/               # LLM factory + retry helpers
│       ├── mcp/               # MCP session manager
│       ├── memory/            # mem0 long-term memory
│       ├── middleware/        # agent middleware pipeline
│       ├── session/ · user/   # SQLModel models, repositories, DTOs
│       └── metrics/           # LLM metrics
├── cli/                       # terminal clients for each agent
├── evals/                     # metric-based evaluation framework
└── mcp/server.py              # sample MCP server

frontend/                      # React chat UI (Vite + React 19 + TS + Tailwind v4)
├── src/lib/api.ts             # typed API client + SSE streaming
├── src/context/AuthContext.tsx# two-token auth (user token → session token)
└── src/components/            # LoginScreen, ChatScreen, Sidebar, MessageBubble, Composer
```

## Dev commands

```bash
make install              # uv sync
make db-up                # start ONLY Postgres (pgvector) in Docker  ← start here
make dev                  # run API on :8000 (reload), reads .env.development
uv run pytest tests/      # run tests
make lint                 # ruff check
make format               # ruff format
make eval                 # interactive evaluation

make docker-compose-up ENV=development    # full stack (API + db + Prometheus + Grafana + cAdvisor)
```

Swagger: `http://localhost:8000/docs` · Grafana: `http://localhost:3000` (admin/admin) · Prometheus: `http://localhost:9090`

Config lives in `.env.<environment>` (development/staging/production). Copy `.env.example` to
`.env.development` and fill `OPENAI_API_KEY`, `JWT_SECRET_KEY`, `LANGFUSE_*`. All settings are
read in `src/app/core/common/config.py` — that file is the single source of truth for config.

### Frontend (`frontend/`)

```bash
cd frontend && npm install     # first time
npm run dev                    # http://localhost:5173 (proxies /api → :8000)
npm run build                  # type-check (tsc -b) + bundle
```

React chat UI for the `data_agent` (auth, sessions sidebar, streaming, activity timeline, inline
HITL approval + deliverable download). Talks to the backend only via the Vite proxy (`/api/*`);
`ChatScreen` streams `POST /data-agent/{sid}/query/stream`. Two-token model: user token
creates/lists sessions, session token is required by chat. See `/frontend` slash command and
`frontend/README.md`.

### Running on Windows

`make`/`bash` are Linux/Mac. On Windows use `.\dev.ps1` (repo root) — it starts Postgres, forces
the SelectorEventLoop (psycopg's async pool can't use the default ProactorEventLoop), and runs the
API via `run_local.py`. `uvloop` is intentionally excluded on win32.

## How to build a new agent

1. Create `src/app/agents/<name>/` with:
   - `__init__.py` — `load_system_prompt()` helper
   - `agent_<name>.py` — the agent class (compile a LangGraph graph)
   - `system.md` — prompt template. Supports `{long_term_memory}` and `{current_date_and_time}` placeholders.
   - `tools/` — optional custom tools, exported as a `tools` list
2. Add a DTO under `src/app/api/v1/dtos/` and a route under `src/app/api/v1/`.
3. Register the router in `src/app/api/v1/api.py`.
4. Add a rate-limit entry in `config.py` (`RATE_LIMIT_ENDPOINTS`) and an env var if needed.
5. Invoke via `agent.agent_invoke()` / `agent.agent_invoke_stream()`.

Use `src/app/agents/chatbot/` + `src/app/api/v1/chatbot.py` as the canonical reference.
There is a `/new-agent` slash command that scaffolds this for you.

## Non-negotiable conventions

1. **All routes have a rate-limit decorator** — `@limiter.limit(...)` using `RATE_LIMIT_ENDPOINTS`.
2. **All LLM operations are traced** by Langfuse (pass the callback handler).
3. **Async everywhere** for DB and external I/O; never block the event loop.
4. **Structured logging only** (`structlog`): event names are `lowercase_with_underscores`,
   variables passed as kwargs — **never f-strings** inside the event. Use `logger.exception()`
   for errors so tracebacks survive.
5. **Retries use `tenacity`** with exponential backoff.
6. **Console/CLI output uses `rich`.**
7. **Cache only successful responses**, never errors.
8. **All imports at the top of the file** — never inside functions or classes.
9. **DB access is async** and uses the connection pool.
10. **Type hints + Pydantic models** on every endpoint; prefer objects over raw dicts (RORO).

### Error handling style
Guard clauses first, early returns for error conditions, happy path last. `HTTPException` with a
proper status code for expected errors; global middleware for unexpected ones.

## Common pitfalls (do not do these)

- ❌ f-strings inside `structlog` events  ❌ imports inside functions
- ❌ missing rate-limit decorator on a route  ❌ missing Langfuse tracing on an LLM call
- ❌ `logger.error()` instead of `logger.exception()` for caught exceptions
- ❌ blocking I/O without `async`  ❌ hardcoded secrets/keys  ❌ missing type hints

## Planning workflow

For any non-trivial task, write a plan first to `.claude/plans/<task-name>.md` as GitHub-style
checkboxes (`- [ ] step`), with a complexity marker (✅ Simple / ⚠️ Medium / 🔴 Complex) and at
least one validation step per item. Update `- [ ]` → `- [x]` as you complete each step. The
`/plan` slash command does this.

## Notes for Claude Code

- This repo is developed with **Claude Code**, not Cursor. Project rules live in this file and
  under `.claude/`. `AGENTS.md` is a generic mirror kept for other tooling.
- Windows host: the primary shell is PowerShell; a Bash tool is also available. `make`/`uv`
  target the `.venv`.
- `schema.sql` at the root is legacy SQLite-flavored DDL — the real schema is created by SQLModel
  (`SQLModel.metadata.create_all`) and by LangGraph's checkpointer at startup. Don't rely on it.
- **Schema changes to existing tables go through Alembic** (`migrations/`, see `migrations/README`).
  `create_all` only creates missing tables (bootstrap + SQLite tests); it never ALTERs. Add a model
  column → generate a migration (`make migration m="…"`), review it (autogenerate over-reaches —
  trim to the intended change, add `server_default` for NOT NULL), then `make migrate`. When you add
  a new `table=True` model, also add it to `src/app/core/db/models_registry.py`.
