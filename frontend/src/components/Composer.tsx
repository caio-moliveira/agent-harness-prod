import { useState } from "react";
import type { KeyboardEvent } from "react";
import { IconSend } from "./icons";

export default function Composer({
  onSend,
  disabled,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
}) {
  const [text, setText] = useState("");

  function submit() {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  const canSend = Boolean(text.trim()) && !disabled;

  return (
    <div className="px-3 pb-4 pt-2">
      {/* One rounded "pill" surface holding the field + send, echoing the reference composer. */}
      <div className="flex items-end gap-2 rounded-[26px] border border-slate-700 bg-slate-900/80 py-2 pl-4 pr-2 shadow-lg shadow-slate-950/40 backdrop-blur transition-colors focus-within:border-indigo-500/70">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder="Descreva o que você precisa…"
          className="max-h-40 flex-1 resize-none self-center bg-transparent py-1.5 text-sm text-slate-100 placeholder:text-slate-500 outline-none"
        />
        <button
          onClick={submit}
          disabled={!canSend}
          title="Enviar (Enter)"
          className="flex shrink-0 items-center gap-1.5 rounded-full bg-indigo-600 px-4 py-2 text-sm font-semibold text-[#000814] transition hover:bg-indigo-500 hover:shadow-[0_0_18px_rgba(0,194,224,0.55)] disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400 disabled:shadow-none"
        >
          <IconSend className="h-4 w-4" />
          <span className="hidden sm:inline">Enviar</span>
        </button>
      </div>
      <p className="mt-1.5 px-2 text-center text-[11px] text-slate-600">
        Enter envia · Shift+Enter quebra linha
      </p>
    </div>
  );
}
