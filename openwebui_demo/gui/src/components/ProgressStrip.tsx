// The constant, screenshot-friendly run summary: seven equal-flex nodes with
// connectors. Done nodes fill --ink with a corner tick (ok/warn/err);
// running gets an accent glow ring; pending stays muted.

import { AGENTS } from "../data/reference";
import type { Stage, Status } from "../data/types";

// Note: "pending" intentionally maps to an empty class - a literal `pending`
// class here would collide with the `.pending` Up-Next card (dashed border).
const NODE_CLASS: Record<Status, string> = {
  success: "done",
  partial: "done partial",
  failed: "done failed",
  running: "running",
  pending: "",
};

export function ProgressStrip({ stages }: { stages: Stage[] }) {
  const byId = new Map(stages.map((s) => [s.stage, s]));
  return (
    <div className="strip">
      {AGENTS.map((a) => {
        const st = byId.get(a.id)?.status ?? "pending";
        const isDone = st === "success" || st === "partial" || st === "failed";
        return (
          <div key={a.id} className={`strip-node ${NODE_CLASS[st]}`}>
            <span className="strip-bubble" title={`${a.name} - ${st}`}>
              <span aria-hidden>{a.emoji}</span>
              {isDone && <span className="strip-tick" />}
            </span>
            <span className="strip-name">{a.short}</span>
          </div>
        );
      })}
    </div>
  );
}
