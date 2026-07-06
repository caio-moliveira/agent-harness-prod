import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

// Tailwind-styled renderers for markdown (no typography plugin needed). Shared by the
// assistant message bubble and the activity timeline's tool output.
const components: Components = {
  p: ({ children }) => <p className="mb-2 leading-relaxed last:mb-0">{children}</p>,
  h1: ({ children }) => <h1 className="mb-2 mt-1 text-lg font-semibold">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-2 mt-1 text-base font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1 mt-1 text-sm font-semibold">{children}</h3>,
  ul: ({ children }) => <ul className="mb-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-indigo-300 underline hover:text-indigo-200">
      {children}
    </a>
  ),
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  blockquote: ({ children }) => (
    <blockquote className="mb-2 border-l-2 border-slate-700 pl-3 text-slate-400">{children}</blockquote>
  ),
  code: ({ className, children }) => {
    const isBlock = (className ?? "").includes("language-");
    if (isBlock) {
      return (
        <code className="block overflow-x-auto rounded-lg bg-slate-950 p-3 font-mono text-xs text-slate-200">
          {children}
        </code>
      );
    }
    return <code className="rounded bg-slate-800 px-1 py-0.5 font-mono text-[0.85em]">{children}</code>;
  },
  pre: ({ children }) => <pre className="mb-2">{children}</pre>,
  table: ({ children }) => (
    <div className="mb-2 overflow-x-auto">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-slate-700 bg-slate-800 px-2 py-1 text-left font-semibold">{children}</th>
  ),
  td: ({ children }) => <td className="border border-slate-800 px-2 py-1">{children}</td>,
  hr: () => <hr className="my-3 border-slate-800" />,
};

/**
 * Heuristic: does this text look like intended markdown (vs. a raw SQL/CSV/JSON dump)?
 * Used to decide whether to render or keep verbatim monospace, so aligned/tabular output
 * doesn't get its whitespace collapsed.
 */
export function looksLikeMarkdown(text: string): boolean {
  const t = text.trim();
  if (!t) return false;
  // GFM table: a pipe row plus a dashed separator row.
  if (/^\s*\|.*\|\s*$/m.test(t) && /^\s*\|?[\s:|-]*-{2,}[\s:|-]*$/m.test(t)) return true;
  if (/^#{1,6}\s+\S/m.test(t)) return true; // headings
  if (/^\s*[-*+]\s+\S/m.test(t) || /^\s*\d+\.\s+\S/m.test(t)) return true; // lists
  if (/```/.test(t)) return true; // fenced code
  if (/\*\*[^*]+\*\*/.test(t) || /\[[^\]]+\]\([^)]+\)/.test(t)) return true; // bold or links
  return false;
}

/** Render a markdown string with the app's shared, Tailwind-styled elements. */
export default function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {children}
    </ReactMarkdown>
  );
}
