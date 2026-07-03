---
description: Scaffold a new LangGraph agent under src/app/agents/ with its route, DTO and rate limit
argument-hint: <agent_name> [one-line description]
---

Scaffold a brand-new agent named `$1` following this repo's conventions. Description: $2

Do all of the following, matching the style of the existing `chatbot` reference agent
(`src/app/agents/chatbot/` + `src/app/api/v1/chatbot.py`):

1. Create `src/app/agents/$1/` with:
   - `__init__.py` exposing a `load_system_prompt()` helper (copy the chatbot pattern).
   - `agent_$1.py` — an agent class that builds and compiles a LangGraph `StateGraph`,
     wired to the shared checkpointer via `get_checkpointer()`. Expose
     `agent_invoke()` and `agent_invoke_stream()`.
   - `system.md` — a system prompt template using the `{long_term_memory}` and
     `{current_date_and_time}` placeholders.
   - `tools/__init__.py` exporting a `tools` list (empty is fine to start).
2. Add a Pydantic DTO under `src/app/api/v1/dtos/$1.py` (request + response models).
3. Add a route module `src/app/api/v1/$1.py` with:
   - a rate-limit decorator using `settings.RATE_LIMIT_ENDPOINTS["$1"]`
   - JWT auth via the `get_current_session` dependency
   - Langfuse tracing on the LLM call
   - structured logging (`logger.info("...", ...kwargs)`, lowercase_underscore events)
4. Register the router in `src/app/api/v1/api.py`.
5. Add `"$1": ["15 per minute"]` to `RATE_LIMIT_ENDPOINTS` in `src/app/core/common/config.py`.

Before writing code, read the chatbot files to mirror imports, error handling (guard clauses +
early returns), and logging exactly. Then confirm it imports cleanly with
`uv run python -c "import src.app.api.v1.$1"`.
