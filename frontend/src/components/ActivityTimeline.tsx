import { useState } from "react";
import type { ToolStep } from "../lib/types";
import { labelFor } from "../lib/toolLabels";
import { IconClose, IconLayers } from "./icons";

function TimelineRow({ step }: { step: ToolStep }) {
  const [open, setOpen] = useState(false);
  const { icon, label } = labelFor(step.name);
  // The plan (`write_todos`) is a checklist, not a payload — keep its "Planejando" row but hide the
  // todos JSON so the timeline doesn't show a raw blob (the plan renders visually as <TodoList>).
  const isPlan = step.name === "write_todos";
  const body = isPlan ? "" : [step.input, step.output].filter(Boolean).join("\n\n");

  return (
    <li className="relative pl-6">
      {/* Rail dot */}
      <span
        className={`absolute left-1.5 top-1.5 h-2 w-2 rounded-full ${step.done ? "bg-emerald-400" : "bg-indigo-400"}`}
      />
      <button onClick={() => body && setOpen((o) => !o)} className="flex w-full items-start gap-1.5 text-left">
        <span className="text-xs">{icon}</span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1 text-xs font-medium text-slate-200">
            {label}
            {!step.done && (
              <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-slate-600 border-t-indigo-400" />
            )}
          </span>
          {step.input && !isPlan && (
            <span className="block truncate text-[10px] text-slate-500">{step.input}</span>
          )}
        </span>
        {body && <span className="text-[10px] text-slate-600">{open ? "▾" : "▸"}</span>}
      </button>
      {open && body && (
        <pre className="mt-1 max-h-40 overflow-auto rounded bg-slate-950 p-2 text-[10px] text-slate-400">{body}</pre>
      )}
    </li>
  );
}

/**
 * Right rail — a chronological log of everything the agent did this session. Fed by the turns' tool
 * steps, which are persisted per turn, so reopening a past conversation restores its full timeline.
 */
export default function ActivityTimeline({ steps, onClose }: { steps: ToolStep[]; onClose: () => void }) {
  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-l border-slate-800 bg-slate-900/40">
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <IconLayers className="h-4 w-4 text-indigo-300" />
          Linha do tempo
        </h2>
        <button
          onClick={onClose}
          title="Ocultar"
          className="grid h-7 w-7 place-items-center rounded-lg text-slate-500 hover:bg-slate-800 hover:text-slate-200"
        >
          <IconClose className="h-4 w-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {steps.length === 0 ? (
          <p className="mt-6 text-center text-xs text-slate-500">
            As ações do agente (consultas, leitura de arquivos, artefatos) aparecem aqui.
          </p>
        ) : (
          <ul className="space-y-3 border-l border-slate-800">
            {steps.map((step) => (
              <TimelineRow key={step.id} step={step} />
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
