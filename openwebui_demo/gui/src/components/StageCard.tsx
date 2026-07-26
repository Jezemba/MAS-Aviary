// Stage card, tool-log row, and iteration cluster - the Live view centerpiece.
// Ported from the prototype's components.jsx (StageCard / ToolRow / StageGroup)
// with real tool names and rules-of-hooks-safe iteration handling.

import { useEffect, useRef, useState } from "react";

import { agentById } from "../data/reference";
import { toolIcon } from "../data/toolIcons";
import type { Iter, Stage, Status, ToolCall } from "../data/types";
import { fmtDuration, fmtTokens } from "../lib/format";
import { AgentTile, Badge, StatusGlyph } from "./atoms";
import { DesignStateBlock } from "./CodeBlock";

// Number of iterations beyond which the stacked cluster collapses into a
// "N attempts" accordion with a runaway-loop warning (open question #5).
const RUNAWAY_THRESHOLD = 6;

function ToolRow({ tool, runningElapsed }: { tool: ToolCall; runningElapsed: number | null }) {
  const dur =
    tool.status === "pending"
      ? "-"
      : tool.status === "running"
        ? fmtDuration(runningElapsed ?? tool.dur_s) + " ⏳"
        : fmtDuration(tool.dur_s);
  return (
    <div className={`tool-row is-${tool.status}`}>
      <span className="ic" aria-hidden>
        {toolIcon(tool.name)}
      </span>
      <span className="nm">
        {tool.desc}
        <code>{tool.name}</code>
      </span>
      <span className="dur">{dur}</span>
      <span className="st">
        <StatusGlyph status={tool.status} />
      </span>
    </div>
  );
}

export function StageCard({
  stage,
  iter,
  iterIndex,
  totalIters,
  runningElapsed,
  defaultOpen,
  showIterTag,
}: {
  stage: Stage;
  iter: Iter;
  iterIndex: number;
  totalIters: number;
  runningElapsed: number | null;
  defaultOpen?: boolean;
  showIterTag?: boolean;
}) {
  const agent = agentById(stage.stage)!;
  const isRunning = stage.status === "running" && iterIndex === totalIters - 1;
  const isCompleted = stage.status !== "running" && stage.status !== "pending";
  const [open, setOpen] = useState(defaultOpen ?? isRunning);

  // During a live/replay stream a stage transitions running → done. Follow the
  // design's "running open, completed closed" rule across that transition:
  // auto-open when it starts running, auto-collapse to the Result line when it
  // finishes. Manual toggles in between are preserved (we only react on edges).
  const prevRunning = useRef(isRunning);
  useEffect(() => {
    if (prevRunning.current && !isRunning) setOpen(false);
    else if (!prevRunning.current && isRunning) setOpen(true);
    prevRunning.current = isRunning;
  }, [isRunning]);

  // Badge status: running stages have the last iter running, prior iters ok;
  // a partial/failed stage marks its last iter accordingly.
  const iterStatus: Status = isRunning
    ? "running"
    : iterIndex === totalIters - 1 && (stage.status === "partial" || stage.status === "failed")
      ? stage.status
      : "success";

  const totalTokens = (iter.tokens_in || 0) + (iter.tokens_out || 0);

  return (
    <div className={`stage ${isRunning ? "is-running" : ""}`}>
      <div className="stage-head" onClick={() => setOpen((o) => !o)}>
        <AgentTile emoji={agent.emoji} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <h4 className="stage-title">
            {agent.name}
            <span className="stage-mcp">{agent.mcp}</span>
          </h4>
        </div>
        <div className="stage-meta">
          {totalIters > 1 && showIterTag !== false && (
            <span className="mono">
              iter {iter.iter}/{totalIters}
            </span>
          )}
          <Badge status={iterStatus} />
          <span className="sep" />
          <span className="mono">{fmtDuration(isRunning ? (runningElapsed ?? iter.duration_s) : iter.duration_s)}</span>
          <span className="sep" />
          <span className="mono">{fmtTokens(totalTokens)} tok</span>
          <span className="chev">{open ? "▴" : "▾"}</span>
        </div>
      </div>

      {!open && !isRunning && iter.summary && (
        <div className="stage-summary">
          <b>Result:</b> {iter.summary}
        </div>
      )}

      {open && (
        <div className="stage-body">
          {iter.intro && <p className="stage-intro">“{iter.intro}”</p>}

          {isRunning && iter.currently && (
            <div className="stage-currently">
              <span className="spinner" />
              <span>
                <span className="cur-label">Currently:</span> {iter.currently.desc}
              </span>
              <span className="cur-time">
                · {fmtDuration(runningElapsed ?? iter.currently.elapsed_s)} elapsed
              </span>
            </div>
          )}

          <details className="toolog" open={isRunning}>
            <summary>
              Tool log · {iter.tools.length} call{iter.tools.length === 1 ? "" : "s"}
            </summary>
            {iter.tools.map((t, i) => (
              <ToolRow
                key={i}
                tool={t}
                runningElapsed={t.status === "running" ? runningElapsed : null}
              />
            ))}
          </details>

          {isCompleted && iter.design_state && (
            <details className="dstate">
              <summary>DESIGN_STATE final answer</summary>
              <DesignStateBlock data={iter.design_state} />
            </details>
          )}
        </div>
      )}
    </div>
  );
}

