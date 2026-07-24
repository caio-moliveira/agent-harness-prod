import { memo, useEffect, useState } from "react";

/**
 * Live "raciocínio" panel — the model's summarized reasoning (Anthropic adaptive thinking),
 * streamed token by token. Expanded while the model thinks, then auto-collapses once the answer
 * starts so it doesn't push the response down. Reasoning is live-only (not persisted in history).
 */
function ThinkingPanel({
  text,
  streaming,
  hasAnswer,
}: {
  text: string;
  streaming: boolean;
  hasAnswer: boolean;
}) {
  const [open, setOpen] = useState(true);
  // Collapse automatically once the answer begins.
  useEffect(() => {
    if (hasAnswer) setOpen(false);
  }, [hasAnswer]);

  if (!text) return null;
  const thinking = streaming && !hasAnswer;

  return (
    <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/40 text-xs">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 px-3 py-2 text-left">
        <span>💭</span>
        <span className="font-medium text-slate-300">{thinking ? "Pensando…" : "Raciocínio"}</span>
        {thinking && (
          <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-slate-600 border-t-indigo-400" />
        )}
        <span className="ml-auto text-slate-500">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="max-h-56 overflow-auto whitespace-pre-wrap border-t border-slate-800 px-3 py-2 italic leading-relaxed text-slate-400">
          {text}
        </div>
      )}
    </div>
  );
}

// All props are primitives (string/boolean), so the default shallow compare already skips a
// re-render whenever the owning turn is unchanged — no custom comparator needed here.
export default memo(ThinkingPanel);
