// Live run monitoring - the highest-value surface. Progress strip, optional
// token-budget bar, newest-first stage cards, pending strip. Elapsed timers on
// the page subtitle, the running stage, and its current tool tick every second.

import { useMemo } from "react";

import type { Prefs } from "../App";
import { PendingStrip } from "../components/PendingStrip";
import { ProgressStrip } from "../components/ProgressStrip";
import { StageGroup } from "../components/StageCard";
import { TokenBudget } from "../components/TokenBudget";
import { AGENTS } from "../data/reference";
import type { Run, RunPhase } from "../data/types";
import { fmtDuration } from "../lib/format";
import { useTicker } from "../lib/useTicker";

const DONE = new Set(["success", "partial", "failed"]);

export function LiveView({
  run,
  prefs,
  phase,
  onPhase,
  connectLive,
  onConnectLive,
  liveError,
}: {
  run: Run;
  prefs: Prefs;
  phase: RunPhase;
  onPhase: (p: RunPhase) => void;
  connectLive: boolean;
  onConnectLive: (v: boolean) => void;
  liveError: string | null;
}) {
  const tick = useTicker(phase === "running");
  const { meta, stages } = run;

  const completed = stages.filter((s) => DONE.has(s.status)).length;
  const total = AGENTS.length;

  // Frozen snapshot ticks upward from its recorded elapsed while running.
  const pageElapsed = phase === "running" ? meta.elapsed_s + tick : meta.elapsed_s;

  const runningStage = stages.find((s) => s.status === "running");
  const runningElapsed = useMemo(() => {
    if (!runningStage || phase !== "running") return null;
    const it = runningStage.iters[runningStage.iters.length - 1];
    const base = it?.currently?.elapsed_s ?? it?.duration_s ?? 0;
    return base + tick;
  }, [runningStage, phase, tick]);

  // Newest-first: non-pending stages, most-recently-active on top.
  const active = stages.filter((s) => s.status !== "pending").slice().reverse();

  const usedTokens = meta.total_tokens_in + meta.total_tokens_out;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">
            Live run <span className="count">{completed}/{total} stages</span>
          </h1>
          <p className="page-sub">
            <span className="mono">{meta.combo}</span> · elapsed {fmtDuration(pageElapsed)}
          </p>
        </div>
        <div className="page-actions">
          {!connectLive && (
            <button
              className="btn btn-sm"
              title="Spawns the real paid 5-MCP pipeline (~10 min, Anthropic + wandb)"
              onClick={() => onConnectLive(true)}
            >
              Connect live run
            </button>
          )}
          <a className="btn btn-sm" href={meta.wandb_url} target="_blank" rel="noreferrer">
            ↗ wandb
          </a>
          {phase === "running" ? (
            <button className="btn btn-sm" onClick={() => onPhase("paused")}>
              Pause
            </button>
          ) : phase === "paused" ? (
            <button className="btn btn-sm" onClick={() => onPhase("running")}>
              Resume
            </button>
          ) : null}
          <button className="btn btn-sm btn-danger" onClick={() => onPhase("done")}>
            Stop run
          </button>
        </div>
      </div>

      {!connectLive && (
        <p className="muted" style={{ fontSize: 11.5, marginTop: -8, marginBottom: 12 }}>
          Showing the bundled sample run. “Connect live run” streams a real
          pipeline from <span className="mono">chat_server</span> (paid).
        </p>
      )}
      {liveError && (
        <p style={{ color: "var(--err)", fontSize: 12, marginBottom: 12 }}>
          Live stream error: {liveError}
        </p>
      )}

      <div className="stack-14">
        <ProgressStrip stages={stages} />

        {prefs.showTokenBudget && <TokenBudget used={usedTokens} budget={meta.tokens_budget} />}

        <div>
          {active.map((s) => (
            <div key={s.stage} style={{ marginBottom: 10 }}>
              <StageGroup
                stage={s}
                runningElapsed={s.status === "running" ? runningElapsed : null}
                iterStyle={prefs.iterStyle}
                onStopStage={() => onPhase("paused")}
              />
            </div>
          ))}
        </div>

        <PendingStrip stages={stages} />
      </div>
    </div>
  );
}
