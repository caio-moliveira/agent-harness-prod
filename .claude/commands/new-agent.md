---
description: Scaffold a new LangGraph agent under src/app/agents/ with its route, DTO and rate limit
argument-hint: <agent_name> [one-line description]
---

Scaffold a brand-new agent named `$1` following this repo's conventions. Description: $2

Mirror `text_to_sql` (`src/app/agents/text_to_sql/` + `src/app/api/v1/text_to_sql.py`) — it's the
smallest complete example of the pattern in CLAUDE.md's "How to build a new agent" section. For
a fuller pattern (custom tools, subagents, artifacts, HITL), mirror `data_agent` instead.

1. Create `src/app/agents/$1/` with:
   - `__init__.py` — a factory exposing `get_$1_agent()` returning a singleton instance
     (copy `text_to_sql/__init__.py`).
   - `agent_$1.py` — the agent class: build/compile a LangGraph graph, wire the shared
     checkpointer, expose `async def agent_invoke(...)` (add `agent_invoke_stream()` too if the
     route needs to stream).
   - `system.md` — prompt template, if the agent takes one; supports the `{long_term_memory}`
     and `{current_date_and_time}` placeholders.
   - `tools/` — optional, only if `$1` needs custom tools beyond the shared ones in
     `src/app/agents/tools/`.
2. Add a Pydantic DTO under `src/app/api/v1/dtos/$1.py` (request + response models) — mirror
   `dtos/text_to_sql.py`.
3. Add a route module `src/app/api/v1/$1.py` with:
   - a rate-limit decorator: `@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["$1"][0])`
   - JWT auth via the `get_current_session` dependency (`src/app/api/v1/auth.py`)
   - structured logging (`logger.info("...", ...kwargs)`, lowercase_underscore events)
4. Register the router in `src/app/api/v1/api.py`
   (`api_router.include_router($1_router, prefix="/$1", tags=["$1"])`).
5. Add `"$1": ["15 per minute"]` to the `RATE_LIMIT_ENDPOINTS` default dict in
   `src/app/core/common/config.py`.

Before writing code, read `src/app/agents/text_to_sql/text_sql_agent.py` and
`src/app/api/v1/text_to_sql.py` to mirror imports, error handling (guard clauses + early
returns), and logging exactly. Then confirm it imports cleanly with
`uv run python -c "import src.app.api.v1.$1"`.

Note: there's no frontend UI for a new agent type by default — `frontend/` is built
specifically for `data_agent`. See `/frontend` if a UI surface is also wanted.
