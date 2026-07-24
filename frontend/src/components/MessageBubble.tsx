import { memo } from "react";
import type { Message } from "../lib/types";
import Markdown from "./Markdown";
import { IconSparkles, IconUser } from "./icons";

/**
 * A chat turn: an avatar + author label paired with the bubble, mirrored by role.
 * User turns sit right in a solid azure bubble; assistant turns sit left in a raised
 * navy surface. Matches the reference layout (labelled, avatared, mirrored).
 */
function MessageBubble({
  message,
  pending,
  authorName,
}: {
  message: Message;
  pending?: boolean;
  authorName?: string;
}) {
  const isUser = message.role === "user";
  const name = authorName ?? (isUser ? "Você" : "Agente");

  const avatar = isUser ? (
    <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-800 text-slate-300 ring-1 ring-slate-700">
      <IconUser className="h-4 w-4" />
    </div>
  ) : (
    <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 text-white shadow-lg shadow-indigo-950/50">
      <IconSparkles className="h-4 w-4" />
    </div>
  );

  return (
    <div className={`flex items-start gap-2.5 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
      {avatar}
      <div className={`flex min-w-0 max-w-[80%] flex-col ${isUser ? "items-end" : "items-start"}`}>
        <span className="mb-1 px-1 text-xs font-medium text-slate-400">{name}</span>
        <div
          className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-sm ${
            isUser
              ? "whitespace-pre-wrap rounded-tr-sm bg-indigo-600 text-[#000814]"
              : "rounded-tl-sm border border-slate-800 bg-slate-900 text-slate-100"
          }`}
        >
          {isUser ? (
            message.content
          ) : message.content ? (
            <Markdown>{message.content}</Markdown>
          ) : pending ? (
            <TypingDots />
          ) : (
            ""
          )}
        </div>
      </div>
    </div>
  );
}

function TypingDots() {
  return (
    <span className="inline-flex gap-1 align-middle py-1">
      <span className="thinking-dot h-1.5 w-1.5 rounded-full bg-indigo-400" />
      <span className="thinking-dot h-1.5 w-1.5 rounded-full bg-indigo-400" />
      <span className="thinking-dot h-1.5 w-1.5 rounded-full bg-indigo-400" />
    </span>
  );
}

// ChatScreen passes `message={{ role, content: seg.text }}` as a fresh object literal every
// render, so the default shallow-prop compare would never bail out — compare the fields instead,
// so a completed turn's bubble doesn't re-render on every token streamed into a *different* turn.
export default memo(
  MessageBubble,
  (prev, next) =>
    prev.message.role === next.message.role &&
    prev.message.content === next.message.content &&
    prev.pending === next.pending &&
    prev.authorName === next.authorName,
);
