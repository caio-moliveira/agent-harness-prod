import { createContext, useContext, useState } from "react";
import type { ReactNode } from "react";
import * as api from "../lib/api";

interface AuthState {
  email: string | null;
  userToken: string | null;
  sessionToken: string | null;
  sessionId: string | null;
}

interface AuthContextValue extends AuthState {
  isAuthenticated: boolean;
  register: (email: string, password: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  newSession: () => Promise<void>;
  setActiveSession: (sessionId: string, sessionToken: string) => void;
  logout: () => void;
}

const STORAGE_KEY = "agent_harness_auth";
const EMPTY: AuthState = { email: null, userToken: null, sessionToken: null, sessionId: null };

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

  // Exchange a user token for a fresh chat session (the session token is what
  // the chat endpoints require).
  async function establishSession(email: string, userToken: string) {
    const session = await api.createSession(userToken);
    persist({
      email,
      userToken,
      sessionToken: session.token.access_token,
      sessionId: session.session_id,
    });
  }

  async function register(email: string, password: string) {
    const user = await api.register(email, password);
    await establishSession(email, user.token.access_token);
  }

  async function login(email: string, password: string) {
    const token = await api.login(email, password);
    await establishSession(email, token.access_token);
  }

  async function newSession() {
    if (!state.userToken || !state.email) return;
    await establishSession(state.email, state.userToken);
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
        isAuthenticated: Boolean(state.sessionToken),
        register,
        login,
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
