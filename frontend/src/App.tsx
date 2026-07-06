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

  return <div className="h-full bg-slate-950 text-slate-100">{screen}</div>;
}
