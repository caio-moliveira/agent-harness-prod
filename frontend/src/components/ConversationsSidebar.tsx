import { useEffect, useRef, useState } from "react";
import * as api from "../lib/api";
import type { SessionResponse } from "../lib/types";

/**
 * Left rail listing the current agent's past conversations. Selecting one restores it; the active
 * conversation is highlighted and scrolled into view. Rename/delete cover each entry's lifecycle.
 */
export default function ConversationsSidebar({
  userToken,
  agentId,
  currentSessionId,
  reloadKey,
  onSelect,
  onNew,
  onDeletedActive,
}: {
  userToken: string;
  agentId: number | null;
  currentSessionId: string | null;
  reloadKey: number;
  onSelect: (sessionId: string, sessionToken: string) => void;
  onNew: () => void;
  onDeletedActive: () => void;
}) {
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const activeRef = useRef<HTMLLIElement>(null);

  async function load() {
    setError(null);
    try {
      const all = await api.listSessions(userToken);
      // Backend returns them oldest-first; show the freshest conversation at the top.
      const forAgent = all.filter((s) => (agentId == null ? true : s.agent_id === agentId)).reverse();
      setSessions(forAgent);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao carregar conversas.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userToken, agentId, reloadKey]);

  // Bring the active conversation into view when the list (re)renders.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" });
  }, [currentSessionId, sessions.length]);

  async function handleRename(s: SessionResponse) {
    const name = window.prompt("Renomear conversa", s.name || "")?.trim();
    if (name == null || name === s.name) return;
    setBusyId(s.session_id);
    try {
      await api.renameSession(s.session_id, s.token.access_token, name || "Sem título");
      await load();
    } catch {
      /* keep the old name on failure */
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(s: SessionResponse) {
    const confirmed = window.confirm(
      `Excluir a conversa "${s.name || "sem título"}"?\n\n` +
        "Isso remove em definitivo o histórico, as ações e os arquivos gerados nesta conversa. " +
        "Não pode ser desfeito.",
    );
    if (!confirmed) return;
    setBusyId(s.session_id);
    setActionError(null);
    try {
      await api.deleteSession(s.session_id, s.token.access_token);
      if (s.session_id === currentSessionId) onDeletedActive();
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Falha ao excluir a conversa.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-slate-800 bg-slate-950">
      <div className="p-3">
        <button
          onClick={onNew}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500"
        >
          <span className="text-base leading-none">＋</span> Nova conversa
        </button>
      </div>

      {actionError && (
        <div className="mx-3 mb-2 rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-xs text-red-300">
          {actionError}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {loading ? (
          <ul className="space-y-1.5 px-1">
            {[0, 1, 2].map((i) => (
              <li key={i} className="h-9 animate-pulse rounded-lg bg-slate-900" />
            ))}
          </ul>
        ) : error ? (
          <div className="mt-4 px-2 text-center text-xs text-slate-500">
            <p>{error}</p>
            <button
              onClick={() => void load()}
              className="mt-2 rounded-lg border border-slate-700 px-3 py-1.5 hover:bg-slate-800"
            >
              Tentar de novo
            </button>
          </div>
        ) : sessions.length === 0 ? (
          <p className="mt-6 px-3 text-center text-xs text-slate-500">
            Nenhuma conversa ainda. Comece uma nova acima.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {sessions.map((s) => {
              const active = s.session_id === currentSessionId;
              return (
                <li key={s.session_id} ref={active ? activeRef : undefined}>
                  <div
                    className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm ${
                      active ? "bg-slate-800 text-slate-100" : "text-slate-300 hover:bg-slate-900"
                    }`}
                  >
                    <button
                      onClick={() => onSelect(s.session_id, s.token.access_token)}
                      className="min-w-0 flex-1 truncate text-left"
                      title={s.name || "Nova conversa"}
                    >
                      {s.name || "Nova conversa"}
                    </button>
                    <div className="flex shrink-0 items-center gap-0.5 opacity-0 group-hover:opacity-100">
                      <button
                        onClick={() => void handleRename(s)}
                        disabled={busyId === s.session_id}
                        title="Renomear"
                        className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-slate-100 disabled:opacity-50"
                      >
                        ✎
                      </button>
                      <button
                        onClick={() => void handleDelete(s)}
                        disabled={busyId === s.session_id}
                        title="Excluir"
                        className="rounded p-1 text-slate-400 hover:bg-slate-700 hover:text-red-300 disabled:opacity-50"
                      >
                        🗑
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>
  );
}
