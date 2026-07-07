import { useState } from "react";
import type { SessionEvent, ToolStep } from "../lib/types";

/** Live tool names (streamed this session) mapped to a friendly icon + label. */
const TOOL_LABELS: Record<string, { icon: string; label: string }> = {
  list_tables: { icon: "🗂️", label: "Listou tabelas" },
  describe_tables: { icon: "🔎", label: "Leu o schema" },
  run_sql: { icon: "🛢️", label: "Executou SQL" },
  ls: { icon: "📁", label: "Listou arquivos" },
  read_file: { icon: "📄", label: "Leu arquivo" },
  glob: { icon: "🔦", label: "Procurou arquivos" },
  grep: { icon: "🔍", label: "Buscou no conteúdo" },
  write_todos: { icon: "🧠", label: "Planejou" },
  gerar_artefato: { icon: "📦", label: "Pediu artefato" },
};

/** Persisted audit event types mapped to a friendly icon + label. */
const EVENT_LABELS: Record<string, { icon: string; label: string }> = {
  query_executed: { icon: "🛢️", label: "Executou SQL" },
  document_read: { icon: "📄", label: "Leu documento" },
  skill_used: { icon: "🧠", label: "Usou skill" },
  artifact_generated: { icon: "📦", label: "Gerou artefato" },
};

interface Entry {
  key: string;
  icon: string;
  label: string;
  detail?: string;
  body?: string;
  done: boolean;
}

function truncate(s: string, n = 90): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

function entryFromEvent(e: SessionEvent): Entry {
  const meta = EVENT_LABELS[e.event_type] ?? { icon: "🔧", label: e.event_type };
  const input = (e.payload?.input as string) || (e.payload?.path as string) || (e.payload?.sql as string) || "";
  return {
    key: `e${e.id}`,
    icon: meta.icon,
    label: meta.label,
    detail: e.scope || (input ? truncate(input) : undefined),
    body: input || undefined,
    done: true,
  };
}

function entryFromStep(s: ToolStep): Entry {
  const meta = TOOL_LABELS[s.name] ?? { icon: "🔧", label: s.name };
  const body = [s.input, s.output].filter(Boolean).join("\n\n");
  return {
    key: `s${s.id}`,
    icon: meta.icon,
    label: meta.label,
    detail: s.input ? truncate(s.input) : undefined,
    body: body || undefined,
    done: s.done,
  };
}

function TimelineRow({ entry }: { entry: Entry }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="relative pl-6">
      {/* Rail dot */}
      <span
        className={`absolute left-1.5 top-1.5 h-2 w-2 rounded-full ${
          entry.done ? "bg-emerald-400" : "bg-indigo-400"
        }`}
      />
      <button
        onClick={() => entry.body && setOpen((o) => !o)}
        className="flex w-full items-start gap-1.5 text-left"
      >
        <span className="text-xs">{entry.icon}</span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1 text-xs font-medium text-slate-200">
            {entry.label}
            {!entry.done && (
              <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-slate-600 border-t-indigo-400" />
            )}
          </span>
          {entry.detail && <span className="block truncate text-[10px] text-slate-500">{entry.detail}</span>}
        </span>
        {entry.body && <span className="text-[10px] text-slate-600">{open ? "▾" : "▸"}</span>}
      </button>
      {open && entry.body && (
        <pre className="mt-1 max-h-40 overflow-auto rounded bg-slate-950 p-2 text-[10px] text-slate-400">
          {entry.body}
        </pre>
      )}
    </li>
  );
}

/**
 * Right rail — a chronological log of everything the agent did this session: persisted audit events
 * (restored when a past conversation is reopened) followed by the live tool activity of new turns.
 */
export default function ActivityTimeline({
  events,
  steps,
  onClose,
}: {
  events: SessionEvent[];
  steps: ToolStep[];
  onClose: () => void;
}) {
  // Persisted history (older) first, then live steps (now) — chronological by construction.
  const entries: Entry[] = [...events.map(entryFromEvent), ...steps.map(entryFromStep)];

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-l border-slate-800 bg-slate-950">
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-200">Linha do tempo</h2>
        <button onClick={onClose} title="Ocultar" className="text-slate-500 hover:text-slate-200">
          ✕
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {entries.length === 0 ? (
          <p className="mt-6 text-center text-xs text-slate-500">
            As ações do agente (SQL, leitura de arquivos, artefatos) aparecem aqui.
          </p>
        ) : (
          <ul className="space-y-3 border-l border-slate-800">
            {entries.map((entry) => (
              <TimelineRow key={entry.key} entry={entry} />
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
