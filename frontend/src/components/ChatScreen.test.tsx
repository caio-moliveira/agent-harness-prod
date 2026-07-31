/**
 * Specs for how ChatScreen renders the backend's turn contract (#74).
 *
 * The backend grew a taxonomy of endings across #66/#72/#73, and each one means something
 * different to a user: a capped turn is resumable, a refused input is not, and only a real failure
 * deserves an error bubble. Getting that mapping wrong is invisible to `tsc` and to the backend
 * suite — it shows up as a user being told to "continuar" on a message that was refused. These
 * specs pin the mapping.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StreamEvent } from "../lib/types";

const auth = {
  agentName: "Data Agent",
  agentId: 1,
  sessionToken: "session-token",
  sessionId: "sid-1",
  userToken: "user-token",
  leaveAgent: vi.fn(),
  logout: vi.fn(),
  startSession: vi.fn(),
  clearSession: vi.fn(),
  setActiveSession: vi.fn(),
};

vi.mock("../context/AuthContext", () => ({ useAuth: () => auth }));

// The API surface ChatScreen touches. Each spec re-scripts `streamDataQuery` for its scenario.
const streamEvents = vi.fn<() => StreamEvent[]>(() => []);
/** What each turn actually asked the backend for — the query and whether it opted into resumption. */
const streamCalls: Array<{ query: string; autoContinue: boolean }> = [];
vi.mock("../lib/api", () => ({
  streamDataQuery: async function* (
    _sessionToken: string,
    _sessionId: string,
    query: string,
    _signal?: AbortSignal,
    autoContinue = false,
  ) {
    streamCalls.push({ query, autoContinue });
    for (const ev of streamEvents()) yield ev;
  },
  getHistory: async () => [],
  dataStatus: async () => ({ db_connected: false }),
  listSessions: async () => [],
  listSessionEvents: async () => [],
  getUsage: async () => ({
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    turns: 0,
    limit: 0,
    remaining: 0,
    exceeded: false,
    resets_at: new Date().toISOString(),
  }),
  listPendingActions: async () => [],
  listSessionFiles: async () => ({ files: [] }),
}));

const { default: ChatScreen } = await import("./ChatScreen");

/** Type a message and submit it, returning once the turn has been streamed. */
async function sendMessage(text = "analise a pasta") {
  const user = userEvent.setup();
  const input = await screen.findByRole("textbox");
  await user.type(input, text);
  await user.keyboard("{Enter}");
}

beforeEach(() => {
  streamEvents.mockReturnValue([]);
  streamCalls.length = 0;
  localStorage.clear(); // the auto-continue preference is persisted; specs must not inherit it
});

