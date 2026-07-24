# Agent Harness — Frontend (React + Vite + TS + Tailwind)

A chat UI for the `data_agent`: register/login, create or pick one of your configured agents,
then chat with streaming responses, an activity timeline, inline human-in-the-loop (HITL)
approval, and deliverable downloads.

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

- **Two tokens.** Register/login (`POST /auth/register` / `POST /auth/login`) returns a *user
  token*, used to list/create agents (`GET /agents`, `POST /agents`) and sessions
  (`GET /auth/sessions`). A *session token* (`POST /auth/session?agent_id=...`) is required by
  the chat endpoints. Both are kept in `localStorage` (`agent_harness_auth`) — see
  `src/context/AuthContext.tsx`.
- **Agent-scoped sessions.** After login, `AgentsScreen` lists the user's own agent
  configurations (name, system prompt, folder, and `web_search`/`sql`/`memory` toggles) and lets
  them attach skills. Picking or creating one enters `ChatScreen`; the session itself is created
  lazily on the first message, not on entry.
- **Streaming.** `POST /data-agent/{session_id}/query/stream` returns Server-Sent Events;
  `src/lib/api.ts` parses them with a `fetch` + `ReadableStream` reader and yields structured
  events (text, tool activity, plan/HITL prompts) as they arrive.
- **History lives on the server.** LangGraph checkpoints each session, so the UI sends only the
  new user message per turn and loads prior turns via `GET /data-agent/{session_id}/messages`.
- **HITL + deliverables.** Plan approval and other pending actions surface inline
  (`ArtifactApproval.tsx`); generated files (docx/pptx/xlsx) are listed and downloadable via
  `DeliverableLinks.tsx` from `GET /data-agent/{session_id}/artifacts/{action_id}/download`.

## Structure

```
src/
  main.tsx                     # entry, wraps <App> in <AuthProvider>
  App.tsx                      # switches LoginScreen / AgentsScreen / ChatScreen on auth state
  context/AuthContext.tsx      # tokens, register/login/logout, agent selection, lazy session start
  lib/
    api.ts                     # typed API client + SSE streaming
    types.ts                   # TS types mirroring the backend DTOs
    toolLabels.ts              # tool-name → icon/label lookup for the activity views
  components/
    LoginScreen.tsx            # register / login form
    AgentsScreen.tsx           # list/create the user's agent configs, attach skills
    ChatScreen.tsx             # message list + composer + all chat-turn panels
    ConversationsSidebar.tsx   # session list: switch/rename/delete
    Composer.tsx               # input box (Enter to send, Shift+Enter newline)
    MessageBubble.tsx          # one message (typing indicator while streaming)
    Markdown.tsx                # markdown renderer for assistant replies
    ActivityTimeline.tsx       # sidebar log of tool activity across the session
    AgentActivity.tsx          # inline per-turn tool activity (expand/collapse)
    ThinkingPanel.tsx          # model reasoning/thinking display
    TodoList.tsx               # renders the agent's plan as a checklist
    ArtifactApproval.tsx       # inline HITL approval prompts
    DeliverableLinks.tsx       # generated-file download links
    SourcesPanel.tsx           # connected data sources (DB / folder) management
    SkillsPanel.tsx            # skill library browsing/attachment
    icons.tsx                  # shared icon set
```

## Build

```bash
npm run build     # type-checks (tsc -b) and bundles to dist/
npm run preview   # serve the production build locally
```

## Scope

This UI is built specifically for `data_agent`. `text_to_sql` and `open_deep_research` don't
have a frontend surface yet — exercise them via `src/cli/` or Swagger (`/docs`).
