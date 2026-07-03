import { useState } from "react";
import type { KeyboardEvent } from "react";

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

  return (
    <div className="flex items-end gap-2 border-t border-slate-800 bg-slate-950 p-3">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        rows={1}
        placeholder="Escreva uma mensagem…  (Enter envia, Shift+Enter quebra linha)"
        className="max-h-40 flex-1 resize-none rounded-xl border border-slate-700 bg-slate-900 px-4 py-3 text-sm outline-none focus:border-indigo-500"
      />
      <button
        onClick={submit}
        disabled={disabled || !text.trim()}
        className="rounded-xl bg-indigo-600 px-4 py-3 text-sm font-medium hover:bg-indigo-500 disabled:opacity-50"
      >
        Enviar
      </button>
    </div>
  );
}
