import { useEffect, useState } from "react";
import * as api from "../lib/api";
import type { PendingAction } from "../lib/api";

/** Drawer that lists confirmation-gated actions (#19) and lets the owner confirm or reject them. */
export default function PendingActionsPanel({
  userToken,
  onClose,
  onChanged,
}: {
  userToken: string;
  onClose: () => void;
  onChanged: (count: number) => void;
}) {
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function refresh() {
    try {
      const list = await api.listPendingActions(userToken);
      setActions(list);
      onChanged(list.length);
    } catch {
      /* ignore transient errors */
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userToken]);

  async function handleConfirm(a: PendingAction) {
    setBusyId(a.id);
    setNotice(null);
    try {
      const res = await api.confirmAction(userToken, a.id);
      const path = (res.result as { path?: string } | null)?.path;
      setNotice(path ? `Gerado: ${path}` : "Ação confirmada.");
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Falha ao confirmar.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(a: PendingAction) {
    setBusyId(a.id);
    setNotice(null);
    try {
      await api.rejectAction(userToken, a.id);
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Falha ao rejeitar.");
    } finally {
      setBusyId(null);
    }
  }

  function describe(a: PendingAction): { title: string; detail: string } {
    const spec = a.payload?.spec as { title?: string } | undefined;
    const fmt = a.payload?.fmt as string | undefined;
    const path = a.payload?.path as string | undefined;
    if (a.action_type === "export_artifact") {
      return {
        title: spec?.title ? `Gerar artefato: ${spec.title}` : "Gerar artefato",
        detail: [fmt?.toUpperCase(), path].filter(Boolean).join(" · "),
      };
    }
    return { title: a.action_type, detail: path ?? "" };
  }

  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-slate-800 bg-slate-900 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">Ações pendentes</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
            ✕
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Ações de saída (como gerar um documento) esperam sua confirmação antes de acontecer.
        </p>

        {notice && (
          <div className="mt-3 break-all rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-300">
            {notice}
          </div>
        )}

        <div className="mt-4 space-y-2">
          {actions.length === 0 ? (
            <p className="rounded-lg bg-slate-950 px-3 py-6 text-center text-sm text-slate-500">
              Nada aguardando confirmação.
            </p>
          ) : (
            actions.map((a) => {
              const { title, detail } = describe(a);
              return (
                <div key={a.id} className="rounded-xl border border-slate-800 p-3">
                  <p className="text-sm font-medium">{title}</p>
                  {detail && <p className="mt-0.5 break-all text-[11px] text-slate-500">{detail}</p>}
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={() => void handleConfirm(a)}
                      disabled={busyId === a.id}
                      className="rounded-lg bg-emerald-700 px-3 py-1.5 text-xs font-medium hover:bg-emerald-600 disabled:opacity-50"
                    >
                      {busyId === a.id ? "…" : "Confirmar"}
                    </button>
                    <button
                      onClick={() => void handleReject(a)}
                      disabled={busyId === a.id}
                      className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                    >
                      Rejeitar
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </aside>
    </div>
  );
}
