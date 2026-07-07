import { useState } from "react";
import type { ToolStep } from "../lib/types";
import { labelFor } from "../lib/toolLabels";
import Markdown, { looksLikeMarkdown } from "./Markdown";

function StepCard({ step }: { step: ToolStep }) {
  const [open, setOpen] = useState(false);
  const { icon, label } = labelFor(step.name);
  const hasDetail = Boolean(step.input || step.output);

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 text-xs">
      <button
        onClick={() => hasDetail && setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span>{icon}</span>
        <span className="font-medium text-slate-200">{label}</span>
        <span className="ml-auto flex items-center gap-2">
          {step.done ? (
            <span className="text-emerald-400">✓</span>
          ) : (
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-600 border-t-indigo-400" />
          )}
          {hasDetail && <span className="text-slate-500">{open ? "▾" : "▸"}</span>}
        </span>
      </button>
      {open && (
        <div className="space-y-1 border-t border-slate-800 px-3 py-2">
          {/* Input is the query/path/command — keep it verbatim in monospace. */}
          {step.input && (
            <pre className="overflow-x-auto rounded bg-slate-950 p-2 text-[11px] text-slate-300">
              {step.input}
            </pre>
          )}
          {/* Output: render markdown only when it looks like markdown; otherwise keep it
              verbatim in monospace so aligned/tabular dumps (SQL, CSV, JSON) stay readable. */}
          {step.output &&
            (looksLikeMarkdown(step.output) ? (
              <div className="max-h-56 overflow-auto rounded bg-slate-950 p-2 text-[11px] text-slate-300">
                <Markdown>{step.output}</Markdown>
              </div>
            ) : (
              <pre className="max-h-56 overflow-auto rounded bg-slate-950 p-2 text-[11px] text-slate-400">
                {step.output}
              </pre>
            ))}
        </div>
      )}
    </div>
  );
}

export default function AgentActivity({ steps }: { steps: ToolStep[] }) {
  if (steps.length === 0) return null;
  return (
    <div className="mb-2 space-y-1.5">
      {steps.map((step) => (
        <StepCard key={step.id} step={step} />
      ))}
    </div>
  );
}
