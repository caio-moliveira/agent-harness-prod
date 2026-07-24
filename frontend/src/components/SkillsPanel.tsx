import { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";
import type { RegistrySkill, Skill } from "../lib/types";

const STATUS_BADGES: Record<string, { label: string; className: string }> = {
  draft: { label: "Rascunho", className: "bg-slate-800 text-slate-400" },
  in_review: {
    label: "Em revisão",
    className: "bg-amber-950/60 text-amber-300 ring-1 ring-inset ring-amber-800/50",
  },
  approved: { label: "Aprovada", className: "bg-emerald-900 text-emerald-200" },
};

/** Slide-over panel to author and manage the user's skill library. */
export default function SkillsPanel({ onClose }: { onClose: () => void }) {
  const { userToken } = useAuth();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [approvingId, setApprovingId] = useState<number | null>(null);
  const [registry, setRegistry] = useState<RegistrySkill[] | null>(null);

  async function refresh() {
    if (!userToken) return;
    try {
      setSkills(await api.listSkills(userToken));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao carregar skills");
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
      await api.createSkill(userToken, { name: name.trim(), description: description.trim(), body });
      setName("");
      setDescription("");
      setBody("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao criar skill");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(skill: Skill) {
    if (!userToken) return;
    try {
      await api.deleteSkill(userToken, skill.id);
      setSkills((prev) => prev.filter((s) => s.id !== skill.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao excluir skill");
    }
  }

  async function handleApprove(skill: Skill) {
    if (!userToken || skill.status === "approved" || approvingId !== null) return;
    setApprovingId(skill.id);
    try {
      let updated = skill;
      if (updated.status === "draft") {
        updated = await api.updateSkillStatus(userToken, skill.id, "in_review");
      }
      if (updated.status === "in_review") {
        updated = await api.updateSkillStatus(userToken, skill.id, "approved");
      }
      setSkills((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao aprovar skill");
    } finally {
      setApprovingId(null);
    }
  }

  async function loadRegistry() {
    if (!userToken) return;
    try {
      setRegistry(await api.listRegistry(userToken));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registro indisponível (configure SKILL_REGISTRY_URL).");
    }
  }

  async function importSkill(slug: string) {
    if (!userToken) return;
    try {
      await api.fetchSkill(userToken, slug);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao importar skill");
    }
  }

  return (
    <div className="fixed inset-y-0 right-0 z-20 flex w-full max-w-md flex-col border-l border-slate-800 bg-slate-950 p-5 shadow-xl">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Biblioteca de skills</h2>
        <button onClick={onClose} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs hover:bg-slate-800">
          Fechar
        </button>
      </div>

      {error && (
        <div className="mb-3 rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="flex-1 space-y-2 overflow-y-auto">
        {skills.length === 0 ? (
          <p className="text-sm text-slate-500">Nenhuma skill ainda. Crie uma abaixo.</p>
        ) : (
          skills.map((s) => {
            const badge = STATUS_BADGES[s.status] ?? STATUS_BADGES.draft;
            return (
              <div key={s.id} className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-sm font-medium">{s.name}</p>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <span className={`rounded-full px-2 py-0.5 text-[10px] ${badge.className}`}>{badge.label}</span>
                    <button
                      onClick={() => void handleDelete(s)}
                      className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:bg-slate-800"
                    >
                      Excluir
                    </button>
                  </div>
                </div>
                <div className="mt-1 flex items-center justify-between gap-2">
                  <p className="truncate text-xs text-slate-500">{s.description || "sem descrição"}</p>
                  {s.status !== "approved" && (
                    <button
                      onClick={() => void handleApprove(s)}
                      disabled={approvingId === s.id}
                      className="shrink-0 rounded border border-emerald-700 bg-emerald-950/40 px-2 py-1 text-xs text-emerald-200 hover:bg-emerald-900/50 disabled:opacity-50"
                    >
                      {approvingId === s.id ? "Aprovando…" : "Aprovar"}
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      <div className="mt-3 border-t border-slate-800 pt-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-medium text-slate-400">Registro confiável</span>
          <button
            onClick={() => void loadRegistry()}
            className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800"
          >
            Buscar
          </button>
        </div>
        {registry !== null && (
          <div className="max-h-40 space-y-1 overflow-y-auto">
            {registry.length === 0 ? (
              <p className="text-xs text-slate-500">Nenhuma skill no registro.</p>
            ) : (
              registry.map((r) => (
                <div
                  key={r.slug}
                  className="flex items-center justify-between gap-2 rounded border border-slate-800 px-2 py-1"
                >
                  <span className="min-w-0 truncate text-xs text-slate-300">{r.name || r.slug}</span>
                  <button
                    onClick={() => void importSkill(r.slug)}
                    className="rounded border border-indigo-700 bg-indigo-950/40 px-2 py-1 text-xs text-indigo-200 hover:bg-indigo-900/50"
                  >
                    Importar
                  </button>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      <form onSubmit={handleCreate} className="mt-4 space-y-2 border-t border-slate-800 pt-4">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Nome da skill"
          className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-600"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Descrição (quando usar)"
          className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-600"
        />
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Instruções (o corpo da skill, em markdown)"
          rows={5}
          className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-600"
        />
        <button
          type="submit"
          disabled={busy || !name.trim()}
          className="w-full rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-[#000814] transition hover:bg-indigo-500 hover:shadow-[0_0_18px_rgba(0,194,224,0.55)] disabled:opacity-50 disabled:shadow-none"
        >
          Criar skill
        </button>
      </form>
    </div>
  );
}
