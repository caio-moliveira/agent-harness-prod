import { useEffect, useState } from "react";
import * as api from "../lib/api";
import type { UsageStatus } from "../lib/types";

/**
 * Quiet daily-budget indicator.
 *
 * Cost governance is only a control if the user can see it coming — a budget whose first
 * appearance is a refusal is a support ticket. Renders nothing when no budget is enforced (the
 * default deployment), stays discreet under 75%, and only draws attention as the limit nears.
 */
export default function UsageIndicator({ userToken, reloadKey }: { userToken: string; reloadKey?: number }) {
  const [usage, setUsage] = useState<UsageStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getUsage(userToken)
      .then((u) => !cancelled && setUsage(u))
      .catch(() => !cancelled && setUsage(null));
    return () => {
      cancelled = true;
    };
  }, [userToken, reloadKey]);

  // No budget configured (limit 0) → nothing to show. Never nag a deployment that doesn't govern cost.
  if (!usage || usage.limit <= 0) return null;

  const pct = Math.min(100, Math.round((usage.total_tokens / usage.limit) * 100));
  const tone =
    usage.exceeded || pct >= 100
      ? "text-red-300 border-red-900 bg-red-950/40"
      : pct >= 90
        ? "text-amber-300 border-amber-900 bg-amber-950/40"
        : "text-zinc-400 border-zinc-800 bg-zinc-900/40";
  const resets = new Date(usage.resets_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  return (
    <div
      className={`mx-2 mb-2 rounded-md border px-2.5 py-1.5 text-xs ${tone}`}
      title={`${usage.total_tokens.toLocaleString("pt-BR")} de ${usage.limit.toLocaleString("pt-BR")} tokens · renova ${resets}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span>Uso diário</span>
        <span className="tabular-nums">{pct}%</span>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded-full bg-zinc-800">
        <div
          className={pct >= 90 ? "h-full bg-amber-500" : "h-full bg-zinc-500"}
          style={{ width: `${pct}%` }}
        />
      </div>
      {usage.exceeded && <div className="mt-1">Limite atingido — renova às {resets}.</div>}
    </div>
  );
}
