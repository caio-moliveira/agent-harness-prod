import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";
import { filterFolderFiles, validateFolderSize } from "../lib/folderUpload";
import type { Agent, Skill } from "../lib/types";

// Opened rarely (an explicit "Skills" click), so it doesn't need to be in the initial bundle.
const SkillsPanel = lazy(() => import("./SkillsPanel"));

export default function AgentsScreen() {
  const { email, userToken, selectAgent, logout } = useAuth();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [library, setLibrary] = useState<Skill[]>([]);
  const [showSkills, setShowSkills] = useState(false);
  const [skillEditingId, setSkillEditingId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [folder, setFolder] = useState("");
  const [webSearch, setWebSearch] = useState(false);
  const [sqlEnabled, setSqlEnabled] = useState(false);
  const [memory, setMemory] = useState(true);
  const [busy, setBusy] = useState(false);

  // Per-card folder editing.
  const [editingId, setEditingId] = useState<number | null>(null);
  const [folderInput, setFolderInput] = useState("");
  const [folderWritable, setFolderWritable] = useState(false);
  const [uploadingFolder, setUploadingFolder] = useState(false);
  const folderInputRef = useRef<HTMLInputElement>(null);

  // Per-card database editing.
  const [dbEditingId, setDbEditingId] = useState<number | null>(null);
  const emptyDb = { driver: "postgresql", host: "", port: "5432", database: "", username: "", password: "" };
  const [dbForm, setDbForm] = useState(emptyDb);

  async function refresh() {
    if (!userToken) return;
    setLoading(true);
    try {
      const [ags, skills] = await Promise.all([api.listAgents(userToken), api.listSkills(userToken)]);
      setAgents(ags);
      setLibrary(skills);
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

  async function toggleAttachedSkill(agent: Agent, skillId: number) {
    if (!userToken) return;
    const current = new Set(agent.skills ?? []);
    if (current.has(skillId)) current.delete(skillId);
    else current.add(skillId);
    try {
      const updated = await api.attachAgentSkills(userToken, agent.id, [...current]);
      setAgents((prev) => prev.map((a) => (a.id === agent.id ? updated : a)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao anexar skill");
    }
  }

  async function reloadLibrary() {
    if (!userToken) return;
    try {
      setLibrary(await api.listSkills(userToken));
    } catch {
      /* ignore */
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!userToken || !name.trim() || busy) return;
    setBusy(true);
    try {
      const agent = await api.createAgent(userToken, name.trim(), prompt.trim(), {
        web_search: webSearch,
        sql: sqlEnabled,
        memory,
      });
      let created = agent;
      if (folder.trim()) {
        const res = await api.bindAgentFolder(userToken, agent.id, folder.trim());
        created = { ...agent, folder: res.folder };
      }
      setName("");
      setPrompt("");
      setFolder("");
      setWebSearch(false);
      setSqlEnabled(false);
      setMemory(true);
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
    setFolderWritable(agent.folder_writable ?? false);
  }

  async function saveFolder(agent: Agent) {
    if (!userToken) return;
    try {
      const path = folderInput.trim();
      const res = path
        ? await api.bindAgentFolder(userToken, agent.id, path, folderWritable)
        : await api.unbindAgentFolder(userToken, agent.id);
      setAgents((prev) =>
        prev.map((a) =>
          a.id === agent.id ? { ...a, folder: res.folder, folder_writable: res.folder_writable } : a,
        ),
      );
      setEditingId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao definir a pasta");
    }
  }

  async function handleFolderPicked(e: React.ChangeEvent<HTMLInputElement>, agent: Agent) {
    const fileList = e.target.files;
    e.target.value = ""; // allow re-picking the same folder later
    if (!userToken || !fileList || fileList.length === 0) return;

    const filtered = filterFolderFiles(fileList);
    const sizeError = validateFolderSize(filtered);
    if (sizeError) {
      setError(sizeError);
      return;
    }

    setError(null);
    setUploadingFolder(true);
    try {
      const res = await api.uploadAgentFolder(userToken, agent.id, filtered.files, folderWritable);
      setAgents((prev) =>
        prev.map((a) =>
          a.id === agent.id ? { ...a, folder: res.folder, folder_writable: res.folder_writable } : a,
        ),
      );
      setEditingId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao enviar a pasta");
    } finally {
      setUploadingFolder(false);
    }
  }

  function startEditDb(agent: Agent) {
    setDbEditingId(agent.id);
    const d = agent.database;
    setDbForm(
      d
        ? { driver: d.driver, host: d.host, port: String(d.port), database: d.database, username: d.username, password: "" }
        : emptyDb,
    );
  }

  async function saveDb(agent: Agent) {
    if (!userToken) return;
    try {
      const res = await api.bindAgentDatabase(userToken, agent.id, {
        driver: dbForm.driver,
        host: dbForm.host.trim(),
        port: Number(dbForm.port),
        database: dbForm.database.trim(),
        username: dbForm.username.trim(),
        password: dbForm.password,
      });
      setAgents((prev) => prev.map((a) => (a.id === agent.id ? { ...a, database: res.database } : a)));
      if (!res.password_persisted) {
        setError("Banco vinculado, mas a senha NÃO foi salva (ENCRYPTION_KEY ausente). Reconecte a senha por sessão.");
      }
      setDbEditingId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao vincular o banco");
    }
  }

  async function removeDb(agent: Agent) {
    if (!userToken) return;
    try {
      await api.unbindAgentDatabase(userToken, agent.id);
      setAgents((prev) => prev.map((a) => (a.id === agent.id ? { ...a, database: null } : a)));
      setDbEditingId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao remover o banco");
    }
  }

  async function toggleCapability(agent: Agent, key: "web_search" | "sql" | "memory") {
    if (!userToken) return;
    const next = !agent[key];
    try {
      const updated = await api.updateAgent(userToken, agent.id, { [key]: next });
      setAgents((prev) => prev.map((a) => (a.id === agent.id ? updated : a)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao alterar a capacidade");
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col p-6">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Seus agentes</h1>
          <p className="text-xs text-slate-500">{email}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowSkills(true)}
            className="rounded-lg border border-indigo-700 bg-indigo-950/40 px-3 py-1.5 text-xs text-indigo-200 hover:bg-indigo-900/50"
          >
            Skills
          </button>
          <button
            onClick={logout}
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs hover:bg-slate-800"
          >
            Sair
          </button>
        </div>
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
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-medium">{agent.name}</p>
                    {/* Soft nudge: an agent with no folder, no database and no web search can only
                        chat — surface it so it isn't a silent surprise. Folder stays optional. */}
                    {!agent.folder && !agent.database && !agent.web_search && (
                      <span className="shrink-0 rounded-full bg-amber-950/60 px-2 py-0.5 text-[10px] text-amber-300 ring-1 ring-inset ring-amber-800/50">
                        sem fontes
                      </span>
                    )}
                  </div>
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
                  <div className="flex flex-1 flex-wrap items-center gap-2">
                    <input
                      ref={folderInputRef}
                      type="file"
                      /* @ts-expect-error webkitdirectory has no TS typing but is supported by every major browser */
                      webkitdirectory=""
                      multiple
                      onChange={(e) => void handleFolderPicked(e, agent)}
                      className="hidden"
                    />
                    <label
                      className="flex shrink-0 items-center gap-1 text-slate-400"
                      title="Permite que o agente crie/edite arquivos na pasta (confinado a ela). Desligado = somente leitura."
                    >
                      <input
                        type="checkbox"
                        checked={folderWritable}
                        onChange={(e) => setFolderWritable(e.target.checked)}
                        className="accent-indigo-600"
                      />
                      gravável
                    </label>
                    <button
                      onClick={() => folderInputRef.current?.click()}
                      disabled={uploadingFolder}
                      className="rounded border border-indigo-700 bg-indigo-950/40 px-2 py-1 text-indigo-200 hover:bg-indigo-900/50 disabled:opacity-50"
                    >
                      {uploadingFolder ? "Enviando…" : "Selecionar pasta…"}
                    </button>
                    <button
                      onClick={() => setEditingId(null)}
                      className="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800"
                    >
                      Cancelar
                    </button>
                    <details className="w-full">
                      <summary className="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300">
                        Avançado: caminho no servidor
                      </summary>
                      <div className="mt-2 flex items-center gap-2">
                        <input
                          value={folderInput}
                          onChange={(e) => setFolderInput(e.target.value)}
                          placeholder="Caminho da pasta (deve estar em SANDBOX_ALLOWED_ROOTS)"
                          className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 outline-none focus:border-indigo-600"
                        />
                        <button
                          onClick={() => void saveFolder(agent)}
                          className="rounded border border-indigo-700 bg-indigo-950/40 px-2 py-1 text-indigo-200 hover:bg-indigo-900/50"
                        >
                          Salvar
                        </button>
                      </div>
                    </details>
                  </div>
                ) : (
                  <>
                    <span className="min-w-0 flex-1 truncate text-slate-400">
                      {agent.folder ? agent.folder : "nenhuma pasta vinculada"}
                    </span>
                    {agent.folder && (
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${
                          agent.folder_writable
                            ? "bg-amber-950/60 text-amber-300"
                            : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {agent.folder_writable ? "gravável" : "somente leitura"}
                      </span>
                    )}
                    <button
                      onClick={() => startEditFolder(agent)}
                      className="rounded border border-slate-700 px-2 py-1 text-slate-400 hover:bg-slate-800"
                    >
                      {agent.folder ? "Alterar" : "Vincular"}
                    </button>
                  </>
                )}
              </div>

              <div className="mt-2 text-xs">
                {dbEditingId === agent.id ? (
                  <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/40 p-2">
                    <div className="grid grid-cols-2 gap-2">
                      <select
                        value={dbForm.driver}
                        onChange={(e) => setDbForm({ ...dbForm, driver: e.target.value })}
                        className="rounded border border-slate-700 bg-slate-900 px-2 py-1"
                      >
                        <option value="postgresql">PostgreSQL</option>
                        <option value="mysql+pymysql">MySQL</option>
                      </select>
                      <input
                        value={dbForm.port}
                        onChange={(e) => setDbForm({ ...dbForm, port: e.target.value })}
                        placeholder="Porta"
                        className="rounded border border-slate-700 bg-slate-900 px-2 py-1"
                      />
                      <input
                        value={dbForm.host}
                        onChange={(e) => setDbForm({ ...dbForm, host: e.target.value })}
                        placeholder="Host"
                        className="col-span-2 rounded border border-slate-700 bg-slate-900 px-2 py-1"
                      />
                      <input
                        value={dbForm.database}
                        onChange={(e) => setDbForm({ ...dbForm, database: e.target.value })}
                        placeholder="Banco"
                        className="rounded border border-slate-700 bg-slate-900 px-2 py-1"
                      />
                      <input
                        value={dbForm.username}
                        onChange={(e) => setDbForm({ ...dbForm, username: e.target.value })}
                        placeholder="Usuário"
                        className="rounded border border-slate-700 bg-slate-900 px-2 py-1"
                      />
                      <input
                        type="password"
                        value={dbForm.password}
                        onChange={(e) => setDbForm({ ...dbForm, password: e.target.value })}
                        placeholder="Senha"
                        className="col-span-2 rounded border border-slate-700 bg-slate-900 px-2 py-1"
                      />
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => void saveDb(agent)}
                        className="rounded border border-indigo-700 bg-indigo-950/40 px-2 py-1 text-indigo-200 hover:bg-indigo-900/50"
                      >
                        Salvar
                      </button>
                      {agent.database && (
                        <button
                          onClick={() => void removeDb(agent)}
                          className="rounded border border-slate-700 px-2 py-1 text-slate-400 hover:bg-slate-800"
                        >
                          Remover
                        </button>
                      )}
                      <button
                        onClick={() => setDbEditingId(null)}
                        className="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800"
                      >
                        Cancelar
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <span className="text-slate-500">🛢️</span>
                    <span className="min-w-0 flex-1 truncate text-slate-400">
                      {agent.database
                        ? `${agent.database.database} @ ${agent.database.host}${agent.database.password_persisted ? "" : " (senha não salva)"}`
                        : "nenhum banco vinculado"}
                    </span>
                    <button
                      onClick={() => startEditDb(agent)}
                      className="rounded border border-slate-700 px-2 py-1 text-slate-400 hover:bg-slate-800"
                    >
                      {agent.database ? "Alterar" : "Vincular"}
                    </button>
                  </div>
                )}
              </div>

              <div className="mt-2 flex gap-2 text-xs">
                <button
                  onClick={() => void toggleCapability(agent, "web_search")}
                  className={`rounded-full px-2 py-1 ${agent.web_search ? "bg-emerald-900 text-emerald-200" : "bg-slate-800 text-slate-500"}`}
                >
                  🌐 web {agent.web_search ? "on" : "off"}
                </button>
                <button
                  onClick={() => void toggleCapability(agent, "sql")}
                  className={`rounded-full px-2 py-1 ${agent.sql ? "bg-emerald-900 text-emerald-200" : "bg-slate-800 text-slate-500"}`}
                >
                  🗄️ sql {agent.sql ? "on" : "off"}
                </button>
                <button
                  onClick={() => void toggleCapability(agent, "memory")}
                  className={`rounded-full px-2 py-1 ${agent.memory ? "bg-emerald-900 text-emerald-200" : "bg-slate-800 text-slate-500"}`}
                >
                  🧠 memória {agent.memory ? "on" : "off"}
                </button>
              </div>

              <div className="mt-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className="text-slate-500">📚</span>
                  <span className="min-w-0 flex-1 truncate text-slate-400">
                    {(agent.skills?.length ?? 0) > 0 ? `${agent.skills.length} skill(s) anexada(s)` : "nenhuma skill anexada"}
                  </span>
                  <button
                    onClick={() => setSkillEditingId(skillEditingId === agent.id ? null : agent.id)}
                    className="rounded border border-slate-700 px-2 py-1 text-slate-400 hover:bg-slate-800"
                  >
                    {skillEditingId === agent.id ? "Fechar" : "Anexar"}
                  </button>
                </div>
                {skillEditingId === agent.id && (
                  <div className="mt-2 space-y-1 rounded-lg border border-slate-800 bg-slate-950/40 p-2">
                    {library.length === 0 ? (
                      <p className="text-slate-500">
                        Nenhuma skill na biblioteca. Crie uma em <strong>Skills</strong> no topo.
                      </p>
                    ) : (
                      library.map((s) => (
                        <label key={s.id} className="flex items-center gap-2 text-slate-300">
                          <input
                            type="checkbox"
                            checked={(agent.skills ?? []).includes(s.id)}
                            onChange={() => void toggleAttachedSkill(agent, s.id)}
                          />
                          <span className="truncate">{s.name}</span>
                        </label>
                      ))
                    )}
                  </div>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {showSkills && (
        <Suspense fallback={null}>
          <SkillsPanel
            onClose={() => {
              setShowSkills(false);
              void reloadLibrary();
            }}
          />
        </Suspense>
      )}

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
            <div className="flex flex-wrap gap-4 text-sm text-slate-300">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={webSearch} onChange={(e) => setWebSearch(e.target.checked)} />
                🌐 Pesquisa na web
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={sqlEnabled} onChange={(e) => setSqlEnabled(e.target.checked)} />
                🗄️ Banco de dados
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={memory} onChange={(e) => setMemory(e.target.checked)} />
                🧠 Memória
              </label>
            </div>
            {/* Non-blocking: creating a source-less agent is valid (pure chat), just easy to do by
                accident. A database can still be bound after creation, from the agent's card. */}
            {!folder.trim() && !webSearch && (
              <p className="rounded-lg border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs text-amber-300/90">
                Sem fontes: este agente só conversa. Conecte uma pasta acima, ligue a busca na web, ou
                vincule um banco depois de criar.
              </p>
            )}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={busy || !name.trim()}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-[#000814] transition hover:bg-indigo-500 hover:shadow-[0_0_18px_rgba(0,194,224,0.55)] disabled:opacity-50 disabled:shadow-none"
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
