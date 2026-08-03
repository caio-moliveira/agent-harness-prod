/**
 * Specs for the two-token auth model (#74).
 *
 * The product uses two distinct tokens — a USER token that creates and lists sessions, and a
 * SESSION token that the chat endpoints require. Mixing them up produces confusing 422s at
 * runtime (it happened while writing the E2E suite), so the boundary deserves explicit coverage.
 * Also pinned: sessions are created lazily on the first message, so browsing never leaves a trail
 * of empty conversations.
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const login = vi.fn();
const createSession = vi.fn();
vi.mock("../lib/api", () => ({
  login: (...args: unknown[]) => login(...args),
  register: vi.fn(),
  createSession: (...args: unknown[]) => createSession(...args),
}));

const { AuthProvider, useAuth } = await import("./AuthContext");

/** A probe component that exposes the context and lets a spec drive it. */
function Probe() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="user-token">{auth.userToken ?? "-"}</span>
      <span data-testid="session-token">{auth.sessionToken ?? "-"}</span>
      <span data-testid="session-id">{auth.sessionId ?? "-"}</span>
      <span data-testid="authenticated">{String(auth.isAuthenticated)}</span>
      <button onClick={() => void auth.login("user@test.com", "senha")}>entrar</button>
      <button onClick={() => void auth.startSession()}>iniciar sessão</button>
      <button onClick={() => auth.selectAgent({ id: 3, name: "Data Agent" } as never)}>escolher agente</button>
      <button onClick={() => auth.clearSession()}>nova conversa</button>
      <button onClick={() => auth.logout()}>sair</button>
    </div>
  );
}

function renderAuth() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
}

beforeEach(() => {
  login.mockResolvedValue({ access_token: "USER-TOKEN", token_type: "bearer" });
  createSession.mockResolvedValue({
    session_id: "sid-99",
    token: { access_token: "SESSION-TOKEN", token_type: "bearer" },
  });
});

describe("two-token model", () => {
  it("login establishes only the user token — no session yet", async () => {
    const user = userEvent.setup();
    renderAuth();
    await user.click(screen.getByText("entrar"));

    expect(screen.getByTestId("user-token")).toHaveTextContent("USER-TOKEN");
    expect(screen.getByTestId("session-token")).toHaveTextContent("-");
    expect(screen.getByTestId("authenticated")).toHaveTextContent("true");
    // Browsing must not create conversations: the session is created on the first message.
    expect(createSession).not.toHaveBeenCalled();
  });

  it("startSession creates the session with the USER token and stores the SESSION token", async () => {
    const user = userEvent.setup();
    renderAuth();
    await user.click(screen.getByText("entrar"));
    await user.click(screen.getByText("escolher agente"));
    await user.click(screen.getByText("iniciar sessão"));

    // The session is created with the user token — passing the session token here is the classic
    // mix-up and would 422 at runtime.
    expect(createSession).toHaveBeenCalledWith("USER-TOKEN", 3);
    expect(screen.getByTestId("session-token")).toHaveTextContent("SESSION-TOKEN");
    expect(screen.getByTestId("session-id")).toHaveTextContent("sid-99");
  });

  it("does not create a session before an agent is chosen", async () => {
    const user = userEvent.setup();
    renderAuth();
    await user.click(screen.getByText("entrar"));
    await user.click(screen.getByText("iniciar sessão"));

    expect(createSession).not.toHaveBeenCalled();
  });

  it("a new conversation drops the session but keeps the user logged in", async () => {
    const user = userEvent.setup();
    renderAuth();
    await user.click(screen.getByText("entrar"));
    await user.click(screen.getByText("escolher agente"));
    await user.click(screen.getByText("iniciar sessão"));
    await user.click(screen.getByText("nova conversa"));

    expect(screen.getByTestId("session-token")).toHaveTextContent("-");
    expect(screen.getByTestId("user-token")).toHaveTextContent("USER-TOKEN");
  });
});

describe("persistence", () => {
  it("restores the session across a reload", async () => {
    const user = userEvent.setup();
    const first = renderAuth();
    await user.click(screen.getByText("entrar"));
    await user.click(screen.getByText("escolher agente"));
    await user.click(screen.getByText("iniciar sessão"));
    first.unmount();

    renderAuth(); // simulates a page reload reading localStorage
    expect(screen.getByTestId("session-id")).toHaveTextContent("sid-99");
    expect(screen.getByTestId("user-token")).toHaveTextContent("USER-TOKEN");
  });

  it("logout clears every token from storage", async () => {
    const user = userEvent.setup();
    renderAuth();
    await user.click(screen.getByText("entrar"));
    await user.click(screen.getByText("sair"));

    expect(screen.getByTestId("user-token")).toHaveTextContent("-");
    expect(localStorage.getItem("agent_harness_auth")).toBeNull();
  });

  it("survives corrupt storage instead of crashing the app", async () => {
    localStorage.setItem("agent_harness_auth", "{not json");
    await act(async () => {
      renderAuth();
    });
    expect(screen.getByTestId("authenticated")).toHaveTextContent("false");
  });
});
