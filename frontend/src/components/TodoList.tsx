import type { TodoItem } from "../lib/types";

/** Status glyph for a plan item — mirrors the tool-step visual language (spinner / check). */
function StatusMark({ status }: { status: TodoItem["status"] }) {
  if (status === "completed") return <span className="text-emerald-400">✓</span>;
  if (status === "in_progress")
    return <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-600 border-t-indigo-400" />;
  return <span className="h-2.5 w-2.5 rounded-full border border-slate-600" />;
}

/**
 * The agent's live plan, from the `write_todos` tool — a checklist that updates as tasks move from
 * pending → in progress → done, instead of showing the raw JSON in a tool step.
 */
export default function TodoList({ items }: { items: TodoItem[] }) {
  if (items.length === 0) return null;
  const done = items.filter((t) => t.status === "completed").length;

  return (
    <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/60 text-xs">
      <div className="flex items-center gap-2 border-b border-slate-800 px-3 py-2">
        <span>🧠</span>
        <span className="font-medium text-slate-200">Plano</span>
        <span className="ml-auto text-slate-500">
          {done}/{items.length}
        </span>
      </div>
      <ul className="space-y-1.5 px-3 py-2">
        {items.map((item, i) => (
          <li key={i} className="flex items-center gap-2">
            <span className="grid w-3 place-items-center">
              <StatusMark status={item.status} />
            </span>
            <span
              className={
                item.status === "completed" ? "text-slate-500 line-through" : "text-slate-300"
              }
            >
              {item.content}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
