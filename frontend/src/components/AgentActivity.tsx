import { useState } from "react";
import type { ToolStep } from "../lib/types";
import Markdown from "./Markdown";

const TOOL_LABELS: Record<string, { icon: string; label: string }> = {
  list_tables: { icon: "🗂️", label: "Listando tabelas" },
  describe_tables: { icon: "🔎", label: "Lendo o schema" },
  run_sql: { icon: "🛢️", label: "Executando SQL" },
  ls: { icon: "📁", label: "Listando arquivos" },
  read_file: { icon: "📄", label: "Lendo arquivo" },
  glob: { icon: "🔦", label: "Procurando arquivos" },
  grep: { icon: "🔍", label: "Buscando no conteúdo" },
  write_todos: { icon: "🧠", label: "Planejando" },
};

function labelFor(name: string): { icon: string; label: string } {
  return TOOL_LABELS[name] ?? { icon: "🔧", label: name };
}

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
          {/* Output can be a markdown file, a table, or prose — render it. */}
          {step.output && (
            <div className="max-h-56 overflow-auto rounded bg-slate-950 p-2 text-[11px] text-slate-300">
              <Markdown>{step.output}</Markdown>
            </div>
          )}
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
