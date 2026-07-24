import { lazy, Suspense } from "react";
import { useAuth } from "./context/AuthContext";
import LoginScreen from "./components/LoginScreen";
import ErrorBoundary from "./components/ErrorBoundary";

// Split out of the initial bundle: an anonymous visit (not yet authenticated) only ever needs
// LoginScreen — the agent picker and chat UI (plus markdown/SSE plumbing they pull in) load on
// demand right after login, not before.
const AgentsScreen = lazy(() => import("./components/AgentsScreen"));
const ChatScreen = lazy(() => import("./components/ChatScreen"));

export default function App() {
  const { isAuthenticated, hasActiveAgent } = useAuth();

  let screen = <LoginScreen />;
  if (isAuthenticated) {
    screen = hasActiveAgent ? <ChatScreen /> : <AgentsScreen />;
  }

  // No solid background here — the body's cyan HUD grid shows through so every screen sits on
  // the same lit canvas. The scanline is a single fixed overlay so it never re-mounts per screen.
  return (
    <div className="h-full text-slate-100">
      <div className="scanline" aria-hidden="true" />
      <ErrorBoundary>
        <Suspense fallback={<div className="grid h-full place-items-center text-sm text-slate-500">Carregando…</div>}>
          {screen}
        </Suspense>
      </ErrorBoundary>
    </div>
  );
}
