// "Up next" strip listing pending agents (bottom of the Live view).

import { agentById } from "../data/reference";
import type { Stage } from "../data/types";

export function PendingStrip({ stages }: { stages: Stage[] }) {
  const pending = stages.filter((s) => s.status === "pending");
  if (!pending.length) return null;
  return (
    <div className="pending">
      <b>Up next</b>
      {pending.map((s, i) => {
        const a = agentById(s.stage)!;
        return (
          <span key={s.stage} className="p-item">
            <span className="agent-emoji" aria-hidden>
              {a.emoji}
            </span>
            <span>{a.short}</span>
            {i < pending.length - 1 && <span className="p-sep">·</span>}
          </span>
        );
      })}
    </div>
  );
}
