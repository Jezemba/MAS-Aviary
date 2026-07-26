// App shell: top bar (brand + nav + run-status pill), footer.

import type { RunMeta, RunPhase } from "../data/types";

export type ViewId = "setup" | "topology" | "live" | "results" | "replay";

const NAV: { id: ViewId; label: string }[] = [
  { id: "setup", label: "Setup" },
  { id: "topology", label: "Topology" },
  { id: "live", label: "Live" },
  { id: "results", label: "Results" },
  { id: "replay", label: "Replay" },
];

function RunPill({ runId, phase }: { runId: string; phase: RunPhase }) {
  const cls =
    phase === "paused" ? "is-paused" : phase === "done" || phase === "failed" ? "is-done" : "";
  const label =
    phase === "idle"
      ? "Not started"
      : phase === "running"
        ? "Running"
        : phase === "paused"
          ? "Paused"
          : phase === "failed"
            ? "Failed"
            : "Completed";
  return (
    <span className={`run-id ${cls}`}>
      <span className="dot" />
      run {runId}
      <span className="sep" />
      {label}
    </span>
  );
}

export function TopBar({
  view,
  onView,
  hasLaunched,
  runId,
  phase,
}: {
  view: ViewId;
  onView: (v: ViewId) => void;
  hasLaunched: boolean;
  runId: string;
  phase: RunPhase;
}) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">M</span>
        <span className="brand-name">MAS-Aviary</span>
        <span className="brand-sub">Multi-agent MDO · DLR-F25</span>
      </div>
      <nav className="nav">
        {NAV.map((n) => {
          const disabled = n.id === "live" && !hasLaunched;
          return (
            <button
              key={n.id}
              className={`nav-item ${view === n.id ? "is-active" : ""}`}
              onClick={() => onView(n.id)}
              disabled={disabled}
              title={disabled ? "Launch a run to enable the Live view" : undefined}
            >
              {n.label}
            </button>
          );
        })}
      </nav>
      <div className="topbar-spacer" />
      <RunPill runId={runId} phase={phase} />
    </header>
  );
}

export function Footer({ meta }: { meta: RunMeta }) {
  return (
    <footer className="foot">
      <span>
        <span className="mono">{meta.combo}</span> · <span className="mono">{meta.output_dir}</span>
      </span>
      <a href={meta.wandb_url} target="_blank" rel="noreferrer">
        ↗ wandb dashboard
      </a>
    </footer>
  );
}
