# AGENTS.md

Guidance for AI coding agents working in this repository. Read this before making changes.
Product intent and functional requirements live in `prd.md` — read it for the *why*; this file
covers the *how*.

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
│   │   ├── data_agent/        #   the product: folder+DB, tools, subagents, artifacts, skills
│   │   ├── text_to_sql/       #   reference agent (skills + tools)
│   │   ├── open_deep_research/#   reference agent (multi-subgraph)
│   │   └── tools/             #   shared tools (search, think)
│   ├── api/
│   │   ├── v1/                # versioned routes (auth, data_agent, hitl, skills, deep_research, text_to_sql)
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
│       ├── mcp/                # MCP session manager
│       ├── memory/            # mem0 long-term memory
│       ├── middleware/        # agent middleware pipeline
│       ├── sandbox/           # per-session backend (workspace/skills virtual mounts, versioning)
│       ├── skill/             # skill model, repository, approval state machine, materialization
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
make install              # uv sync --group test (the test group has pytest-asyncio — without it
                          # async tests are SILENTLY SKIPPED, not failed)
make db-up                # start ONLY Postgres (pgvector) in Docker  ← start here
make dev                  # run API on :8000 (reload), reads .env.development
uv run pytest tests/      # run tests (in-memory SQLite — no Postgres needed)
RUN_E2E=1 uv run pytest tests/e2e   # E2E: real API + Postgres + scripted mock LLM (no tokens)
make lint                 # ruff check
make format               # ruff format
make eval                 # interactive evaluation

make docker-compose-up ENV=development    # full stack (API + db + Prometheus + Grafana + cAdvisor)
```

Swagger: `http://localhost:8000/docs` · Grafana: `http://localhost:3000` (admin/admin) · Prometheus: `http://localhost:9090`

Config lives in `.env.<environment>` (development/staging/production). Copy `.env.example` to
`.env.development` and fill `JWT_SECRET_KEY`, `LANGFUSE_*`, and the LLM `MODEL` + its key (see below).
All settings are read in `src/app/core/common/config.py` — that file is the single source of truth for
config.

### Choosing an LLM model

