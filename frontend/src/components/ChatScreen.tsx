import { useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";
import type { Approval, AssistantTurn, SourceStatus, ToolStep, Turn } from "../lib/types";
import MessageBubble from "./MessageBubble";
import Composer from "./Composer";
import SourcesPanel from "./SourcesPanel";
import AgentActivity from "./AgentActivity";
import ApprovalCard from "./ApprovalCard";
import ConversationsSidebar from "./ConversationsSidebar";
import ActivityTimeline from "./ActivityTimeline";

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
  const { agentName, agentId, sessionToken, sessionId, userToken, leaveAgent, logout, newSession, setActiveSession } =
    useAuth();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [showTimeline, setShowTimeline] = useState(true);
  const [sending, setSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [sidebarReload, setSidebarReload] = useState(0);
  const [showSources, setShowSources] = useState(false);
  const [sources, setSources] = useState<SourceStatus>({ db_connected: false });
  const scrollRef = useRef<HTMLDivElement>(null);
  const stepIdRef = useRef(0);
  // Whether to keep the view pinned to the bottom. Turns false as soon as the user scrolls up,
  // so streaming text never yanks their scrollbar back down; turns true when they return to bottom.
  const stickToBottom = useRef(true);

  // Seed inline approval cards from any still-pending action for this session (survives reload).
  async function seedApprovals() {
    if (!userToken || !sessionId) {
      setApprovals([]);
      return;
    }
    try {
      const list = await api.listPendingActions(userToken);
      setApprovals(
        list
          .filter((a) => a.session_id === sessionId && a.action_type === "export_artifact")
          .map((a) => ({
            id: a.id,
            title: (a.payload?.spec as { title?: string } | undefined)?.title ?? "Gerar artefato",
            format: a.payload?.fmt as string | undefined,
            status: "pending" as const,
          })),
      );
    } catch {
      /* ignore transient errors */
    }
  }

  function decideApproval(id: number, status: "approved" | "rejected", error?: string) {
    setApprovals((prev) => prev.map((a) => (a.id === id ? { ...a, status, error } : a)));
  }

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  async function refreshSources() {
    if (!sessionToken) return;
    try {
      setSources(await api.dataStatus(sessionToken));
    } catch {
      /* ignore */
    }
  }

  // Switching session (new, restored, or login) -> rehydrate its persisted history + sources.
  useEffect(() => {
    let cancelled = false;
    async function loadHistory() {
      if (!sessionToken || !sessionId) {
        setTurns([]);
        return;
      }
      setLoadingHistory(true);
      try {
        const msgs = await api.getDataAgentMessages(sessionToken, sessionId);
        if (cancelled) return;
        // Continue the live step-id counter so restored ids never collide with new ones.
        let nextStepId = stepIdRef.current;
        setTurns(
          msgs.map((m) =>
            m.role === "user"
              ? { role: "user", content: m.content }
              : {
                  role: "assistant",
                  content: m.content,
                  streaming: false,
                  steps: m.steps.map((s) => ({
                    id: nextStepId++,
                    name: s.name,
                    input: s.input ?? undefined,
                    output: s.output ?? undefined,
                    done: true,
                  })),
                },
          ),
        );
        stepIdRef.current = nextStepId;
      } catch {
        if (!cancelled) setTurns([]);
      } finally {
        if (!cancelled) setLoadingHistory(false);
      }
    }
    void loadHistory();
    void refreshSources();
    void seedApprovals();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionToken]);

  // The full session activity (restored turns now carry their persisted steps, live turns accrue new ones).
  const liveSteps = turns.flatMap((t) => (t.role === "assistant" ? t.steps : []));

  async function handleNewConversation() {
    await newSession();
    setSidebarReload((k) => k + 1);
  }

  async function handleDeletedActive() {
    await newSession();
    setSidebarReload((k) => k + 1);
  }

  useEffect(() => {
    if (!stickToBottom.current) return; // user scrolled up — don't fight them
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight; // instant, so streaming doesn't jank
  }, [turns]);

  async function handleSend(text: string) {
    if (!sessionToken || !sessionId || sending) return;
    const isFirstMessage = turns.length === 0; // the server names the session from its first message
    stickToBottom.current = true; // re-engage auto-scroll when the user sends

    setTurns((prev) => [
      ...prev,
      { role: "user", content: text },
      { role: "assistant", steps: [], content: "", streaming: true },
    ]);
    setSending(true);

    try {
      // Only the new message is sent — the agent keeps context via its long-term memory, not a replay.
      for await (const ev of api.streamDataQuery(sessionToken, sessionId, text)) {
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
        } else if (ev.type === "hitl_request") {
          // The agent parked an outward action — surface an inline approval card.
          setApprovals((prev) =>
            prev.some((a) => a.id === ev.id)
              ? prev
              : [...prev, { id: ev.id, title: ev.title ?? "Gerar artefato", format: ev.format, status: "pending" }],
          );
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
      // The first message auto-names the session server-side — refresh the sidebar to show it.
      if (isFirstMessage) setSidebarReload((k) => k + 1);
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
      {userToken && (
        <ConversationsSidebar
          userToken={userToken}
          agentId={agentId}
          currentSessionId={sessionId}
          reloadKey={sidebarReload}
          onSelect={setActiveSession}
          onNew={handleNewConversation}
          onDeletedActive={handleDeletedActive}
        />
      )}
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
            <button
              onClick={() => setShowTimeline((v) => !v)}
              title="Linha do tempo das ações"
              className={`rounded-lg border px-3 py-1.5 ${
                showTimeline
                  ? "border-indigo-700 bg-indigo-950/40 text-indigo-200"
                  : "border-slate-700 hover:bg-slate-800"
              }`}
            >
              🕒
            </button>
            <button onClick={handleClear} className="rounded-lg border border-slate-700 px-3 py-1.5 hover:bg-slate-800">
              Limpar
            </button>
            <button onClick={logout} className="rounded-lg border border-slate-700 px-3 py-1.5 hover:bg-slate-800">
              Sair
            </button>
          </div>
        </header>

        <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-y-auto p-4">
          {loadingHistory ? (
            <div className="mx-auto mt-16 max-w-md text-center text-sm text-slate-500">Carregando conversa…</div>
          ) : turns.length === 0 ? (
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
              {userToken && sessionToken && sessionId && approvals.length > 0 && (
                <div className="space-y-2">
                  {approvals.map((a) => (
                    <ApprovalCard
                      key={a.id}
                      approval={a}
                      userToken={userToken}
                      sessionToken={sessionToken}
                      sessionId={sessionId}
                      onDecided={decideApproval}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="mx-auto w-full max-w-3xl">
          <Composer onSend={handleSend} disabled={sending} />
        </div>
      </div>

      {showTimeline && <ActivityTimeline steps={liveSteps} onClose={() => setShowTimeline(false)} />}

      {showSources && <SourcesPanel onClose={handleCloseSources} />}
    </div>
  );
}
