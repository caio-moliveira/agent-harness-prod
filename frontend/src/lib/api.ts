import type {
  ChatResponse,
  ConnectDbRequest,
  ConnectDbResponse,
  GrantFolderResponse,
  Message,
  SessionResponse,
  SourceStatus,
  StreamChunk,
  StreamEvent,
  TokenResponse,
  UserResponse,
} from "./types";

const BASE = "/api/v1";

/** Turn a non-2xx response into a readable Error, unwrapping FastAPI error shapes. */
async function ensureOk(res: Response): Promise<Response> {
  if (res.ok) return res;
  let detail: string = res.statusText;
  try {
    const body = await res.json();
    if (Array.isArray(body?.errors)) {
      detail = body.errors
        .map((e: { field?: string; message?: string }) => `${e.field ?? ""}: ${e.message ?? ""}`.trim())
        .join(" · ");
    } else if (typeof body?.detail === "string") {
      detail = body.detail;
    } else if (body?.detail) {
      detail = JSON.stringify(body.detail);
    }
  } catch {
    // response had no JSON body; keep statusText
  }
  throw new Error(detail);
}

export async function register(email: string, password: string): Promise<UserResponse> {
  const res = await fetch(`${BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return (await ensureOk(res)).json();
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);
  form.set("grant_type", "password");
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  return (await ensureOk(res)).json();
}

export async function createSession(userToken: string): Promise<SessionResponse> {
  const res = await fetch(`${BASE}/auth/session`, {
    method: "POST",
    headers: { Authorization: `Bearer ${userToken}` },
  });
  return (await ensureOk(res)).json();
}

export async function listSessions(userToken: string): Promise<SessionResponse[]> {
  const res = await fetch(`${BASE}/auth/sessions`, {
    headers: { Authorization: `Bearer ${userToken}` },
  });
  return (await ensureOk(res)).json();
}

export async function renameSession(
  sessionId: string,
  sessionToken: string,
  name: string,
): Promise<SessionResponse> {
  const form = new URLSearchParams();
  form.set("name", name);
  const res = await fetch(`${BASE}/auth/session/${sessionId}/name`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Authorization: `Bearer ${sessionToken}`,
    },
    body: form.toString(),
  });
  return (await ensureOk(res)).json();
}

export async function deleteSession(sessionId: string, sessionToken: string): Promise<void> {
  const res = await fetch(`${BASE}/auth/session/${sessionId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  await ensureOk(res);
}

export async function getMessages(sessionToken: string): Promise<Message[]> {
  const res = await fetch(`${BASE}/chatbot/messages`, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  const data: ChatResponse = await (await ensureOk(res)).json();
  return data.messages ?? [];
}

export async function clearMessages(sessionToken: string): Promise<void> {
  const res = await fetch(`${BASE}/chatbot/messages`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  await ensureOk(res);
}

// --- Data Agent: connect a database / grant a folder / query the sources ---

export async function connectDb(sessionToken: string, body: ConnectDbRequest): Promise<ConnectDbResponse> {
  const res = await fetch(`${BASE}/data-agent/connect-db`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionToken}` },
    body: JSON.stringify(body),
  });
  return (await ensureOk(res)).json();
}

export async function grantFolder(sessionToken: string, path: string): Promise<GrantFolderResponse> {
  const res = await fetch(`${BASE}/data-agent/grant-folder`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionToken}` },
    body: JSON.stringify({ path }),
  });
  return (await ensureOk(res)).json();
}

export async function dataStatus(sessionToken: string): Promise<SourceStatus> {
  const res = await fetch(`${BASE}/data-agent/status`, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return (await ensureOk(res)).json();
}

export async function dataQuery(sessionToken: string, query: string): Promise<Message[]> {
  const res = await fetch(`${BASE}/data-agent/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionToken}` },
    body: JSON.stringify({ query }),
  });
  const data: ChatResponse = await (await ensureOk(res)).json();
  return data.messages ?? [];
}

export async function disconnectSources(sessionToken: string): Promise<void> {
  const res = await fetch(`${BASE}/data-agent/disconnect`, {
    method: "POST",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  await ensureOk(res);
}

/** Stream the Data Agent's work as structured events (tool calls, tokens). */
export async function* streamDataQuery(
  sessionToken: string,
  messages: { role: string; content: string }[],
): AsyncGenerator<StreamEvent, void, unknown> {
  const res = await fetch(`${BASE}/data-agent/query/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionToken}` },
    body: JSON.stringify({ messages }),
  });
  await ensureOk(res);

  const reader = res.body?.getReader();
  if (!reader) throw new Error("Streaming não suportado pelo navegador");
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const line = event.trim();
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      try {
        yield JSON.parse(payload) as StreamEvent;
      } catch {
        // ignore malformed frames
      }
    }
  }
}

/**
 * Stream an assistant reply token by token from the SSE endpoint.
 * Only the NEW user message is sent — the server keeps prior turns via the
 * LangGraph checkpointer keyed by the session.
 */
export async function* streamChat(
  sessionToken: string,
  newMessages: Message[],
): AsyncGenerator<string, void, unknown> {
  const res = await fetch(`${BASE}/chatbot/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${sessionToken}`,
    },
    body: JSON.stringify({ messages: newMessages }),
  });
  await ensureOk(res);

  const reader = res.body?.getReader();
  if (!reader) throw new Error("Streaming não suportado pelo navegador");
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const line = event.trim();
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      let chunk: StreamChunk;
      try {
        chunk = JSON.parse(payload);
      } catch {
        continue;
      }
      if (chunk.content) yield chunk.content;
      if (chunk.done) return;
    }
  }
}
