import { useState } from "react";
import * as api from "../lib/api";
import type { Approval } from "../lib/types";

/**
 * Inline approval for a confirmation-gated action (e.g. generating a document). The agent parks the
 * action; the user approves or rejects it right here in the conversation. Once approved, the card
 * offers a download of the produced artifact — no drawer, no context switch.
 */
export default function ApprovalCard({
  approval,
  userToken,
  sessionToken,
  sessionId,
  onDecided,
}: {
  approval: Approval;
  userToken: string;
  sessionToken: string;
  sessionId: string;
  onDecided: (id: number, status: "approved" | "rejected", error?: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const fmt = approval.format?.toUpperCase();

  async function approve() {
    setBusy(true);
    try {
      await api.confirmAction(userToken, approval.id);
      onDecided(approval.id, "approved");
    } catch (err) {
      onDecided(approval.id, "approved", err instanceof Error ? err.message : "Falha ao aprovar.");
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    setBusy(true);
    try {
      await api.rejectAction(userToken, approval.id);
      onDecided(approval.id, "rejected");
    } catch (err) {
      onDecided(approval.id, "rejected", err instanceof Error ? err.message : "Falha ao recusar.");
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    setDownloading(true);
    setDownloadError(null);
    try {
      const { blob, filename } = await api.downloadArtifact(sessionToken, sessionId, approval.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Falha ao baixar.");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="rounded-xl border border-amber-800/60 bg-amber-950/20 p-3">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 text-base">📄</span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-amber-100">
            {approval.status === "pending" ? "Aprovar geração de artefato?" : approval.title}
          </p>
          <p className="mt-0.5 truncate text-xs text-amber-200/70">
            {approval.title}
            {fmt ? ` · ${fmt}` : ""}
          </p>

          {approval.status === "pending" && (
            <div className="mt-2 flex gap-2">
              <button
                onClick={() => void approve()}
                disabled={busy}
                className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {busy ? "…" : "Aprovar"}
              </button>
              <button
                onClick={() => void reject()}
                disabled={busy}
                className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-50"
              >
                Recusar
              </button>
            </div>
          )}

          {approval.status === "approved" && (
            <div className="mt-2">
              {approval.error ? (
                <p className="text-xs text-red-300">{approval.error}</p>
              ) : (
                <>
                  <button
                    onClick={() => void download()}
                    disabled={downloading}
                    className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
                  >
                    {downloading ? "Baixando…" : "⬇ Baixar artefato"}
                  </button>
                  {downloadError && (
                    <p className="mt-1 text-xs text-red-300">
                      {downloadError}{" "}
                      <button onClick={() => void download()} className="underline hover:text-red-200">
                        Tentar de novo
                      </button>
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          {approval.status === "rejected" && <p className="mt-2 text-xs text-slate-500">Recusado.</p>}
        </div>
      </div>
    </div>
  );
}
