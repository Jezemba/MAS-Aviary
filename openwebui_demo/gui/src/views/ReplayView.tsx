// Replay - identical renderers to Live, driven by the real replay SSE stream
// (chat_server /v1/runs/stream?mode=replay), with a transport bar. Falls back
// to the mock fixture if the backend isn't reachable, so the design still
// renders offline.

import { useMemo, useState } from "react";

import type { Prefs } from "../App";
import { PendingStrip } from "../components/PendingStrip";
import { ProgressStrip } from "../components/ProgressStrip";
import { StageGroup } from "../components/StageCard";
import { TokenBudget } from "../components/TokenBudget";
import type { Run } from "../data/types";
import { fmtClock } from "../lib/format";
import { useRunStream } from "../sse/useRunStream";

const DONE = new Set(["success", "partial", "failed"]);

export function ReplayView({ run: fallback, prefs }: { run: Run; prefs: Prefs }) {
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(4);
  const [epoch, setEpoch] = useState(0);

  const { run: streamRun, phase, error } = useRunStream({
    mode: "replay",
    speed,
    enabled: playing,
    epoch,
  });

  const run = streamRun ?? fallback;
  const { meta, stages } = run;

  const active = useMemo(
    () => stages.filter((s) => s.status !== "pending").slice().reverse(),
    [stages],
  );
  const usedTokens = meta.total_tokens_in + meta.total_tokens_out;
  const totalDone = stages.filter((s) => DONE.has(s.status)).length;
  const live = streamRun != null;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Replay</h1>
          <p className="page-sub">
            <span className="mono">{meta.combo}</span> · cached run{" "}
            <span className="mono">{meta.run_id}</span>
            {!live && <span className="muted"> · offline (mock)</span>}
            {error && <span style={{ color: "var(--err)" }}> · {error}</span>}
          </p>
        </div>
        <div className="page-actions">
          <button
            className="btn btn-sm"
            onClick={() => {
              setPlaying(true);
              setEpoch((e) => e + 1);
            }}
          >
            ↺ Restart
          </button>
          <button className="btn btn-sm">Open another run…</button>
        </div>
      </div>

      {/* Transport bar */}
      <div className="replay-bar">
        <button className="btn btn-sm" onClick={() => setPlaying((p) => !p)}>
          {playing ? "❚❚ Pause" : "▶ Play"}
        </button>
        <span>Speed</span>
        <input
          type="range"
          min={0.5}
          max={10}
          step={0.5}
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
        />
        <span className="speed-val mono">{speed}×</span>
        <span className="pos mono">
          {totalDone}/{stages.length} stages · {fmtClock(meta.elapsed_s)}
        </span>
      </div>

      <div className="stack-14">
        <ProgressStrip stages={stages} />
        {prefs.showTokenBudget && <TokenBudget used={usedTokens} budget={meta.tokens_budget} />}
        <div>
          {active.map((s) => (
            <div key={s.stage} style={{ marginBottom: 10 }}>
              <StageGroup
                stage={s}
                runningElapsed={s.status === "running" ? meta.elapsed_s : null}
                iterStyle={prefs.iterStyle}
              />
            </div>
          ))}
        </div>
        <PendingStrip stages={stages} />
      </div>

      <div className="muted" style={{ marginTop: 8, fontSize: 11.5 }}>
        {live ? `Streaming replay at ${speed}×` : "Backend offline - showing the bundled sample run"}
        {phase === "done" && " · complete"} {!playing && "· paused"}
      </div>
    </div>
  );
}
