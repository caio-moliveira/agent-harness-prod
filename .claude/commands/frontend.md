---
description: Work on the React chat frontend in frontend/ — run, build, or extend it
argument-hint: dev | build | sessions | sources | skills | add-agent-ui
---

The frontend lives in `frontend/` (Vite + React 19 + TypeScript + Tailwind v4). It is a chat UI
for `data_agent`, with auth, an agent picker, a sessions sidebar, streaming, HITL approval, and
deliverable downloads. Action: $1 (default: dev)

## Ground rules (match the existing code)
- The Vite dev server proxies `/api/*` → `http://localhost:8000` (`vite.config.ts`). Always call
  the backend via relative `/api/v1/...` paths — never hardcode `http://localhost:8000`.
- Runs on **port 5173** (`strictPort: true`) because port 3000 is used by another local app.
- **Two-token model:** register/login → *user token* (lists/creates agents and sessions);
  `POST /auth/session?agent_id=...` → *session token* (required by chat + rename/delete). See
  `src/context/AuthContext.tsx`. Never send the user token to a chat endpoint. Sessions are
  scoped to one of the user's agent configs — there's no session without a selected agent.
- History is server-side (LangGraph checkpointer): send only the NEW user message per turn;
  load prior turns via `GET /data-agent/{session_id}/messages`.
- All API calls go through `src/lib/api.ts` with types in `src/lib/types.ts` mirroring the
  backend Pydantic DTOs. Add new endpoints there, typed.
- Keep it type-clean: `npm run build` runs `tsc -b` with `noUnusedLocals`/`noUnusedParameters`.

## Actions
- **dev**   → ensure the backend is up (`.\dev.ps1` in repo root), then `cd frontend && npm run dev`.
- **build** → `cd frontend && npm run build` (type-check + bundle). Fix any TS errors it reports.
- **sessions** → `ConversationsSidebar.tsx` lists/creates/renames/deletes sessions via
  `/auth/sessions`, `/auth/session`, `/auth/session/{id}/name`, `/auth/session/{id}`.
- **sources** → `SourcesPanel.tsx` connects/manages a session's data sources via
  `POST /data-agent/connect-db`, `POST /data-agent/grant-folder`, `GET /data-agent/status`,
  `POST /data-agent/disconnect`.
- **skills** → `SkillsPanel.tsx` (in `AgentsScreen.tsx`) browses the skill library and attaches
  skills to an agent via the `/skills` and `/agents` endpoints.
- **add-agent-ui** → there is currently no UI for `text_to_sql` or `open_deep_research` (only
  `data_agent` has a frontend surface). Adding one means a new screen component, typed client
  functions in `api.ts`, and a switch in `App.tsx` — reuse the token/session flow and streaming
  pattern from `ChatScreen.tsx`, but note those two agents don't share `data_agent`'s
  session-per-agent-config model, so the auth flow will need adapting, not just copying.

## Validate
- `npm run build` passes clean.
- With backend up: open http://localhost:5173, register, create an agent, send a message, get a
  streamed reply.
