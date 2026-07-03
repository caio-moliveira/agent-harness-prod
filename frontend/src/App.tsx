import { useAuth } from "./context/AuthContext";
import LoginScreen from "./components/LoginScreen";
import ChatScreen from "./components/ChatScreen";

export default function App() {
  const { isAuthenticated } = useAuth();
  return (
    <div className="h-full bg-slate-950 text-slate-100">
      {isAuthenticated ? <ChatScreen /> : <LoginScreen />}
    </div>
  );
}
