import { useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";
import type { AssistantTurn, SourceStatus, ToolStep, Turn } from "../lib/types";
import MessageBubble from "./MessageBubble";
import Composer from "./Composer";
import SourcesPanel from "./SourcesPanel";
import AgentActivity from "./AgentActivity";

function updateLastAssistant(turns: Turn[], fn: (a: AssistantTurn) => AssistantTurn): Turn[] {
  const copy = [...turns];
  for (let i = copy.length - 1; i >= 0; i--) {
    const turn = copy[i];
    if (turn.role === "assistant") {
      copy[i] = fn(turn);
      break;
    }
  }
  return copy;
}

function closeStep(steps: ToolStep[], name: string, output?: string): ToolStep[] {
  const copy = [...steps];
  for (let i = copy.length - 1; i >= 0; i--) {
    if (copy[i].name === name && !copy[i].done) {
      copy[i] = { ...copy[i], output, done: true };
      break;
    }
  }
  return copy;
}

export default function ChatScreen() {
  const { agentName, sessionToken, leaveAgent, logout } = useAuth();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sending, setSending] = useState(false);
  const [showSources, setShowSources] = useState(false);
  const [sources, setSources] = useState<SourceStatus>({ db_connected: false });
  const scrollRef = useRef<HTMLDivElement>(null);
  const stepIdRef = useRef(0);

  async function refreshSources() {
    if (!sessionToken) return;
    try {
      setSources(await api.dataStatus(sessionToken));
    } catch {
      /* ignore */
    }
  }

  // New session (or login) -> fresh conversation + reload which sources are attached.
  useEffect(() => {
    setTurns([]);
    void refreshSources();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionToken]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  async function handleSend(text: string) {
    if (!sessionToken || sending) return;
    const history = turns
      .filter((t) => t.content)
      .map((t) => ({ role: t.role, content: t.content }));
    const outgoing = [...history, { role: "user", content: text }];

    setTurns((prev) => [
      ...prev,
      { role: "user", content: text },
      { role: "assistant", steps: [], content: "", streaming: true },
    ]);
    setSending(true);

    try {
      for await (const ev of api.streamDataQuery(sessionToken, outgoing)) {
        if (ev.type === "tool_start") {
          const id = stepIdRef.current++;
          setTurns((prev) =>
            updateLastAssistant(prev, (a) => ({
              ...a,
              steps: [...a.steps, { id, name: ev.name, input: ev.input, done: false }],
            })),
          );
        } else if (ev.type === "tool_end") {
          setTurns((prev) =>
            updateLastAssistant(prev, (a) => ({ ...a, steps: closeStep(a.steps, ev.name, ev.output) })),
          );
        } else if (ev.type === "token") {
          setTurns((prev) => updateLastAssistant(prev, (a) => ({ ...a, content: a.content + ev.content })));
        } else if (ev.type === "error") {
          setTurns((prev) => updateLastAssistant(prev, (a) => ({ ...a, streaming: false, error: ev.content })));
        } else if (ev.type === "done") {
          setTurns((prev) => updateLastAssistant(prev, (a) => ({ ...a, streaming: false })));
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Erro ao enviar";
      setTurns((prev) => updateLastAssistant(prev, (a) => ({ ...a, streaming: false, error: message })));
    } finally {
      setSending(false);
    }
  }

  function handleClear() {
    setTurns([]);
  }

  function handleCloseSources() {
    setShowSources(false);
    void refreshSources();
  }

  const hasSource = sources.db_connected || Boolean(sources.folder);

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <button
              onClick={leaveAgent}
              title="Trocar de agente"
              className="rounded-lg border border-slate-700 px-2 py-1.5 text-xs hover:bg-slate-800"
            >
              ←
            </button>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold">{agentName ?? "Agente"}</h1>
              <p className="truncate text-xs text-slate-500">Agent Harness</p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className={`rounded-full px-2 py-1 ${sources.db_connected ? "bg-emerald-900 text-emerald-200" : "bg-slate-800 text-slate-500"}`}>
              🛢️ {sources.db_connected ? sources.dialect : "sem banco"}
            </span>
            <span className={`rounded-full px-2 py-1 ${sources.folder ? "bg-emerald-900 text-emerald-200" : "bg-slate-800 text-slate-500"}`}>
              📁 {sources.folder ? "pasta" : "sem pasta"}
            </span>
            <button onClick={() => setShowSources(true)} className="rounded-lg border border-indigo-700 bg-indigo-950/40 px-3 py-1.5 text-indigo-200 hover:bg-indigo-900/50">
              Fontes
            </button>
            <button onClick={handleClear} className="rounded-lg border border-slate-700 px-3 py-1.5 hover:bg-slate-800">
              Limpar
            </button>
            <button onClick={logout} className="rounded-lg border border-slate-700 px-3 py-1.5 hover:bg-slate-800">
              Sair
            </button>
          </div>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto p-4">
          {turns.length === 0 ? (
            <div className="mx-auto mt-16 max-w-md text-center text-sm text-slate-500">
              <p className="text-base text-slate-300">Converse com o agente.</p>
              <p className="mt-2">
                {hasSource
                  ? "Fontes conectadas — pergunte sobre seu banco ou seus arquivos."
                  : "Dica: abra Fontes para conectar um banco ou autorizar uma pasta e o agente ganha ferramentas."}
              </p>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-4">
              {turns.map((turn, i) =>
                turn.role === "user" ? (
                  <MessageBubble key={i} message={{ role: "user", content: turn.content }} />
                ) : (
                  <div key={i}>
                    <AgentActivity steps={turn.steps} />
                    {(turn.content || turn.streaming) && (
                      <MessageBubble
                        message={{ role: "assistant", content: turn.content }}
                        pending={turn.streaming && !turn.content}
                      />
                    )}
                    {turn.error && (
                      <div className="mt-1 rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-300">
                        {turn.error}
                      </div>
                    )}
                  </div>
                ),
              )}
            </div>
          )}
        </div>

        <div className="mx-auto w-full max-w-3xl">
          <Composer onSend={handleSend} disabled={sending} />
        </div>
      </div>

      {showSources && <SourcesPanel onClose={handleCloseSources} />}
    </div>
  );
}
