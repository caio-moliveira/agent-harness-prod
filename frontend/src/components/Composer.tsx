import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, MouseEvent as ReactMouseEvent } from "react";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";
import type { AgentSkillItem, SessionFileItem } from "../lib/types";
import { IconSend } from "./icons";

interface Mention {
  trigger: "/" | "@";
  start: number;
  query: string;
}

type MentionItem = { label: string; description?: string };

/** Find the `/` or `@` mention token touching the cursor, if any.
 *
 * Scans backward from the cursor to the nearest whitespace/start-of-string boundary: a mention is
 * only active when its trigger character starts that token (never mid-word, e.g. inside a URL).
 */
function findMention(text: string, cursor: number): Mention | null {
  let start = cursor;
  while (start > 0 && !/\s/.test(text[start - 1])) {
    start--;
  }
  const triggerChar = text[start];
  if (triggerChar !== "/" && triggerChar !== "@") return null;
  return { trigger: triggerChar, start, query: text.slice(start + 1, cursor) };
}

export default function Composer({
  onSend,
  disabled,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
}) {
  const { userToken, sessionToken, agentId, sessionId } = useAuth();
  const [text, setText] = useState("");
  const [mention, setMention] = useState<Mention | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  // Whether the user has actively browsed the dropdown (arrow keys) for the CURRENT mention token.
  // Until then, Enter still sends normally — typing a mention out by hand and pressing Enter must
  // behave exactly like any other message, never get silently swallowed as a "confirm selection".
  const [navigated, setNavigated] = useState(false);
  const [skillItems, setSkillItems] = useState<AgentSkillItem[] | null>(null);
  const [fileItems, setFileItems] = useState<SessionFileItem[] | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Fetch this agent's usable skills fresh each time the `/` picker opens (or the agent changes).
  useEffect(() => {
    if (mention?.trigger !== "/") return;
    if (!userToken || agentId == null) {
      setSkillItems([]);
      return;
    }
    let cancelled = false;
    api
      .listAgentSkills(userToken, agentId)
      .then((items) => !cancelled && setSkillItems(items))
      .catch(() => !cancelled && setSkillItems([]));
    return () => {
      cancelled = true;
    };
  }, [mention?.trigger, agentId, userToken]);

  // Fetch the session's granted folder listing fresh each time the `@` picker opens (or the session changes).
  useEffect(() => {
    if (mention?.trigger !== "@") return;
    if (!sessionToken || !sessionId) {
      setFileItems([]);
      return;
    }
    let cancelled = false;
    api
      .listSessionFiles(sessionToken, sessionId)
      .then((items) => !cancelled && setFileItems(items))
      .catch(() => !cancelled && setFileItems([]));
    return () => {
      cancelled = true;
    };
  }, [mention?.trigger, sessionId, sessionToken]);

  // Click-away: close the picker when the user clicks outside the composer.
  useEffect(() => {
    if (!mention) return;
    function onDocMouseDown(e: globalThis.MouseEvent) {
      if (wrapperRef.current && e.target instanceof Node && !wrapperRef.current.contains(e.target)) {
        setMention(null);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [mention]);

  const items: MentionItem[] =
    mention?.trigger === "/"
      ? (skillItems ?? []).map((s) => ({ label: s.name, description: s.description }))
      : mention?.trigger === "@"
        ? (fileItems ?? [])
            .filter((f) => !f.is_dir)
            .map((f) => ({ label: f.path.replace(/^\//, "") }))
        : [];
  const query = mention?.query ?? "";
  const filtered = query
    ? items.filter((i) => i.label.toLowerCase().includes(query.toLowerCase()))
    : items;
  const loading = mention !== null && (mention.trigger === "/" ? skillItems === null : fileItems === null);

  function recomputeMention(el: HTMLTextAreaElement) {
    const cursor = el.selectionStart ?? el.value.length;
    const found = findMention(el.value, cursor);
    // A genuinely new token (or no token) resets the "did the user browse the list" signal.
    if (!found || !mention || mention.start !== found.start || mention.trigger !== found.trigger) {
      setNavigated(false);
    }
    setMention(found);
    setActiveIndex(0);
  }

  function submit() {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
    setMention(null);
    setNavigated(false);
  }

  function selectItem(item: MentionItem) {
    if (!mention) return;
    const end = mention.start + 1 + mention.query.length;
    const before = text.slice(0, mention.start);
    const after = text.slice(end);
    const insertion = `${mention.trigger}${item.label} `;
    setText(`${before}${insertion}${after}`);
    setMention(null);
    setNavigated(false);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      const pos = before.length + insertion.length;
      el.focus();
      el.setSelectionRange(pos, pos);
    });
  }

  function onChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setText(e.target.value);
    recomputeMention(e.target);
  }

  function onClick(e: ReactMouseEvent<HTMLTextAreaElement>) {
    recomputeMention(e.currentTarget);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (mention && filtered.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setNavigated(true);
        setActiveIndex((i) => (i + 1) % filtered.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setNavigated(true);
        setActiveIndex((i) => (i - 1 + filtered.length) % filtered.length);
        return;
      }
      // Only intercept Enter once the user has actively browsed the dropdown — otherwise Enter
      // must always send, exactly like a message that never had a picker open at all.
      if (e.key === "Enter" && navigated) {
        e.preventDefault();
        selectItem(filtered[activeIndex]);
        return;
      }
    }
    if (mention && e.key === "Escape") {
      e.preventDefault();
      setMention(null);
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
      return;
    }
    // Cursor-movement keys don't fire onChange — recompute after the browser applies them.
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
      requestAnimationFrame(() => textareaRef.current && recomputeMention(textareaRef.current));
    }
  }

  const canSend = Boolean(text.trim()) && !disabled;

  return (
    <div ref={wrapperRef} className="relative px-3 pb-4 pt-2">
      {mention && (
        <div className="absolute inset-x-3 bottom-full mb-1 max-h-56 overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 shadow-xl">
          {loading ? (
            <p className="px-3 py-2 text-xs text-slate-500">Carregando…</p>
          ) : filtered.length === 0 ? (
            <p className="px-3 py-2 text-xs text-slate-500">
              {mention.trigger === "/" ? "Nenhuma skill disponível." : "Nenhum arquivo disponível."}
            </p>
          ) : (
            filtered.map((item, i) => (
              <button
                key={item.label}
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault(); // keep textarea focus so the caret position stays valid
                  selectItem(item);
                }}
                onMouseEnter={() => setActiveIndex(i)}
                className={`block w-full truncate px-3 py-1.5 text-left text-sm ${
                  i === activeIndex ? "bg-indigo-950/60 text-indigo-200" : "text-slate-200 hover:bg-slate-800"
                }`}
              >
                <span className="font-medium">
                  {mention.trigger}
                  {item.label}
                </span>
                {item.description && <span className="ml-2 truncate text-xs text-slate-500">{item.description}</span>}
              </button>
            ))
          )}
        </div>
      )}
      {/* One rounded "pill" surface holding the field + send, echoing the reference composer. */}
      <div className="flex items-end gap-2 rounded-[26px] border border-slate-700 bg-slate-900/80 py-2 pl-4 pr-2 shadow-lg shadow-slate-950/40 backdrop-blur transition-colors focus-within:border-indigo-500/70">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={onChange}
          onClick={onClick}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder="Descreva o que você precisa… (/ para skills, @ para arquivos)"
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
        {mention ? "↑↓ navega · Enter seleciona · Esc fecha" : "Enter envia · Shift+Enter quebra linha"}
      </p>
    </div>
  );
}
