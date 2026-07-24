import { useState } from "react";
import * as api from "../lib/api";
import type { ArtifactPreview, ArtifactSource, TurnApproval } from "../lib/types";

/** Compact, human-readable attribution line — mirrors the backend's Source.render(). */
function renderSource(source: ArtifactSource): string {
  if (source.kind === "query") {
    const tables = source.tables?.length ? source.tables.join(", ") : "(desconhecida)";
    return `tabela(s): ${tables} | consulta: ${source.query ?? "?"}`;
  }
  const loc = source.section ? ` (${source.section})` : "";
  return `documento: ${source.document ?? "?"}${loc}`;
}

/**
 * Compact, inline approval anchored under the assistant turn that requested it. Approving generates
 * the artifact server-side and collapses to a one-line confirmation that stays in place — and, for a
 * generated artifact, offers a **Baixar** action (the "go to result"), so the deliverable is
 * reachable right where it was produced. Plans have no download (they resume the agent instead).
 */
export default function ArtifactApproval({
  approval,
  userToken,
  sessionToken,
  sessionId,
  onDecided,
  onApprovedResume,
}: {
  approval: TurnApproval;
  userToken: string;
  sessionToken: string | null;
  sessionId: string | null;
  onDecided: (status: "approved" | "rejected") => void;
  /** Called after a plan is approved, so the caller can resume the agent (auto-send "proceed"). */
  onApprovedResume?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const isPlan = approval.action_type === "approve_plan";
  const fmt = approval.format?.toUpperCase();
  const label = `“${approval.title}”${fmt ? ` (${fmt})` : ""}`;

  async function togglePreview() {
    const next = !showPreview;
    setShowPreview(next);
    if (next && !preview && !previewLoading) {
      setPreviewLoading(true);
      setPreviewError(null);
      try {
        setPreview(await api.previewArtifact(userToken, approval.id));
      } catch (err) {
        setPreviewError(err instanceof Error ? err.message : "Falha ao carregar o preview.");
      } finally {
        setPreviewLoading(false);
      }
    }
  }

  function renderPreviewBody() {
    if (previewLoading) return <p className="text-slate-500">Carregando…</p>;
    if (previewError) return <p className="text-red-300">{previewError}</p>;
    if (!preview?.payload.spec) return null;
    const { spec } = preview.payload;
    if (preview.payload.kind === "spreadsheet" && "sheets" in spec) {
      return (
        <div className="space-y-2">
          {spec.sheets.map((sheet, i) => (
            <div key={i}>
              <p className="font-medium text-slate-300">{sheet.name}</p>
              <p className="text-slate-500">
                {sheet.columns.join(", ")} · {sheet.rows.length} linha(s)
              </p>
            </div>
          ))}
        </div>
      );
    }
    if ("sections" in spec) {
      return (
        <div className="space-y-2">
          {spec.sections.map((section, i) => (
            <div key={i}>
              <p className="font-medium text-slate-300">{section.heading}</p>
              <ul className="ml-3 list-disc space-y-0.5">
                {section.claims.map((claim, j) => (
                  <li key={j}>
                    {claim.text}{" "}
                    {claim.source ? (
                      <span className="text-slate-500">[{renderSource(claim.source)}]</span>
                    ) : (
                      <span className="rounded bg-amber-900/60 px-1 text-amber-300">[SEM FONTE]</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      );
    }
    return null;
  }

  async function download() {
    if (!sessionToken || !sessionId) return;
    setDownloading(true);
    setError(null);
    try {
      const { blob, filename } = await api.downloadArtifact(sessionToken, sessionId, approval.id);
      api.saveBlob(blob, filename);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao baixar.");
    } finally {
      setDownloading(false);
    }
  }

  if (approval.status === "approved") {
    if (isPlan) {
      return <p className="mt-1 text-xs text-emerald-400">{`✅ Plano ${label} aprovado.`}</p>;
    }
    return (
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-emerald-400">
        <span>{`📄 Artefato ${label} gerado.`}</span>
        <button
          onClick={() => void download()}
          disabled={downloading || !sessionToken}
          className="rounded-md border border-emerald-700 px-2.5 py-1 font-medium text-emerald-200 hover:bg-emerald-900/40 disabled:opacity-50"
        >
          {downloading ? "Baixando…" : "Baixar"}
        </button>
        {error && <span className="text-red-300">{error}</span>}
      </div>
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
    <div className="mt-1 text-xs text-slate-400">
      <div className="flex flex-wrap items-center gap-2">
        <span>{isPlan ? `Executar o plano ${label}?` : `Gerar o artefato ${label}?`}</span>
        {!isPlan && (
          <button
            onClick={() => void togglePreview()}
            className="rounded-md border border-slate-700 px-2.5 py-1 text-slate-300 hover:bg-slate-800"
          >
            {showPreview ? "Ocultar detalhes" : "Ver detalhes"}
          </button>
        )}
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
      {!isPlan && showPreview && (
        <div className="mt-2 rounded-md border border-slate-700 bg-slate-900/60 p-2">{renderPreviewBody()}</div>
      )}
    </div>
  );
}
