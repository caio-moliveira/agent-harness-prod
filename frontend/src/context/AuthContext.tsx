import { createContext, useContext, useState } from "react";
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
  selectAgent: (agent: Agent) => Promise<void>;
  leaveAgent: () => void;
  newSession: () => Promise<void>;
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

  function persist(next: AuthState) {
    setState(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }

  // Log in / register only establishes the user token. The chat session is created
  // later, bound to a chosen agent, so memory and history stay isolated per agent.
  async function register(email: string, password: string) {
    const user = await api.register(email, password);
    persist({ ...EMPTY, email, userToken: user.token.access_token });
  }

  async function login(email: string, password: string) {
    const token = await api.login(email, password);
    persist({ ...EMPTY, email, userToken: token.access_token });
  }

  // Bind a fresh chat session to the selected agent.
  async function selectAgent(agent: Agent) {
    if (!state.userToken || !state.email) return;
    const session = await api.createSession(state.userToken, agent.id);
    persist({
      ...state,
      sessionToken: session.token.access_token,
      sessionId: session.session_id,
      agentId: agent.id,
      agentName: agent.name,
    });
  }

  // Return to the agent picker, dropping the active session.
  function leaveAgent() {
    persist({ ...state, sessionToken: null, sessionId: null, agentId: null, agentName: null });
  }

  async function newSession() {
    if (!state.userToken || state.agentId == null) return;
    const session = await api.createSession(state.userToken, state.agentId);
    persist({ ...state, sessionToken: session.token.access_token, sessionId: session.session_id });
  }

  // Switch the active session to an existing one (token comes from the sessions list).
  function setActiveSession(sessionId: string, sessionToken: string) {
    persist({ ...state, sessionId, sessionToken });
  }

  function logout() {
    localStorage.removeItem(STORAGE_KEY);
    setState(EMPTY);
  }

  return (
    <AuthContext.Provider
      value={{
        ...state,
        isAuthenticated: Boolean(state.userToken),
        hasActiveAgent: Boolean(state.sessionToken),
        register,
        login,
        selectAgent,
        leaveAgent,
        newSession,
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
