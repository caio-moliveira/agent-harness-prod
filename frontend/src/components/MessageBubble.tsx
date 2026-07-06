import type { Message } from "../lib/types";
import Markdown from "./Markdown";

export default function MessageBubble({ message, pending }: { message: Message; pending?: boolean }) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-2 text-sm leading-relaxed ${
          isUser
            ? "whitespace-pre-wrap bg-indigo-600 text-white"
            : "border border-slate-800 bg-slate-900 text-slate-100"
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
  );
}

function TypingDots() {
  return (
    <span className="inline-flex gap-1 align-middle">
      <span className="h-2 w-2 animate-bounce rounded-full bg-slate-500 [animation-delay:-0.3s]" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-slate-500 [animation-delay:-0.15s]" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-slate-500" />
    </span>
  );
}
