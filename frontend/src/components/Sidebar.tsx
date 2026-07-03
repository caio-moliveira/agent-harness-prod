import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";
import type { SessionResponse } from "../lib/types";

export default function Sidebar() {
  const { userToken, sessionId, setActiveSession, newSession } = useAuth();
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (!userToken) return;
    try {
      const list = await api.listSessions(userToken);
      setSessions(list);
    } catch {
      // keep whatever we had
    } finally {
      setLoading(false);
    }
  }, [userToken]);

  // Reload the list whenever the active session changes (covers create/switch).
  useEffect(() => {
    void refresh();
  }, [refresh, sessionId]);

  async function handleNew() {
    setBusy(true);
    try {
      await newSession();
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function handleRename(s: SessionResponse) {
    const name = window.prompt("Nome da sessão", s.name ?? "");
    if (name == null) return;
    await api.renameSession(s.session_id, s.token.access_token, name).catch(() => undefined);
    await refresh();
  }

  async function handleDelete(s: SessionResponse) {
    if (!window.confirm("Excluir esta sessão e seu histórico?")) return;
    await api.deleteSession(s.session_id, s.token.access_token).catch(() => undefined);
    const wasActive = s.session_id === sessionId;
    await refresh();
    if (wasActive) await newSession();
  }

  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-slate-800 bg-slate-900 md:flex">
      <div className="p-3">
        <button
          onClick={handleNew}
          disabled={busy}
          className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium hover:bg-indigo-500 disabled:opacity-50"
        >
          + Nova conversa
        </button>
      </div>

      <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {loading ? (
          <p className="px-2 py-3 text-xs text-slate-500">Carregando sessões…</p>
        ) : sessions.length === 0 ? (
          <p className="px-2 py-3 text-xs text-slate-500">Nenhuma sessão ainda.</p>
        ) : (
          sessions.map((s) => {
            const active = s.session_id === sessionId;
            const label = s.name?.trim() || `Sessão ${s.session_id.slice(0, 8)}`;
            return (
              <div
                key={s.session_id}
                className={`group flex items-center gap-1 rounded-lg px-2 py-2 text-sm ${
                  active ? "bg-slate-800 text-white" : "text-slate-300 hover:bg-slate-800/60"
                }`}
              >
                <button
                  onClick={() => setActiveSession(s.session_id, s.token.access_token)}
                  className="min-w-0 flex-1 truncate text-left"
                  title={label}
                >
                  {label}
                </button>
                <button
                  onClick={() => handleRename(s)}
                  className="opacity-0 transition group-hover:opacity-100"
                  title="Renomear"
                >
                  ✏️
                </button>
                <button
                  onClick={() => handleDelete(s)}
                  className="opacity-0 transition group-hover:opacity-100"
                  title="Excluir"
                >
                  🗑️
                </button>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
