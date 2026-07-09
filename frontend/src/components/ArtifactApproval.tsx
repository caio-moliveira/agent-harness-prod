import { useState } from "react";
import * as api from "../lib/api";
import type { TurnApproval } from "../lib/types";

/**
 * Compact, inline approval anchored under the assistant turn that requested it. Approving generates
 * the artifact server-side (which also records a short "artifact generated" note in the chat) and
 * collapses to a one-line confirmation — no big card, no download button, and it stays in place as
 * the conversation continues.
 */
export default function ArtifactApproval({
  approval,
  userToken,
  onDecided,
  onApprovedResume,
}: {
  approval: TurnApproval;
  userToken: string;
  onDecided: (status: "approved" | "rejected") => void;
  /** Called after a plan is approved, so the caller can resume the agent (auto-send "proceed"). */
  onApprovedResume?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isPlan = approval.action_type === "approve_plan";
  const fmt = approval.format?.toUpperCase();
  const label = `“${approval.title}”${fmt ? ` (${fmt})` : ""}`;

  if (approval.status === "approved") {
    return (
      <p className="mt-1 text-xs text-emerald-400">
        {isPlan ? `✅ Plano ${label} aprovado.` : `📄 Artefato ${label} gerado.`}
      </p>
    );
  }
  if (approval.status === "rejected") {
    return <p className="mt-1 text-xs text-slate-500">{isPlan ? "Plano recusado." : "Geração recusada."}</p>;
  }

  async function decide(approve: boolean) {
    setBusy(true);
    setError(null);
    try {
      if (approve) await api.confirmAction(userToken, approval.id);
      else await api.rejectAction(userToken, approval.id);
      onDecided(approve ? "approved" : "rejected");
      if (approve && isPlan) onApprovedResume?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao processar.");
      setBusy(false);
    }
  }

  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
      <span>{isPlan ? `Executar o plano ${label}?` : `Gerar o artefato ${label}?`}</span>
      <button
        onClick={() => void decide(true)}
        disabled={busy}
        className="rounded-md bg-emerald-700 px-2.5 py-1 font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
      >
        {busy ? "…" : "Aprovar"}
      </button>
      <button
        onClick={() => void decide(false)}
        disabled={busy}
        className="rounded-md border border-slate-600 px-2.5 py-1 text-slate-200 hover:bg-slate-800 disabled:opacity-50"
      >
        Recusar
      </button>
      {error && <span className="text-red-300">{error}</span>}
    </div>
  );
}
