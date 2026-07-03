# Agent Harness — Frontend (React + Vite + TS + Tailwind)

A minimal but real chat UI for the harness's `chatbot` agent: register/login, automatic
session creation, and a streaming chat with server-kept history.

## Prerequisites

- Node 18+ (tested on Node 24)
- The **backend running** on `http://localhost:8000` (`.\dev.ps1` in the repo root)

## Run

```bash
cd frontend
npm install        # first time only
npm run dev        # serves on http://localhost:5173
```

Open **http://localhost:5173**. The Vite dev server proxies every `/api/*` call to the
backend on `:8000` (see `vite.config.ts`), so there are no CORS issues and no tokens in URLs.

> Port note: the UI runs on **5173** (Vite default) because port 3000 is often taken by other
> local apps. Change `server.port` in `vite.config.ts` if needed.

## How it works

- **Two tokens.** Register/login returns a *user token*; the app immediately calls
  `POST /auth/session` to get a *session token*. The chat endpoints require the session token.
  Both are kept in `localStorage` (`agent_harness_auth`).
- **Streaming.** `POST /chatbot/chat/stream` returns Server-Sent Events; `src/lib/api.ts`
  parses them with a `fetch` + `ReadableStream` reader and yields tokens as they arrive.
- **History lives on the server.** LangGraph checkpoints each session, so the UI sends only the
  new user message per turn and loads prior turns via `GET /chatbot/messages`.

## Structure

```
src/
  main.tsx              # entry, wraps <App> in <AuthProvider>
  App.tsx               # routes between LoginScreen and ChatScreen
  context/AuthContext.tsx  # tokens, register/login/logout, session creation
  lib/api.ts            # typed API client + SSE streaming
  lib/types.ts          # TS types mirroring the backend DTOs
  components/
    LoginScreen.tsx     # register / login form
    ChatScreen.tsx      # message list + send + clear + new session
    MessageBubble.tsx   # one message (typing indicator while streaming)
    Composer.tsx        # input box (Enter to send, Shift+Enter newline)
```

## Build

```bash
npm run build     # type-checks (tsc -b) and bundles to dist/
npm run preview   # serve the production build locally
```

## Next ideas

- Session sidebar (list via `GET /auth/sessions`, switch/rename/delete).
- Tabs for the other agents (`/text-to-sql`, `/deep-research`).
- Token-expiry handling + refresh, and markdown rendering of assistant replies.