Set **one** env var: `MODEL="provider:model"` — e.g. `anthropic:claude-sonnet-5`, `openai:gpt-4o`,
`azure_openai:<deployment>`, or `ollama:llama3.3` for open-weight local models. LangChain's
`init_chat_model` infers the provider from the prefix, so you only set that provider's API key (Azure
also needs `AZURE_OPENAI_ENDPOINT` + `_API_VERSION`; Ollama needs **no key** — just `OLLAMA_BASE_URL`,
default `http://localhost:11434`). Setting `OPENAI_BASE_URL` points `openai:<model>` at any
OpenAI-compatible server (vLLM, LM Studio, a LiteLLM proxy, OpenRouter) with the key optional — so
LiteLLM users route through its proxy with zero extra dependencies. Other `init_chat_model` providers
(`groq:`, `google_genai:`, `mistralai:`, …) also work: install their `langchain-*` package and set
their standard env key. Startup builds `MODEL` once and fails fast with a clear message if the key is
missing. `MODEL_MAX_TOKENS` and `MODEL_CALL_LIMIT` tune the output cap and the per-turn safety cap.
`UTILITY_MODEL` (blank = reuse `MODEL`) is the cheap model for low-stakes sub-flows (file descriptions,
safety check, research internals, mem0's memory-extraction LLM). **Embeddings are separate**
(`EMBEDDINGS_MODEL`) because Anthropic has no embedding model: chat/utility/deep-research work on any
provider, but long-term memory needs OpenAI, Azure, or Ollama embeddings (`ollama:nomic-embed-text`
makes memory fully local; `EMBEDDINGS_DIMS` overrides the vector size) — blank auto-resolves from a
present OpenAI/Azure key (Ollama must be explicit), else memory auto-disables
with a warning (`long_term_memory_disabled_no_embeddings`). Everything is built by
`src/app/core/llm/factory.py` (`create_chat_model` / `create_utility_chat_model`); never hardcode a
provider/model or call `ChatOpenAI`/`ChatAnthropic`/`init_chat_model` directly in an agent — go through
the factory. See `.env.example` for the full surface.

### Frontend (`frontend/`)

```bash
cd frontend && npm install     # first time
npm run dev                    # http://localhost:5173 (proxies /api → :8000)
npm run build                  # type-check (tsc -b) + bundle
```

React chat UI for the `data_agent` (auth, sessions sidebar, streaming, activity timeline, inline
HITL approval + deliverable download). Talks to the backend only via the Vite proxy (`/api/*`);
`ChatScreen` streams `POST /data-agent/{sid}/query/stream`. Two-token model: user token
creates/lists sessions, session token is required by chat. See `frontend/README.md`.

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

Use `src/app/agents/text_to_sql/` + `src/app/api/v1/text_to_sql.py` as a simple reference, or
`src/app/agents/data_agent/` for the full deep-agent pattern (tools + subagents + skills +
artifacts + HITL).

## Skills (deepagents `SkillsMiddleware`)

Skills load through the per-session backend by **virtual mount**, never by raw host path —
`SkillsMiddleware` has no direct filesystem access (`src/app/core/sandbox/backend.py`):
`SKILLS_MOUNT` (`/skills/`) serves the bundled skills shipped with `data_agent`; `USER_SKILLS_MOUNT`
(`/skills/user/`) serves the caller's approved library, materialized from Postgres to a temp dir per
agent build (`src/app/core/skill/materialize.py`). **A skill directory not routed through one of
these mounts silently never loads** — it falls through to the ephemeral `StateBackend`, which has
never heard of it. If you add a new skill source, give it its own mount; don't hand
`create_deep_agent(skills=[...])` a bare filesystem path.

User-authored skills are gated by an approval state machine (`draft → in_review → approved`,
`src/app/core/skill/skill_status.py`) — only `approved` skills materialize. Editing an approved
skill returns it to `draft`.

## SLOs, alertas e runbooks

`observability/` é provisionado pelo compose: Prometheus carrega `prometheus/alerts.yml` (regras de
SLO) e o Grafana provisiona os dashboards de `grafana/dashboards/json/` — subir `make
docker-compose-up ENV=development` já traz alertas (`http://localhost:9090/alerts`) e o dashboard
**Agent Health & SLOs** funcionando, sem clique manual.

SLOs atuais: taxa de `reason="error"` < 2% dos turnos · p95 de turno concluído < 120s ·
`recursion_backstop` **sempre zero** (o invariante da política de limites) · 5xx HTTP < 5%.

Cada alerta carrega uma anotação `runbook` que aponta para a seção correspondente em
`docs/runbooks.md` — **alerta sem runbook é pager que ninguém sabe responder**.
`tests/unit/test_alert_rules.py` trava as duas pontas: toda regra precisa de severidade, descrição
e âncora de runbook existente, e **toda série consultada precisa ser uma série que o app expõe de
fato** (o `prometheus_client` sufixa counters com `_total`, então `rate(llm_errors[5m])` casaria
com nada e o alerta nunca dispararia — esse bug foi pego exatamente assim). Métrica nova relevante
nasce com painel e, quando fizer sentido, regra de alerta.

## Golden evals (quality regression gate)

`evals/` holds the **golden-eval harness** (issue #70): a versioned dataset
(`evals/golden_set.json`, PT-BR cases over the fixture workspace in `evals/workspace/`) scored by
**deterministic rubrics** — termination reason, tools used, answer content, artifact
generation/provenance, duration. `evals/config.yaml` sets the thresholds; below them the runner
exits 1 (the gate).

- `make eval-golden` — mock-LLM mode (zero tokens; `smoke` cases): validates the eval machinery +
  provider plumbing. Runs in CI on any PR touching `evals/` or `tests/e2e/`.
- `make eval-golden-live` — the real `MODEL` from the env (full dataset): the number that answers
  "did quality regress?". Runs nightly + on demand via `workflow_dispatch`
  (`.github/workflows/evals.yaml`; needs a provider secret configured in the repo).

The runner reuses the E2E stack launcher (`tests/e2e/harness.py`). When you add a product
capability, add a golden case for it — a failing eval drives the next task, not a bug report.
This is complementary to `src/evals` (`make eval`), the Langfuse **trace** evaluator that scores
production traffic after the fact.

## Turn limits (data_agent)

Three independent layers bound one agent turn (`src/app/agents/data_agent/turn_limits.py`); the
**semantic cap is the only limit a legitimate turn should ever hit**, and every layer ends with the
same recoverable UX (partial persisted + "continuar" hint + SSE `done{reason}`), never a generic
error:

1. **`MODEL_CALL_LIMIT`** — model calls per turn via `ModelCallLimitMiddleware(exit_behavior="end")`.
   Subagents get their own cap (`cap_subagent_specs`) because they inherit the parent's recursion
   budget but no call limit of their own.
2. **Recursion backstop** — LangGraph's `recursion_limit` is **derived from the compiled graph**
   (`compute_recursion_limit`): in LangChain v1 every middleware `before_model`/`after_model` hook is
   a graph node costing one super-step per round (~8-10 with this stack), so never guess this with a
   constant — that's exactly the bug that made `GraphRecursionError` fire before the graceful cap.
3. **`TURN_TIMEOUT_SECONDS`** — wall-clock ceiling enforced at the API layer (0 disables); protects
   the stream against a hung/slow provider (e.g. a large local Ollama model).

The SSE terminal event is `done` with `reason: "completed" | "call_limit" | "timeout" |
"recursion_backstop"` (or `error` for real failures); terminations are counted in Prometheus
(`agent_turn_terminations_total{agent,reason}`) — `recursion_backstop` staying at zero is the
invariant to watch.

## Workspace memory (`AGENTS.md` in the granted folder)

The granted folder can carry its own `AGENTS.md` (the [agents.md](https://agents.md/) convention —
a *different* file from this one, living in the user's data folder, not the repo root), read once
per session and injected into the system prompt via `WorkspaceMemoryMiddleware`
(`src/app/agents/data_agent/workspace_memory.py`). Unlike skills, this needs **no new mount** —
`MemoryMiddleware.sources` are exact file paths resolved through whatever backend is already
wired in, and `/workspace/` already exists. It subclasses deepagents' stock `MemoryMiddleware`
to drop the self-editing guidance baked into its default prompt (which instructs the agent to
call `edit_file` to write learnings back) — the granted folder is read-only by default, and
"the agent learns from the user" is already handled by long-term memory + reflection. Keep this
read-only; don't restore the self-editing behavior without reconciling it with that pipeline
first.

## Database schema changes

`schema.sql` at the root is legacy SQLite-flavored DDL — the real schema is created by SQLModel
(`SQLModel.metadata.create_all`) and by LangGraph's checkpointer at startup. Don't rely on it.
**Schema changes to existing tables go through Alembic** (`migrations/`, see `migrations/README`).
`create_all` only creates missing tables (bootstrap + SQLite tests); it never ALTERs. Add a model
column → generate a migration (`make migration m="…"`), review it (autogenerate over-reaches —
trim to the intended change, add `server_default` for NOT NULL), then `make migrate`. When you add
a new `table=True` model, also add it to `src/app/core/db/models_registry.py`.

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
- ❌ passing a raw host path into `create_deep_agent(skills=[...])` — mount it first (see Skills above)
