import { memo, useState } from "react";
import * as api from "../lib/api";
import type { ToolStep } from "../lib/types";

const WRITE_TOOLS = new Set(["write_file", "edit_file"]);

/** Extract the /workspace file path from a write_file/edit_file tool step's input, or null. */
function writePath(step: ToolStep): string | null {
  if (!WRITE_TOOLS.has(step.name) || !step.input) return null;
  const match = /file_path['"]?\s*[:=]\s*['"]([^'"]+)['"]/.exec(step.input);
  const path = match?.[1];
  return path && path.includes("/workspace") ? path : null;
}

/**
 * Persistent download chips for files the agent wrote to /workspace this turn. A written deliverable
 * (a plan/report `.md`, a `.csv`, …) is otherwise only findable on disk — this keeps the *result*
 * reachable in-app, right under the turn that produced it. Confined server-side to the granted folder.
 */
function DeliverableLinks({
  steps,
  sessionToken,
  sessionId,
}: {
  steps: ToolStep[];
  sessionToken: string | null;
  sessionId: string | null;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const paths = Array.from(new Set(steps.map(writePath).filter((p): p is string => Boolean(p))));
  if (paths.length === 0 || !sessionToken || !sessionId) return null;

  async function download(path: string) {
    setBusy(path);
    setError(null);
    try {
      const { blob, filename } = await api.downloadWorkspaceFile(sessionToken!, sessionId!, path);
      api.saveBlob(blob, filename);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao baixar.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="ml-[42px] mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-300">
      {paths.map((path) => {
        const name = path.split("/").pop() || path;
        return (
          <button
            key={path}
            onClick={() => void download(path)}
            disabled={busy === path}
            className="inline-flex items-center gap-1 rounded-md border border-slate-600 bg-slate-800/60 px-2.5 py-1 hover:bg-slate-700 disabled:opacity-50"
            title={`Baixar ${name}`}
          >
            <span aria-hidden>📄</span>
            <span>{busy === path ? "Baixando…" : name}</span>
          </button>
        );
      })}
      {error && <span className="text-red-300">{error}</span>}
    </div>
  );
}

// `steps` keeps a stable array reference for any turn other than the one currently streaming.
export default memo(DeliverableLinks);
