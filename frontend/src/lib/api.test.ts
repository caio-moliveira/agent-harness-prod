/**
 * Specs for the SSE parser and the API client (#74).
 *
 * The stream parser is the single point where the backend's turn contract becomes UI state, and
 * it grew a lot across #66/#72/#73 (`done{reason}`, `blocked_input`, `budget_exhausted`). It is
 * also the piece most exposed to real-world messiness: SSE frames arrive split across arbitrary
 * network chunks, so a parser that only works on whole frames breaks in production and nowhere
 * else. These specs pin both the contract and the chunk-boundary behaviour.
 */
import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
import type { StreamEvent } from "./types";

/** Build a fetch Response whose body streams the given raw chunks, byte for byte. */
function streamingResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  const body = {
    getReader() {
      return {
        read: async () =>
          i < chunks.length ? { done: false, value: encoder.encode(chunks[i++]) } : { done: true, value: undefined },
      };
    },
  };
  return { ok: true, status: 200, body } as unknown as Response;
}

/** Frame one SSE event exactly as the backend writes it. */
function frame(event: Record<string, unknown>): string {
  return `data: ${JSON.stringify(event)}\n\n`;
}

async function collect(chunks: string[]): Promise<StreamEvent[]> {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(streamingResponse(chunks));
  const events: StreamEvent[] = [];
  for await (const ev of api.streamDataQuery("session-token", "sid", "pergunta")) {
    events.push(ev);
  }
  return events;
}

describe("streamDataQuery — turn contract", () => {
  it("yields every event type the backend can send", async () => {
    const events = await collect([
      frame({ type: "tool_start", name: "ls", input: "{}" }),
      frame({ type: "tool_end", name: "ls", output: "['/workspace/a.csv']" }),
      frame({ type: "thinking", content: "analisando" }),
      frame({ type: "token", content: "Olá" }),
      frame({ type: "todos", items: [{ content: "ler arquivos", status: "completed" }] }),
      frame({ type: "hitl_request", id: 7, action_type: "export_artifact", title: "Relatório", format: "docx" }),
      frame({ type: "done", reason: "completed" }),
    ]);

    expect(events.map((e) => e.type)).toEqual([
      "tool_start",
      "tool_end",
      "thinking",
      "token",
      "todos",
      "hitl_request",
      "done",
    ]);
  });

  it.each([
    ["completed", "completed"],
    ["call_limit", "call_limit"],
    ["timeout", "timeout"],
    ["recursion_backstop", "recursion_backstop"],
    ["blocked_input", "blocked_input"],
    ["budget_exhausted", "budget_exhausted"],
  ])("carries the %s termination reason through to the client", async (reason, expected) => {
    const events = await collect([frame({ type: "done", reason })]);
    const terminal = events.at(-1) as Extract<StreamEvent, { type: "done" }>;
    expect(terminal.type).toBe("done");
    expect(terminal.reason).toBe(expected);
  });

  it("treats a bare done (no reason) as a normal completion", async () => {
    const events = await collect([frame({ type: "done" })]);
    expect((events[0] as Extract<StreamEvent, { type: "done" }>).reason).toBeUndefined();
  });
});

describe("streamDataQuery — transport robustness", () => {
  it("reassembles a frame split across network chunks", async () => {
    // The reader hands back arbitrary byte boundaries; a frame can be cut anywhere, including
    // mid-JSON. Losing this event would silently drop a token or a terminal `done`.
    const whole = frame({ type: "token", content: "resposta completa" });
    const cut = Math.floor(whole.length / 2);
    const events = await collect([whole.slice(0, cut), whole.slice(cut)]);
    expect(events).toEqual([{ type: "token", content: "resposta completa" }]);
  });

  it("parses several frames delivered in a single chunk", async () => {
    const events = await collect([
      frame({ type: "token", content: "a" }) + frame({ type: "token", content: "b" }) + frame({ type: "done" }),
    ]);
    expect(events.map((e) => e.type)).toEqual(["token", "token", "done"]);
  });

  it("skips malformed frames without aborting the turn", async () => {
    // A corrupt frame must not take the whole stream down: the surrounding events still arrive.
    const events = await collect([
      frame({ type: "token", content: "antes" }),
      "data: {isto não é json}\n\n",
      frame({ type: "done", reason: "completed" }),
    ]);
    expect(events.map((e) => e.type)).toEqual(["token", "done"]);
  });

  it("ignores heartbeat comments and blank lines", async () => {
    // The backend sends `: heartbeat` comments to keep proxies from closing an idle stream.
    const events = await collect([": heartbeat\n\n", "\n", frame({ type: "done", reason: "completed" })]);
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("done");
  });
});

/** A minimal Response stub for the non-streaming client paths. */
function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: `status ${status}`,
    headers: new Headers(headers),
    json: async () => body,
  } as unknown as Response;
}

describe("fetchWithRetry — transient failures never reach the user", () => {
  it("retries a 429 honoring Retry-After and succeeds transparently", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(429, {}, { "Retry-After": "1" }))
      .mockResolvedValueOnce(jsonResponse(200, { total_tokens: 42, limit: 0 }));

    const promise = api.getUsage("token");
    await vi.runAllTimersAsync();
    await expect(promise).resolves.toMatchObject({ total_tokens: 42 });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });

  it("caps the wait so a long rate-limit window never stalls the UI", async () => {
    // The server may advertise a very long window; the client waits at most 5s per attempt.
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(429, {}, { "Retry-After": "3600" }))
      .mockResolvedValueOnce(jsonResponse(200, { total_tokens: 1, limit: 0 }));

    const promise = api.getUsage("token");
    await vi.advanceTimersByTimeAsync(5_000); // the cap, not the advertised hour
    await expect(promise).resolves.toMatchObject({ total_tokens: 1 });
    vi.useRealTimers();
  });

  it("retries a 5xx and recovers", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(503, {}))
      .mockResolvedValueOnce(jsonResponse(200, { total_tokens: 7, limit: 0 }));

    const promise = api.getUsage("token");
    await vi.runAllTimersAsync();
    await expect(promise).resolves.toMatchObject({ total_tokens: 7 });
    vi.useRealTimers();
  });
});

describe("error surfacing", () => {
  it("explains a persistent rate limit in human terms, not a status code", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(429, {}, { "Retry-After": "3" }));

    const promise = api.getUsage("token");
    const assertion = expect(promise).rejects.toThrow(/Muitas requisições — aguarde 3s/);
    await vi.runAllTimersAsync();
    await assertion;
    vi.useRealTimers();
  });

  it("unwraps FastAPI's detail field", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(403, { detail: "Artefato pertence a outro usuário." }),
    );

    await expect(api.getUsage("token")).rejects.toThrow("Artefato pertence a outro usuário.");
  });

  it("unwraps FastAPI's validation error array", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(422, { errors: [{ field: "query", message: "Field required" }] }),
    );

    await expect(api.getUsage("token")).rejects.toThrow(/query: Field required/);
  });
});
