import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { Agent } from "../lib/types";
import * as api from "../lib/api";

interface AuthState {
  email: string | null;
  userToken: string | null;
  sessionToken: string | null;
  sessionId: string | null;
  agentId: number | null;
  agentName: string | null;
}

interface AuthContextValue extends AuthState {
  isAuthenticated: boolean;
  hasActiveAgent: boolean;
  register: (email: string, password: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  selectAgent: (agent: Agent) => void;
  leaveAgent: () => void;
  startSession: () => Promise<{ sessionId: string; sessionToken: string } | null>;
  clearSession: () => void;
  setActiveSession: (sessionId: string, sessionToken: string) => void;
  logout: () => void;
}

const STORAGE_KEY = "agent_harness_auth";
const EMPTY: AuthState = {
  email: null,
  userToken: null,
  sessionToken: null,
  sessionId: null,
  agentId: null,
  agentName: null,
};

const AuthContext = createContext<AuthContextValue | null>(null);

/** Reflect the active conversation in the browser URL so it's visible and traceable. */
function syncSessionUrl(sessionId: string | null) {
  const path = sessionId ? `/c/${sessionId}` : "/";
  if (window.location.pathname !== path) {
    window.history.replaceState(null, "", path);
  }
}

function loadState(): AuthState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...EMPTY, ...JSON.parse(raw) };
  } catch {
    // corrupt storage; fall through to empty
  }
  return EMPTY;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(loadState);

  // Keep the URL in sync with the restored session on first load.
  useEffect(() => {
    syncSessionUrl(state.sessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function persist(next: AuthState) {
    setState(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    syncSessionUrl(next.sessionId);
  }

  // Log in / register only establishes the user token. The chat session is created
  // lazily — on the first message — so empty conversations never pile up.
  async function register(email: string, password: string) {
    const user = await api.register(email, password);
    persist({ ...EMPTY, email, userToken: user.token.access_token });
  }

  async function login(email: string, password: string) {
    const token = await api.login(email, password);
    persist({ ...EMPTY, email, userToken: token.access_token });
  }

  // Enter an agent's chat WITHOUT creating a session yet — the first message starts one.
  function selectAgent(agent: Agent) {
    if (!state.userToken || !state.email) return;
    persist({ ...state, agentId: agent.id, agentName: agent.name, sessionToken: null, sessionId: null });
  }

  // Return to the agent picker, dropping the agent + any active session.
  function leaveAgent() {
    persist({ ...state, sessionToken: null, sessionId: null, agentId: null, agentName: null });
  }

  // Create the session on demand (first message / first source), bound to the current agent.
  async function startSession(): Promise<{ sessionId: string; sessionToken: string } | null> {
    if (!state.userToken || state.agentId == null) return null;
    const session = await api.createSession(state.userToken, state.agentId);
    const sessionToken = session.token.access_token;
    persist({ ...state, sessionToken, sessionId: session.session_id });
    return { sessionId: session.session_id, sessionToken };
  }

  // Start a fresh conversation: drop the active session (a new one is created on the next message).
  function clearSession() {
    persist({ ...state, sessionToken: null, sessionId: null });
  }

  // Switch the active session to an existing one (token comes from the sessions list).
  function setActiveSession(sessionId: string, sessionToken: string) {
    persist({ ...state, sessionId, sessionToken });
  }

  function logout() {
    localStorage.removeItem(STORAGE_KEY);
    setState(EMPTY);
    syncSessionUrl(null);
  }

  return (
    <AuthContext.Provider
      value={{
        ...state,
        isAuthenticated: Boolean(state.userToken),
        hasActiveAgent: Boolean(state.agentId),
        register,
        login,
        selectAgent,
        leaveAgent,
        startSession,
        clearSession,
        setActiveSession,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth deve ser usado dentro de <AuthProvider>");
  return ctx;
}