describe("turn endings", () => {
  it("offers Continuar when the turn stopped at the model-call cap", async () => {
    streamEvents.mockReturnValue([
      { type: "token", content: "Comecei a análise" },
      { type: "done", reason: "call_limit" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    expect(await screen.findByText(/limite de passos/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /continuar/i })).toBeInTheDocument();
  });

  it("explains a timeout as a time limit, not a step limit", async () => {
    streamEvents.mockReturnValue([
      { type: "token", content: "parcial" },
      { type: "done", reason: "timeout" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    expect(await screen.findByText(/tempo máximo/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /continuar/i })).toBeInTheDocument();
  });

  it("shows no notice at all on a normal completion", async () => {
    streamEvents.mockReturnValue([
      { type: "token", content: "Pronto, aqui está o resumo." },
      { type: "done", reason: "completed" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    expect(await screen.findByText(/aqui está o resumo/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /continuar/i })).not.toBeInTheDocument();
  });

  it("does NOT offer Continuar when the input was refused by a guardrail", async () => {
    // Repeating a refused message changes nothing — the refusal IS the answer. Offering to
    // continue would invite the user into a loop the backend will refuse again.
    streamEvents.mockReturnValue([
      { type: "token", content: "Não consigo processar esta solicitação." },
      { type: "done", reason: "blocked_input" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    expect(await screen.findByText(/não consigo processar/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /continuar/i })).not.toBeInTheDocument();
  });

  it("does NOT offer Continuar when the daily token budget is exhausted", async () => {
    // Same reasoning: retrying now cannot succeed — the budget resets on a clock, not on a click.
    streamEvents.mockReturnValue([
      { type: "token", content: "Você atingiu o limite diário de 1.000 tokens desta conta." },
      { type: "done", reason: "budget_exhausted" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    expect(await screen.findByText(/limite diário/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /continuar/i })).not.toBeInTheDocument();
  });

  it("renders an error bubble only for a real failure", async () => {
    streamEvents.mockReturnValue([{ type: "error", content: "Erro ao processar a consulta." }]);
    render(<ChatScreen />);
    await sendMessage();

    expect(await screen.findByText(/erro ao processar/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /continuar/i })).not.toBeInTheDocument();
  });
});

/**
 * Unattended resumption (#77). The manual button stays the default, so the specs here pin the two
 * things that make the opt-in trustworthy: the request actually carries the flag the user toggled
 * (a stale-closure bug here would look like the toggle silently doing nothing), and an automatic
 * resumption is disclosed rather than hidden.
 */
describe("automatic resumption", () => {
  it("does not opt in by default", async () => {
    streamEvents.mockReturnValue([{ type: "done", reason: "completed" }]);
    render(<ChatScreen />);
    await sendMessage();

    await waitFor(() => expect(streamCalls).toHaveLength(1));
    expect(streamCalls[0].autoContinue).toBe(false);
  });

  it("offers the opt-in only for the step cap, which is the ending the server can resume", async () => {
    streamEvents.mockReturnValue([
      { type: "token", content: "parcial" },
      { type: "done", reason: "call_limit" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    expect(await screen.findByRole("checkbox", { name: /continuar automaticamente/i })).toBeInTheDocument();
  });

  it("does NOT offer the opt-in on a timeout — the server will not auto-resume one", async () => {
    streamEvents.mockReturnValue([
      { type: "token", content: "parcial" },
      { type: "done", reason: "timeout" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    expect(await screen.findByText(/tempo máximo/i)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /continuar automaticamente/i })).not.toBeInTheDocument();
  });

  it("resumes immediately with the flag when the user opts in", async () => {
    streamEvents.mockReturnValue([
      { type: "token", content: "parcial" },
      { type: "done", reason: "call_limit" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: /continuar automaticamente/i }));

    // The resumption must carry the flag the user just set — reading it from state instead would
    // send the pre-toggle value and quietly resume without auto-continue.
    await waitFor(() => expect(streamCalls).toHaveLength(2));
    expect(streamCalls[1]).toEqual({ query: "continuar", autoContinue: true });
  });

  it("remembers the preference for the next message", async () => {
    streamEvents.mockReturnValue([
      { type: "token", content: "parcial" },
      { type: "done", reason: "call_limit" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: /continuar automaticamente/i }));
    await waitFor(() => expect(streamCalls).toHaveLength(2));

    await sendMessage("e agora o relatório");
    await waitFor(() => expect(streamCalls).toHaveLength(3));
    expect(streamCalls[2].autoContinue).toBe(true);
  });

  it("discloses an automatic resumption and ends without asking the user for anything", async () => {
    streamEvents.mockReturnValue([
      { type: "token", content: "parte 1 " },
      { type: "auto_continue", attempt: 1, max: 2 },
      { type: "token", content: "parte 2" },
      { type: "done", reason: "completed" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    expect(await screen.findByText(/retomado automaticamente uma vez/i)).toBeInTheDocument();
    // It finished, so there is nothing for the user to act on.
    expect(screen.queryByRole("button", { name: /continuar/i })).not.toBeInTheDocument();
  });

  it("still hands back to the manual button when resumptions run out", async () => {
    streamEvents.mockReturnValue([
      { type: "token", content: "parte 1 " },
      { type: "auto_continue", attempt: 1, max: 2 },
      { type: "token", content: "parte 2 " },
      { type: "auto_continue", attempt: 2, max: 2 },
      { type: "token", content: "parte 3" },
      { type: "done", reason: "call_limit" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    expect(await screen.findByText(/retomado automaticamente 2 vezes/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^continuar$/i })).toBeInTheDocument();
  });
});

describe("streamed activity", () => {
  it("renders tool activity and the answer text together", async () => {
    streamEvents.mockReturnValue([
      { type: "tool_start", name: "ls", input: "{'path': '/workspace'}" },
      { type: "tool_end", name: "ls", output: "['/workspace/vendas.csv']" },
      { type: "token", content: "A pasta tem vendas.csv." },
      { type: "done", reason: "completed" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    expect(await screen.findByText(/vendas\.csv/i)).toBeInTheDocument();
  });

  it("surfaces an approval card when the agent parks an artifact", async () => {
    streamEvents.mockReturnValue([
      { type: "token", content: "Preparei o relatório." },
      { type: "hitl_request", id: 42, action_type: "export_artifact", title: "Relatório de Vendas", format: "docx" },
      { type: "done", reason: "completed" },
    ]);
    render(<ChatScreen />);
    await sendMessage();

    await waitFor(() => expect(screen.getByText(/Relatório de Vendas/i)).toBeInTheDocument());
  });
});
