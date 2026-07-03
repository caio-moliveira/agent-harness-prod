import { useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";
import type { Message } from "../lib/types";
import MessageBubble from "./MessageBubble";
import Composer from "./Composer";
import Sidebar from "./Sidebar";

export default function ChatScreen() {
  const { email, sessionToken, sessionId, logout } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Load prior turns for this session (kept server-side by the checkpointer).
  useEffect(() => {
    if (!sessionToken) return;
    setLoading(true);
    api
      .getMessages(sessionToken)
      .then(setMessages)
      .catch(() => setMessages([]))
      .finally(() => setLoading(false));
  }, [sessionToken]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  async function handleSend(text: string) {
    if (!sessionToken || sending) return;
    setError(null);
    const userMessage: Message = { role: "user", content: text };
    // Optimistic: show the user message + an empty assistant bubble to fill in.
    setMessages((prev) => [...prev, userMessage, { role: "assistant", content: "" }]);
    setSending(true);
    try {
      let acc = "";
      for await (const token of api.streamChat(sessionToken, [userMessage])) {
        acc += token;
        setMessages((prev) => {
          const copy = [...prev];
          copy[copy.length - 1] = { role: "assistant", content: acc };
          return copy;
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao enviar mensagem");
      // Drop the empty assistant placeholder on failure.
      setMessages((prev) => (prev.at(-1)?.content ? prev : prev.slice(0, -1)));
    } finally {
      setSending(false);
    }
  }

  async function handleClear() {
    if (!sessionToken) return;
    await api.clearMessages(sessionToken).catch(() => undefined);
    setMessages([]);
  }

  return (
    <div className="flex h-full">
      <Sidebar />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div className="min-w-0">
            <h1 className="text-sm font-semibold">Agent Harness — Chatbot</h1>
            <p className="truncate text-xs text-slate-500">
              {email} · sessão {sessionId?.slice(0, 8)}
            </p>
          </div>
          <div className="flex gap-2 text-xs">
            <button
              onClick={handleClear}
              className="rounded-lg border border-slate-700 px-3 py-1.5 hover:bg-slate-800"
            >
              Limpar
            </button>
            <button
              onClick={logout}
              className="rounded-lg border border-slate-700 px-3 py-1.5 hover:bg-slate-800"
            >
              Sair
            </button>
          </div>
        </header>

        <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4">
          {loading ? (
            <p className="text-center text-sm text-slate-500">Carregando histórico…</p>
          ) : messages.length === 0 ? (
            <p className="mt-10 text-center text-sm text-slate-500">
              Diga um "olá" para começar a conversa.
            </p>
          ) : (
            <div className="mx-auto max-w-3xl space-y-3">
              {messages.map((m, i) => (
                <MessageBubble
                  key={i}
                  message={m}
                  pending={sending && i === messages.length - 1 && m.role === "assistant"}
                />
              ))}
            </div>
          )}
        </div>

        {error && (
          <div className="mx-4 mb-2 rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}

        <div className="mx-auto w-full max-w-3xl">
          <Composer onSend={handleSend} disabled={sending} />
        </div>
      </div>
    </div>
  );
}
