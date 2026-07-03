---
description: Work on the React chat frontend in frontend/ — run, build, or extend it
argument-hint: dev | build | add-agent-tab | sessions
---

The frontend lives in `frontend/` (Vite + React 19 + TypeScript + Tailwind v4). It is a chat UI
for the `chatbot` agent with auth, a sessions sidebar, and streaming. Action: $1 (default: dev)

## Ground rules (match the existing code)
- The Vite dev server proxies `/api/*` → `http://localhost:8000` (`vite.config.ts`). Always call
  the backend via relative `/api/v1/...` paths — never hardcode `http://localhost:8000`.
- Runs on **port 5173** (`strictPort: true`) because port 3000 is used by another local app.
- **Two-token model:** register/login → *user token* (lists/creates sessions);
  `POST /auth/session` → *session token* (required by chat + rename/delete). See
  `src/context/AuthContext.tsx`. Never send the user token to a chat endpoint.
- History is server-side (LangGraph checkpointer): send only the NEW user message per turn;
  load prior turns via `GET /chatbot/messages`.
- All API calls go through `src/lib/api.ts` with types in `src/lib/types.ts` mirroring the
  backend Pydantic DTOs. Add new endpoints there, typed.
- Keep it type-clean: `npm run build` runs `tsc -b` with `noUnusedLocals`/`noUnusedParameters`.

## Actions
- **dev**   → ensure the backend is up (`.\dev.ps1` in repo root), then `cd frontend && npm run dev`.
- **build** → `cd frontend && npm run build` (type-check + bundle). Fix any TS errors it reports.
- **add-agent-tab** → add a view for another agent (`/text-to-sql`, `/deep-research`): add typed
  client fns in `api.ts`, a screen component, and switch between agents in `App.tsx`. Reuse the
  session/token flow and the streaming pattern from `chatbot`.
- **sessions** → the sidebar (`src/components/Sidebar.tsx`) lists/creates/renames/deletes sessions
  via `/auth/sessions`, `/auth/session`, `/auth/session/{id}/name`, `/auth/session/{id}`.

## Validate
- `npm run build` passes clean.
- With backend up: open http://localhost:5173, register, send a message, get a streamed reply.