// Tabs variant, extracted so hooks aren't called conditionally.
function IterTabs({ stage, runningElapsed }: { stage: Stage; runningElapsed: number | null }) {
  const [active, setActive] = useState(stage.iters.length - 1);
  const iter = stage.iters[active];
  return (
    <div>
      <div className="seg" style={{ marginBottom: 8 }}>
        {stage.iters.map((it, i) => (
          <button key={i} className={i === active ? "is-active" : ""} onClick={() => setActive(i)}>
            iter {it.iter}
          </button>
        ))}
      </div>
      <StageCard
        stage={stage}
        iter={iter}
        iterIndex={active}
        totalIters={stage.iters.length}
        runningElapsed={active === stage.iters.length - 1 ? runningElapsed : null}
      />
    </div>
  );
}

// Runaway cluster: collapses a long stack of attempts behind an accordion with
// an amber warning and a "Stop this stage" affordance (open question #5).
function RunawayStack({
  stage,
  runningElapsed,
  onStopStage,
}: {
  stage: Stage;
  runningElapsed: number | null;
  onStopStage?: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const agent = agentById(stage.stage)!;
  const last = stage.iters[stage.iters.length - 1];
  return (
    <div className="iter-stack">
      <div className="runaway">
        <span aria-hidden>⚠️</span>
        <span className="rw-msg">
          <b>{agent.short}</b> has run <b>{stage.iters.length} attempts</b> without settling.
        </span>
        <button className="btn btn-sm" onClick={() => setExpanded((e) => !e)}>
          {expanded ? "Collapse" : `Show all ${stage.iters.length}`}
        </button>
        {onStopStage && (
          <button className="btn btn-sm btn-danger" onClick={() => onStopStage(stage.stage)}>
            Stop this stage
          </button>
        )}
      </div>
      {(expanded ? stage.iters : [last]).map((it) => {
        const idx = stage.iters.indexOf(it);
        return (
          <div className="iter-wrap" key={idx}>
            <div className="iter-tag">↻</div>
            <StageCard
              stage={stage}
              iter={it}
              iterIndex={idx}
              totalIters={stage.iters.length}
              runningElapsed={idx === stage.iters.length - 1 ? runningElapsed : null}
              defaultOpen={idx === stage.iters.length - 1}
            />
          </div>
        );
      })}
    </div>
  );
}

export function StageGroup({
  stage,
  runningElapsed,
  iterStyle,
  onStopStage,
}: {
  stage: Stage;
  runningElapsed: number | null;
  iterStyle: "stacked" | "tabs";
  onStopStage?: (id: string) => void;
}) {
  if (stage.status === "pending") return null;

  if (stage.iters.length === 1) {
    return (
      <StageCard
        stage={stage}
        iter={stage.iters[0]}
        iterIndex={0}
        totalIters={1}
        runningElapsed={runningElapsed}
      />
    );
  }

  if (iterStyle === "tabs") {
    return <IterTabs stage={stage} runningElapsed={runningElapsed} />;
  }

  if (stage.iters.length > RUNAWAY_THRESHOLD) {
    return <RunawayStack stage={stage} runningElapsed={runningElapsed} onStopStage={onStopStage} />;
  }

  // stacked (default): cluster with a dashed vertical rail + ↻ tags
  return (
    <div className="iter-stack">
      {stage.iters.map((it, i) => (
        <div className="iter-wrap" key={i}>
          <div className="iter-tag">↻</div>
          <StageCard
            stage={stage}
            iter={it}
            iterIndex={i}
            totalIters={stage.iters.length}
            runningElapsed={i === stage.iters.length - 1 ? runningElapsed : null}
            defaultOpen={i === stage.iters.length - 1}
          />
        </div>
      ))}
    </div>
  );
}
