import { useAuth } from "./context/AuthContext";
import LoginScreen from "./components/LoginScreen";
import AgentsScreen from "./components/AgentsScreen";
import ChatScreen from "./components/ChatScreen";

export default function App() {
  const { isAuthenticated, hasActiveAgent } = useAuth();

  let screen = <LoginScreen />;
  if (isAuthenticated) {
    screen = hasActiveAgent ? <ChatScreen /> : <AgentsScreen />;
  }

  // No solid background here — the body's navy vignette shows through so every screen
  // sits on the same lit canvas.
  return <div className="h-full text-slate-100">{screen}</div>;
}
