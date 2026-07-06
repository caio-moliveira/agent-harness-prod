import { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";
import type { Agent } from "../lib/types";

export default function AgentsScreen() {
  const { email, userToken, selectAgent, logout } = useAuth();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);

  // Per-card folder editing.
  const [editingId, setEditingId] = useState<number | null>(null);
  const [folderInput, setFolderInput] = useState("");

  async function refresh() {
    if (!userToken) return;
    setLoading(true);
    try {
      setAgents(await api.listAgents(userToken));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao carregar agentes");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userToken]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!userToken || !name.trim() || busy) return;
    setBusy(true);
    try {
      const agent = await api.createAgent(userToken, name.trim(), prompt.trim());
      let created = agent;
      if (folder.trim()) {
        const res = await api.bindAgentFolder(userToken, agent.id, folder.trim());
        created = { ...agent, folder: res.folder };
      }
      setName("");
      setPrompt("");
      setFolder("");
      setCreating(false);
      setAgents((prev) => [...prev, created]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao criar agente");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(agent: Agent) {
    if (!userToken) return;
    try {
      await api.deleteAgent(userToken, agent.id);
      setAgents((prev) => prev.filter((a) => a.id !== agent.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao excluir agente");
    }
  }

  function startEditFolder(agent: Agent) {
    setEditingId(agent.id);
    setFolderInput(agent.folder ?? "");
  }

  async function saveFolder(agent: Agent) {
    if (!userToken) return;
    try {
      const path = folderInput.trim();
      const res = path
        ? await api.bindAgentFolder(userToken, agent.id, path)
        : await api.unbindAgentFolder(userToken, agent.id);
      setAgents((prev) => prev.map((a) => (a.id === agent.id ? { ...a, folder: res.folder } : a)));
      setEditingId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao definir a pasta");
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col p-6">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Seus agentes</h1>
          <p className="text-xs text-slate-500">{email}</p>
        </div>
        <button
          onClick={logout}
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs hover:bg-slate-800"
        >
          Sair
        </button>
      </header>

      {error && (
        <div className="mb-4 rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="flex-1 space-y-3 overflow-y-auto">
        {loading ? (
          <p className="text-sm text-slate-500">Carregando…</p>
        ) : agents.length === 0 ? (
          <p className="text-sm text-slate-500">
            Nenhum agente ainda. Crie o primeiro para começar a conversar.
          </p>
        ) : (
          agents.map((agent) => (
            <div
              key={agent.id}
              className="rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-3"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{agent.name}</p>
                  <p className="truncate text-xs text-slate-500">
                    {agent.system_prompt || "Sem prompt de sistema"}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    onClick={() => void selectAgent(agent)}
                    className="rounded-lg border border-indigo-700 bg-indigo-950/40 px-3 py-1.5 text-xs text-indigo-200 hover:bg-indigo-900/50"
                  >
                    Conversar
                  </button>
                  <button
                    onClick={() => void handleDelete(agent)}
                    className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-400 hover:bg-slate-800"
                  >
                    Excluir
                  </button>
                </div>
              </div>

              <div className="mt-2 flex items-center gap-2 text-xs">
                <span className="text-slate-500">📁</span>
                {editingId === agent.id ? (
                  <>
                    <input
                      value={folderInput}
                      onChange={(e) => setFolderInput(e.target.value)}
                      placeholder="Caminho da pasta (deve estar em SANDBOX_ALLOWED_ROOTS)"
                      className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 outline-none focus:border-indigo-600"
                      autoFocus
                    />
                    <button
                      onClick={() => void saveFolder(agent)}
                      className="rounded border border-indigo-700 bg-indigo-950/40 px-2 py-1 text-indigo-200 hover:bg-indigo-900/50"
                    >
                      Salvar
                    </button>
                    <button
                      onClick={() => setEditingId(null)}
                      className="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800"
                    >
                      Cancelar
                    </button>
                  </>
                ) : (
                  <>
                    <span className="min-w-0 flex-1 truncate text-slate-400">
                      {agent.folder ? agent.folder : "nenhuma pasta vinculada"}
                    </span>
                    <button
                      onClick={() => startEditFolder(agent)}
                      className="rounded border border-slate-700 px-2 py-1 text-slate-400 hover:bg-slate-800"
                    >
                      {agent.folder ? "Alterar" : "Vincular"}
                    </button>
                  </>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      <div className="mt-4 border-t border-slate-800 pt-4">
        {creating ? (
          <form onSubmit={handleCreate} className="space-y-3">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Nome do agente"
              className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-600"
              autoFocus
            />
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Prompt de sistema (opcional) — como o agente deve se comportar"
              rows={3}
              className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-600"
            />
            <input
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
              placeholder="Pasta (opcional) — caminho dentro de SANDBOX_ALLOWED_ROOTS"
              className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-600"
            />
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={busy || !name.trim()}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                Criar
              </button>
              <button
                type="button"
                onClick={() => setCreating(false)}
                className="rounded-lg border border-slate-700 px-4 py-2 text-sm hover:bg-slate-800"
              >
                Cancelar
              </button>
            </div>
          </form>
        ) : (
          <button
            onClick={() => setCreating(true)}
            className="w-full rounded-lg border border-dashed border-slate-700 px-4 py-3 text-sm text-slate-400 hover:border-indigo-600 hover:text-indigo-300"
          >
            + Novo agente
          </button>
        )}
      </div>
    </div>
  );
}
