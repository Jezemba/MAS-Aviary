// Context-window budget bar. Fill flips --accent → --warn above 85%.
// Long runs fill the 200K window; this is the only warning the user gets.

import { fmtTokens } from "../lib/format";

export function TokenBudget({ used, budget }: { used: number; budget: number }) {
  const pct = budget > 0 ? Math.min(100, (used / budget) * 100) : 0;
  const warn = pct > 85;
  // Some runner logs report cumulative tokens that exceed a single context
  // window; clamp the readout so the bar reads "full", never a broken >100%.
  const shownUsed = Math.min(used, budget);
  return (
    <div className="card" style={{ padding: "14px 18px" }}>
      <div className="spread" style={{ marginBottom: 8 }}>
        <span style={{ fontSize: 11.5, fontWeight: 500, color: "var(--ink-3)" }}>
          Context window
        </span>
        <span className="mono" style={{ fontSize: 12, color: warn ? "var(--warn-ink)" : "var(--ink-3)" }}>
          {fmtTokens(shownUsed)} / {fmtTokens(budget)} ({Math.round(pct)}%)
        </span>
      </div>
      <div className={`bar ${warn ? "is-warn" : ""}`}>
        <i style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
