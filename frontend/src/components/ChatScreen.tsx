import { useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";
import type { AssistantTurn, Segment, SourceStatus, ToolStep, Turn } from "../lib/types";
import MessageBubble from "./MessageBubble";
import Composer from "./Composer";
import SourcesPanel from "./SourcesPanel";
import AgentActivity from "./AgentActivity";
import TodoList from "./TodoList";
import ThinkingPanel from "./ThinkingPanel";
import ArtifactApproval from "./ArtifactApproval";
import DeliverableLinks from "./DeliverableLinks";
import ConversationsSidebar from "./ConversationsSidebar";
import ActivityTimeline from "./ActivityTimeline";
import {
  IconArrowLeft,
  IconBroom,
  IconDatabase,
  IconFolder,
  IconLayers,
  IconLogout,
  IconSparkles,
} from "./icons";

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

/** When a turn stops, an item left "in_progress" would spin forever — settle it to a paused mark. */
function settleTodos(a: AssistantTurn): AssistantTurn {
  if (!a.todos?.length) return a;
  return {
    ...a,
    todos: a.todos.map((t) => (t.status === "in_progress" ? { ...t, status: "stopped" } : t)),
  };
}

/** Append a tool step to the chronological segments, batching consecutive tools into one group. */
function pushToolStep(segments: Segment[], step: ToolStep): Segment[] {
  const last = segments[segments.length - 1];
  if (last?.kind === "tools") {
    return [...segments.slice(0, -1), { kind: "tools", steps: [...last.steps, step] }];
  }
  return [...segments, { kind: "tools", steps: [step] }];
}

/** Append a text delta to the segments, growing the current text block or opening a new one. */
function pushText(segments: Segment[], text: string): Segment[] {
  const last = segments[segments.length - 1];
  if (last?.kind === "text") {
    return [...segments.slice(0, -1), { kind: "text", text: last.text + text }];
  }
  return [...segments, { kind: "text", text }];
}

/** Close the most recent matching open tool step inside whichever tools segment holds it. */
function closeStepInSegments(segments: Segment[], name: string, output?: string): Segment[] {
  for (let i = segments.length - 1; i >= 0; i--) {
    const seg = segments[i];
    if (seg.kind !== "tools") continue;
    const idx = [...seg.steps].reverse().findIndex((s) => s.name === name && !s.done);
    if (idx === -1) continue;
    const realIdx = seg.steps.length - 1 - idx;
    const steps = seg.steps.map((s, j) => (j === realIdx ? { ...s, output, done: true } : s));
    return segments.map((s, j) => (j === i ? { kind: "tools", steps } : s));
  }
  return segments;
}

export default function ChatScreen() {
  const {
    agentName,
    agentId,
    sessionToken,
    sessionId,
    userToken,
    leaveAgent,
    logout,
    startSession,
    clearSession,
    setActiveSession,
  } = useAuth();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [showTimeline, setShowTimeline] = useState(true);
  const [sending, setSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [sidebarReload, setSidebarReload] = useState(0);
  const [showSources, setShowSources] = useState(false);
  const [sources, setSources] = useState<SourceStatus>({ db_connected: false });
  const scrollRef = useRef<HTMLDivElement>(null);
  const stepIdRef = useRef(0);
  // Set when a session is created mid-send, so the [sessionToken] effect doesn't reload (and wipe)
  // the optimistic turns we just added for a brand-new, empty conversation.
  const skipLoadRef = useRef(false);
  // Whether to keep the view pinned to the bottom. Turns false as soon as the user scrolls up,
  // so streaming text never yanks their scrollbar back down; turns true when they return to bottom.
  const stickToBottom = useRef(true);

  // Update the status of the approval anchored to whichever assistant turn owns this action id.
  function setApprovalStatus(actionId: number, status: "approved" | "rejected") {
    setTurns((prev) =>
      prev.map((t) =>
        t.role === "assistant" && t.approval?.id === actionId
          ? { ...t, approval: { ...t.approval, status } }
          : t,
      ),
    );
  }

  // Anchor a still-pending artifact approval onto the last assistant turn of a restored conversation.
  async function attachPendingApproval(built: Turn[], sid: string) {
    if (!userToken) return;
    try {
      const pending = await api.listPendingActions(userToken);
      const mine = pending.filter(
        (a) => a.session_id === sid && (a.action_type === "export_artifact" || a.action_type === "approve_plan"),
      );
      if (!mine.length) return;
      const action = mine[mine.length - 1];
      for (let i = built.length - 1; i >= 0; i--) {
        const t = built[i];
        if (t.role === "assistant") {
          t.approval = {
            id: action.id,
            title:
              (action.payload?.spec as { title?: string } | undefined)?.title ??
              (action.payload?.title as string | undefined) ??
              (action.action_type === "approve_plan" ? "plano" : "artefato"),
            format: action.payload?.fmt as string | undefined,
            status: "pending",
            action_type: action.action_type,
          };
          break;
        }
      }
    } catch {
      /* ignore transient errors */
    }
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
    // A session just created for an in-flight first message — its history is the optimistic view.
    if (skipLoadRef.current) {
      skipLoadRef.current = false;
      return;
    }
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
        const built: Turn[] = msgs.map((m) => {
          if (m.role === "user") return { role: "user", content: m.content };
          const steps: ToolStep[] = m.steps.map((s) => ({
            id: nextStepId++,
            name: s.name,
            input: s.input ?? undefined,
            output: s.output ?? undefined,
            done: true,
          }));
          // Persisted turns don't record the tools↔text interleaving, so restore them as one tool
          // batch followed by the answer — live turns stream true interleaved segments.
          const segments: Segment[] = [];
          if (steps.length > 0) segments.push({ kind: "tools", steps });
          if (m.content) segments.push({ kind: "text", text: m.content });
          return { role: "assistant", content: m.content, streaming: false, steps, segments };
        });
        stepIdRef.current = nextStepId;
        // Re-anchor a still-pending artifact approval to the last assistant turn so it can be
        // decided after a reload — without floating at the bottom of the conversation.
        await attachPendingApproval(built, sessionId);
        if (cancelled) return;
        setTurns(built);
      } catch {
        if (!cancelled) setTurns([]);
      } finally {
        if (!cancelled) setLoadingHistory(false);
      }
    }
    void loadHistory();
    void refreshSources();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionToken]);

  // The full session activity (restored turns now carry their persisted steps, live turns accrue new ones).
  const liveSteps = turns.flatMap((t) => (t.role === "assistant" ? t.steps : []));

  // "Nova conversa" / deleting the active one just drops the session — a new one starts on the
  // next message, so we never create empty conversations.
  function handleNewConversation() {
    clearSession();
  }

  function handleDeletedActive() {
    clearSession();
  }

  // Open the sources panel, creating the session first so a connected DB/folder has one to attach to.
  async function openSources() {
    if (!sessionToken || !sessionId) {
      skipLoadRef.current = true;
      const created = await startSession();
      if (created) setSidebarReload((k) => k + 1);
    }
    setShowSources(true);
  }

  useEffect(() => {
    if (!stickToBottom.current) return; // user scrolled up — don't fight them
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight; // instant, so streaming doesn't jank
  }, [turns]);

  async function handleSend(text: string) {
    if (sending) return;

    // Create the session on the first message (lazy) — its id then shows in the URL.
    let sid = sessionId;
    let stoken = sessionToken;
    const justCreated = !sid || !stoken;
    if (justCreated) {
      skipLoadRef.current = true; // don't let the [sessionToken] effect wipe the optimistic turns
      const created = await startSession();
      if (!created) return;
      sid = created.sessionId;
      stoken = created.sessionToken;
      setSidebarReload((k) => k + 1); // the fresh conversation appears in the sidebar
    }
    if (!sid || !stoken) return;

    stickToBottom.current = true; // re-engage auto-scroll when the user sends

    setTurns((prev) => [
      ...prev,
      { role: "user", content: text },
      { role: "assistant", steps: [], content: "", segments: [], streaming: true },
    ]);
    setSending(true);

    try {
      // Only the new message is sent — the agent keeps context via its long-term memory, not a replay.
      for await (const ev of api.streamDataQuery(stoken, sid, text)) {
        if (ev.type === "tool_start") {
          const id = stepIdRef.current++;
          const step: ToolStep = { id, name: ev.name, input: ev.input, done: false };
          setTurns((prev) =>
            updateLastAssistant(prev, (a) => ({
              ...a,
              steps: [...a.steps, step],
              segments: pushToolStep(a.segments, step),
            })),
          );
        } else if (ev.type === "tool_end") {
          setTurns((prev) =>
            updateLastAssistant(prev, (a) => ({
              ...a,
              steps: closeStep(a.steps, ev.name, ev.output),
              segments: closeStepInSegments(a.segments, ev.name, ev.output),
            })),
          );
        } else if (ev.type === "token") {
          setTurns((prev) =>
            updateLastAssistant(prev, (a) => ({
              ...a,
              content: a.content + ev.content,
              segments: pushText(a.segments, ev.content),
            })),
          );
        } else if (ev.type === "thinking") {
          // Live reasoning stream — accumulate into the turn's thinking panel.
          setTurns((prev) =>
            updateLastAssistant(prev, (a) => ({ ...a, thinking: (a.thinking ?? "") + ev.content })),
          );
        } else if (ev.type === "todos") {
          // Live plan checklist — replaced whenever the agent re-issues write_todos.
          setTurns((prev) => updateLastAssistant(prev, (a) => ({ ...a, todos: ev.items })));
        } else if (ev.type === "hitl_request") {
          // The agent parked an outward action (artifact export or a plan) — anchor a compact
          // approval to this turn.
          setTurns((prev) =>
            updateLastAssistant(prev, (a) => ({
              ...a,
              approval: {
                id: ev.id,
                title: ev.title ?? "artefato",
                format: ev.format,
                status: "pending",
                action_type: ev.action_type,
              },
            })),
          );
        } else if (ev.type === "error") {
          setTurns((prev) =>
            updateLastAssistant(prev, (a) => ({ ...settleTodos(a), streaming: false, error: ev.content })),
          );
        } else if (ev.type === "done") {
          setTurns((prev) => updateLastAssistant(prev, (a) => ({ ...settleTodos(a), streaming: false })));
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Erro ao enviar";
      setTurns((prev) =>
        updateLastAssistant(prev, (a) => ({ ...settleTodos(a), streaming: false, error: message })),
      );
    } finally {
      setSending(false);
      // The first message auto-names the session server-side — refresh the sidebar to show the name.
      if (justCreated) setSidebarReload((k) => k + 1);
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
        <header className="flex items-center justify-between border-b border-slate-800 px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <button
              onClick={leaveAgent}
              title="Trocar de agente"
              className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 hover:bg-slate-800 hover:text-slate-100"
            >
              <IconArrowLeft className="h-4 w-4" />
            </button>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold text-slate-100">{agentName ?? "Agente"}</h1>
              <p className="truncate text-xs text-slate-500">Data Agent</p>
            </div>
          </div>
          <div className="flex items-center gap-1.5 text-xs">
            <span
              className={`hidden items-center gap-1 rounded-full px-2.5 py-1 sm:inline-flex ${sources.db_connected ? "bg-emerald-950/60 text-emerald-300 ring-1 ring-inset ring-emerald-800/50" : "bg-slate-800/70 text-slate-500"}`}
            >
              <IconDatabase className="h-3.5 w-3.5" />
              {sources.db_connected ? sources.dialect : "sem banco"}
            </span>
            <span
              className={`hidden items-center gap-1 rounded-full px-2.5 py-1 sm:inline-flex ${sources.folder ? "bg-emerald-950/60 text-emerald-300 ring-1 ring-inset ring-emerald-800/50" : "bg-slate-800/70 text-slate-500"}`}
            >
              <IconFolder className="h-3.5 w-3.5" />
              {sources.folder ? "pasta" : "sem pasta"}
            </span>
            <button
              onClick={() => void openSources()}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600/15 px-3 py-1.5 font-medium text-indigo-200 ring-1 ring-inset ring-indigo-500/30 hover:bg-indigo-600/25"
            >
              <IconDatabase className="h-4 w-4" />
              <span className="hidden md:inline">Fontes</span>
            </button>
            <button
              onClick={() => setShowTimeline((v) => !v)}
              title="Linha do tempo das ações"
              className={`grid h-8 w-8 place-items-center rounded-lg ${
                showTimeline
                  ? "bg-indigo-600/15 text-indigo-200 ring-1 ring-inset ring-indigo-500/30"
                  : "text-slate-400 hover:bg-slate-800 hover:text-slate-100"
              }`}
            >
              <IconLayers className="h-4 w-4" />
            </button>
            <button
              onClick={handleClear}
              title="Limpar a tela"
              className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 hover:bg-slate-800 hover:text-slate-100"
            >
              <IconBroom className="h-4 w-4" />
            </button>
            <button
              onClick={logout}
              title="Sair"
              className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 hover:bg-slate-800 hover:text-slate-100"
            >
              <IconLogout className="h-4 w-4" />
            </button>
          </div>
        </header>

        <p className="border-b border-slate-800/60 px-4 py-1.5 text-center text-[11px] text-slate-600">
          O agente pode consultar suas fontes conectadas. Revise os resultados antes de decisões críticas.
        </p>

        <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-y-auto p-4">
          {loadingHistory ? (
            <div className="mx-auto mt-16 max-w-md text-center text-sm text-slate-500">Carregando conversa…</div>
          ) : turns.length === 0 ? (
            <div className="mx-auto mt-20 flex max-w-md flex-col items-center text-center">
              <div className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-indigo-500 to-indigo-700 text-white shadow-xl shadow-indigo-950/50">
                <IconSparkles className="h-7 w-7" />
              </div>
              <p className="mt-4 text-lg font-semibold text-slate-200">Converse com o agente</p>
              <p className="mt-2 text-sm leading-relaxed text-slate-500">
                {hasSource
                  ? "Fontes conectadas — pergunte sobre seu banco ou seus arquivos."
                  : "Dica: abra Fontes para conectar um banco ou autorizar uma pasta e o agente ganha ferramentas."}
              </p>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-5">
              {turns.map((turn, i) =>
                turn.role === "user" ? (
                  <div key={i} className="animate-rise">
                    <MessageBubble message={{ role: "user", content: turn.content }} authorName="Você" />
                  </div>
                ) : (
                  <div key={i} className="animate-rise">
                    <div className="pl-[42px]">
                      {turn.thinking && (
                        <ThinkingPanel
                          text={turn.thinking}
                          streaming={turn.streaming}
                          hasAnswer={Boolean(turn.content)}
                        />
                      )}
                      {turn.todos && turn.todos.length > 0 && <TodoList items={turn.todos} />}
                    </div>
                    {/* Tools and answer text in the order they streamed — interleaved, not stacked. */}
                    {turn.segments.map((seg, si) =>
                      seg.kind === "tools" ? (
                        <div key={si} className="pl-[42px]">
                          <AgentActivity steps={seg.steps} />
                        </div>
                      ) : (
                        <MessageBubble
                          key={si}
                          message={{ role: "assistant", content: seg.text }}
                          authorName={agentName ?? "Agente"}
                        />
                      ),
                    )}
                    {turn.streaming && turn.segments[turn.segments.length - 1]?.kind !== "text" && (
                      <MessageBubble
                        message={{ role: "assistant", content: "" }}
                        pending
                        authorName={agentName ?? "Agente"}
                      />
                    )}
                    {!turn.streaming && (
                      <DeliverableLinks steps={turn.steps} sessionToken={sessionToken} sessionId={sessionId} />
                    )}
                    {turn.approval && userToken && (
                      <div className="pl-[42px]">
                        <ArtifactApproval
                          approval={turn.approval}
                          userToken={userToken}
                          sessionToken={sessionToken}
                          sessionId={sessionId}
                          onDecided={(status) => setApprovalStatus(turn.approval!.id, status)}
                          onApprovedResume={() => void handleSend("Plano aprovado, pode prosseguir com a execução.")}
                        />
                      </div>
                    )}
                    {turn.error && (
                      <div className="ml-[42px] mt-1 rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-300">
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

      {showTimeline && <ActivityTimeline steps={liveSteps} onClose={() => setShowTimeline(false)} />}

      {showSources && <SourcesPanel onClose={handleCloseSources} />}
    </div>
  );
}
