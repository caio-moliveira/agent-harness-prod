import type { Message } from "../lib/types";

export default function MessageBubble({ message, pending }: { message: Message; pending?: boolean }) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm leading-relaxed ${
          isUser
            ? "bg-indigo-600 text-white"
            : "border border-slate-800 bg-slate-900 text-slate-100"
        }`}
      >
        {message.content || (pending ? <TypingDots /> : "")}
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
