import { useEffect, useMemo, useRef, useState } from "react";
import { LinkChart, Spark, TrajectoryChart } from "./charts";
import { ShotPair, type Shot } from "./media";
import {
  agentStepsSoFar, claimTurns, commSoFar, cursorTime, feedbackStrip, firstSuccess, fmt, graphModel, paramDiff, pipelineStrip,
  signalsSoFar, trend, type GraphModel,
} from "./playback";
import type { Link, MetricDef, Run, Step, TrajPhase } from "./types";

const pretty = (a: string | null | undefined) => (a ?? "—").replace(/_/g, " ");

// ---------------------------------------------------------------- current agent

export function CurrentAgent({ run, link, cursor }: { run: Run; link: Link; cursor: number }) {
  const step = link.steps[cursor];
  const mine = useMemo(() => (step ? agentStepsSoFar(link, cursor, step.agent) : []), [link, cursor, step]);
  const [picked, setPicked] = useState<number | null>(null);
  useEffect(() => setPicked(null), [cursor, link.link]);
  if (!step) return <section className="panel agent"><h2>Current Agent</h2><p className="muted">No agent steps recorded in this link yet.</p></section>;
  const shown: Step = picked !== null ? link.steps[picked] : step;
  const m = link.step_metrics[cursor] ?? {};
  const budget = run.token_budget.prompt_budget;
  const ctx = shown.prompt_tokens;
  return (
    <section className="panel agent">
      <div className="agent-head">
        <span className="eyebrow">Current agent</span>
        <h2>{pretty(step.agent)}</h2>
        <span className="hint">step {step.agent_step ?? "?"} of this agent · link step {cursor + 1} of {link.steps.length}</span>
      </div>
      <label className="field">
        <span>Tools called</span>
        <select value={picked ?? step.i} onChange={(e) => setPicked(Number(e.target.value))}>
          {mine.map((s) => (
            <option key={s.i} value={s.i}>
              #{s.agent_step} {s.tool_calls.map((c) => c.name).join(", ") || "(no tool call)"}
              {s.error ? " — error" : ""}
            </option>
          ))}
        </select>
      </label>
      <div className="agent-body">
        {shown.tool_calls.map((c, k) => (
          <details key={k} className="call" open={k === 0}>
            <summary><code>{c.name}</code></summary>
            <pre>{c.arguments}</pre>
          </details>
        ))}
        {shown.observation && (
          <details className="call">
            <summary>tool result</summary>
            <pre>{shown.observation}</pre>
          </details>
        )}
        {shown.error && <div className="error-box">{shown.error}</div>}
        <div className="llm-label">LLM output{shown.generation_attempts > 1 ? ` (after ${shown.generation_attempts} generation attempts)` : ""}</div>
        <pre className="llm">{shown.llm_output ?? "(no output recorded)"}</pre>
      </div>
      <div className="counter">
        <div><b>{mine.length}</b> steps (agent) · <b>{m.steps ?? cursor + 1}</b> (link)</div>
        <div>
          context <b>{ctx === null ? "not recorded" : fmt(ctx)}</b> / {budget.toLocaleString()} tokens
          {ctx === null && <span className="muted small"> (the step log has no prompt for this generation)</span>}
        </div>
        <div className="budget"><div style={{ width: `${Math.min(100, ((ctx ?? 0) / budget) * 100)}%` }} /></div>
        <div className="muted">spent this step {fmt(shown.prompt_tokens_spent + shown.output_tokens)} · link total {fmt(m.tokens_total as number)}</div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- images

function shot(run: Run, link: Link | null, kind: "aircraft" | "su2", title: string, hiddenWhy?: string): Shot {
  if (!link) return { key: title, title, src: null, missing: "link 1 has no previous link" };
  const info = link.images[kind];
  const rep = info.replayed as { replay?: { CL?: number; CD?: number } } | undefined;
  const subtitle =
    kind === "su2" && rep ? (
      <>
        CL {fmt(info.CL as number)} · CD {fmt(info.CD as number)}
        <span className="tag" title="SU2 files were not saved during the run; this surface is from an offline re-solve of the same recorded mesh and config inputs">
          re-solved: CL {fmt(rep.replay?.CL ?? null)} · CD {fmt(rep.replay?.CD ?? null)}
        </span>
      </>
    ) : kind === "su2" && info.CL != null ? `CL ${fmt(info.CL as number)} · CD ${fmt(info.CD as number)}`
      : kind === "aircraft" && info.changed_this_link === false ? "geometry unchanged" : undefined;
  if (hiddenWhy) return { key: title, title, subtitle, src: null, missing: hiddenWhy };
  return {
    key: title, title, subtitle,
    src: info.file ? `/api/runs/${run.run_id}/img/${info.file}?v=${info.v ?? 1}` : null,
    missing: info.status,
  };
}

function Stat({ label, before, now, unit }: { label: string; before: number | null; now: number | null; unit?: string }) {
  const t = trend(before, now);
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-val">
        <span className="muted">{fmt(before)}</span> → <b className={`trend-${t}`}>{fmt(now)}</b>
        {unit && <span className="muted"> {unit}</span>}
      </span>
    </div>
  );
}

function trajEnd(phases: TrajPhase[] | undefined, key: keyof TrajPhase, mode: "last" | "max"): number | null {
  const vals = (phases ?? []).flatMap((p) => (p[key] as number[] | undefined) ?? []);
  if (!vals.length) return null;
  return mode === "max" ? Math.max(...vals) : vals[vals.length - 1];
}

// A link that did not fly leaves its failed attempt in aviary's recorder: not a mission, not plotted.
const flownPhases = (l: Link | null) => (l && l.summary.flown !== false ? l.images.trajectory.data?.phases : undefined);
const trajStatus = (l: Link | null) =>
  !l ? "—" : l.summary.flown === false ? "did not fly (the recorder's failed attempt is not plotted)" : l.images.trajectory.status;

export function Images({ run, link, prev, cursor }: { run: Run; link: Link; prev: Link | null; cursor: number }) {
  const t = cursorTime(link, cursor);
  // SU2 and the mission appear in link N once playback passes the call that produced them.
  const su2At = firstSuccess(link, "run_su2_solver", true);
  const simAt = firstSuccess(link, "run_simulation", true);
  const su2Shown = link.complete || (su2At !== null && t >= su2At);
  const simShown = link.complete || (simAt !== null && t >= simAt);
  const [y, setY] = useState("altitude");
  const nPrev = prev ? `Link ${prev.link} · N-1` : "N-1";
  const nNow = `Link ${link.link} · N`;
  const su2Hidden = !su2Shown && link.images.su2.file ? "SU2 not run yet at this step" : undefined;
  const now = simShown ? flownPhases(link) : undefined;
  const before = flownPhases(prev);
  const num = (v: unknown) => (typeof v === "number" ? v : null);
  return (
    <>
      <section className="panel images">
        <div className="panel-head"><h2>Aircraft</h2><span className="hint">TiGL · N-1 vs N · click to enlarge</span></div>
        <ShotPair group="Aircraft (TiGL)" shots={[shot(run, prev, "aircraft", nPrev), shot(run, link, "aircraft", `${nNow} (end)`)]} />
        <div className="panel-head"><h2>Simulation</h2><span className="hint">SU2 surface Cp · N-1 vs N</span></div>
        <ShotPair group="SU2 surface pressure coefficient" shots={[shot(run, prev, "su2", nPrev), shot(run, link, "su2", nNow, su2Hidden)]} />
      </section>
      <section className="panel traj-panel">
        <div className="panel-head">
          <h2>Mission flown</h2>
          <select value={y} onChange={(e) => setY(e.target.value)} aria-label="trajectory quantity">
            <option value="altitude">Altitude</option><option value="mach">Mach</option><option value="mass">Mass</option><option value="throttle">Throttle</option>
          </select>
        </div>
        <div className="legend"><span className="lg-now" /> N (link {link.link}) <span className="lg-prev" /> N-1{prev ? ` (link ${prev.link})` : ""}</div>
        <TrajectoryChart now={now} prev={before} yName={y} />
        <div className="stats">
          <Stat label="Fuel burned" before={num(prev?.summary.fuel_burned_kg)} now={simShown ? num(link.summary.fuel_burned_kg) : null} unit="kg" />
          <Stat label="GTOW" before={num(prev?.summary.gtow_kg)} now={simShown ? num(link.summary.gtow_kg) : null} unit="kg" />
          <Stat label="Flight time" before={trajEnd(before, "time_s", "last") && trajEnd(before, "time_s", "last")! / 3600} now={trajEnd(now, "time_s", "last") && trajEnd(now, "time_s", "last")! / 3600} unit="h" />
          <Stat label="Max altitude" before={trajEnd(before, "altitude_ft", "max")} now={trajEnd(now, "altitude_ft", "max")} unit="ft" />
        </div>
        <p className="foot">
          N: {simShown ? trajStatus(link) : "mission not flown yet at this step"} · N-1: {trajStatus(prev)}.
          From aviary's recorder (the session's last simulation).
        </p>
      </section>
    </>
  );
}

// ---------------------------------------------------------------- metrics column

function MetricBlock({ run, defs, pick, current, stepKey, link, cursor }: {
  run: Run; defs: MetricDef[]; pick: (row: Run["links"][number], key: string) => number | null; current: number;
  stepKey?: (k: string) => boolean; link: Link; cursor: number;
}) {
  return (
    <>
      {defs.map((d) => {
        const values = run.links.map((r) => pick(r, d.key));
        const live = stepKey?.(d.key) ? link.step_metrics.slice(0, cursor + 1).map((m) => (m[d.key] ?? null) as number | null) : null;
        return (
          <div className="metric" key={d.key}>
            <div className="metric-head">
              <span>{d.label}</span>
              <span className="metric-vals">
                {fmt(values[current - 2] ?? null)} → <b>{fmt(values[current - 1] ?? null)}</b> → {fmt(values[current] ?? null)}
                {d.unit ? ` ${d.unit}` : ""}
              </span>
            </div>
            <LinkChart values={values} current={current} />
            {live && <><Spark values={live} /><div className="muted small">running in link {current}: {fmt(live[live.length - 1])}</div></>}
          </div>
        );
      })}
    </>
  );
}

export function Metrics({ run, link, cursor }: { run: Run; link: Link; cursor: number }) {
  const cur = link.link;
  const stepKeys = new Set(Object.keys(link.step_metrics[0] ?? {}));
  const m = link.step_metrics[cursor] ?? {};
  return (
    <section className="panel metrics">
      <div className="panel-head">
        <h2>Metrics</h2>
        <span className="hint">N-1 → <b>N = link {cur}</b> → N+1 · window shaded</span>
      </div>
      <div className="metrics-scroll">
        <details open className="mgroup">
          <summary>Physics</summary>
          <MetricBlock run={run} defs={run.metric_defs.physics} current={cur} link={link} cursor={cursor}
            pick={(r, k) => (r[k] as number | null) ?? null} stepKey={(k) => k === "fuel_burned_kg" && stepKeys.has(k)} />
        </details>
        <details open className="mgroup">
          <summary>Coordination · {run.label}</summary>
          {run.metric_defs.step.length > 0 && (
            <div className="step-metrics">
              <div className="step-metrics-title">At this step</div>
              {run.metric_defs.step.map((d) => (
                <div key={d.key} className="kv"><span>{d.label}</span><b>{fmt(m[d.key] as number)}</b></div>
              ))}
            </div>
          )}
          <MetricBlock run={run} defs={run.metric_defs.coordination} current={cur} link={link} cursor={cursor}
            pick={(r, k) => r.coordination?.[k] ?? null} />
          <p className="foot">Per link from result.json org_theory_metrics; "at this step" recomputed from the step log.</p>
        </details>
        <details open className="mgroup">
          <summary>General</summary>
          <MetricBlock run={run} defs={run.metric_defs.general} current={cur} link={link} cursor={cursor}
            pick={(r, k) => (r[k] as number | null) ?? null} stepKey={(k) => stepKeys.has(k)} />
        </details>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- parameters

const GROUP_TITLES: Record<string, string> = { geometry: "Geometry", mission: "Mission", aero: "Aero", engine: "Engine" };

const ARROW: Record<string, string> = { up: "▲", down: "▼", same: "", unknown: "" };

function pct(before: number | null, after: number | null): string {
  if (before === null || after === null || before === 0 || after === before) return "";
  const p = ((after - before) / Math.abs(before)) * 100;
  return `${p > 0 ? "+" : ""}${Math.abs(p) >= 10 ? p.toFixed(0) : p.toFixed(1)}%`;
}

export function Params({ link, side }: { link: Link; side: "N" | "N+1" }) {
  const diff = paramDiff(link.params_start, link.params_end);
  const changed = Object.values(diff).flat().filter((p) => p.trend === "up" || p.trend === "down").length;
  return (
    <section className={`panel params side-${side === "N" ? "n" : "n1"}`}>
      <div className="panel-head">
        <h2>Parameters · {side}</h2>
        <span className="hint">
          {side === "N" ? `start of link ${link.link}` : link.params_end ? `end of link ${link.link} · ${changed} changed` : "link not finished"}
        </span>
      </div>
      <div className="param-grid">
        {Object.entries(diff).map(([g, rows]) => (
          <div className="param-card" key={g}>
            <h4>{GROUP_TITLES[g]}</h4>
            {rows.map((p) => (
              <div className="prow" key={p.key}>
                <span className="plabel">{p.label}</span>
                {side === "N" ? (
                  <b className={p.before === null ? "trend-unknown" : ""}>{fmt(p.before, p.unit)}</b>
                ) : (
                  <span className={`pval trend-${p.trend}`}>
                    {ARROW[p.trend] && <span className="arrow" aria-label={p.trend === "up" ? "increased" : "decreased"}>{ARROW[p.trend]}</span>}
                    <b>{fmt(p.value, p.unit)}</b>
                    {pct(p.before, p.value) && <span className="pct">{pct(p.before, p.value)}</span>}
                  </span>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- recommendation

export function Recommendation({ link, cursor }: { link: Link; cursor: number }) {
  const r = link.recommendation;
  const t = cursorTime(link, cursor);
  const responses = signalsSoFar(link.signals, t).filter((s) => s.kind === "response");
  const current = link.steps[cursor]?.agent;
  return (
    <section className="panel rec">
      <h2>Recommendation from MDO agent (link {link.link - 1})</h2>
      {!r ? (
        <p className="muted">{link.link === 1 ? "Link 1 has no previous link." : "No recommendation recorded in carry_over.json."}</p>
      ) : (
        <>
          <div className="rec-route">
            <span className="chip">{pretty(r.from)}</span> → <span className={`chip target ${current === r.to ? "now" : ""}`}>{pretty(r.to)}</span>
            {r.verdict && <span className="verdict">{r.verdict}</span>}
          </div>
          <p className="rec-change">{r.change ?? "(no change text)"}</p>
          {current === r.to && <div className="muted small">The addressed agent is acting now.</div>}
        </>
      )}
      {responses.length > 0 && (
        <div className="responses">
          <h4>Responses so far in this link</h4>
          {responses.map((s, k) => (
            <div key={k} className="resp"><span className="chip">{pretty(s.agent)}</span> {s.how_addressed}</div>
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- communication

export function Communication({ run, link, cursor }: { run: Run; link: Link; cursor: number }) {
  const items = commSoFar(link.comm, cursor);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight });
  }, [items.length]);
  const m = link.step_metrics[cursor] ?? {};
  const networked = run.org === "networked";
  return (
    <section className="panel comm">
      <h2>{networked ? "Networked Blackboard" : "Orchestrator Communication"}</h2>
      <div className="comm-list" ref={ref}>
        {items.length === 0 && <p className="muted">Nothing yet in this link.</p>}
        {items.map((c, k) => (
          <div key={k} className={`comm-item ${c.i === cursor ? "now" : ""} ${c.ok === false ? "refused" : ""}`}>
            <span className="muted small">#{c.i + 1}</span> <b>{pretty(c.src)}</b>
            {networked ? <> <code>{c.tool}</code>{c.todo ? <> <span className="chip">{c.todo}</span></> : null}{c.ok === false ? <span className="bad"> refused</span> : null}</> : <> → <b>{pretty(c.dst ?? c.tool)}</b> <code>{c.tool}</code></>}
            {c.text && <div className="comm-text">{c.text}</div>}
          </div>
        ))}
      </div>
      <div className="counter">
        {networked ? (
          ["read_blackboard", "read_todos", "write_blackboard", "claim_todo", "wait_for_board", "mark_todo_done", "mark_todo_failed", "mark_task_done"].map((k) => (
            <span key={k} className="count"><code>{k}</code> <b>{fmt(m[k] as number)}</b></span>
          ))
        ) : (
          <>
            <span className="count">orchestrator calls <b>{fmt(m.orchestrator_calls as number)}</b></span>
            <span className="count">to orchestrator <b>{fmt(m.messages_to_orchestrator as number)}</b></span>
            <span className="count">replies <b>{fmt(m.replies_from_orchestrator as number)}</b></span>
            <span className="count">tasks assigned <b>{fmt(m.tasks_assigned as number)}</b></span>
          </>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- bottom strip

function GraphView({ g, target }: { g: GraphModel; target: string | null }) {
  const w = Math.max(640, g.nodes.length * 150);
  const h = 170;
  const mid = 85;
  const x = (n: string) => 70 + g.nodes.indexOf(n) * ((w - 140) / Math.max(g.nodes.length - 1, 1));
  return (
    <svg className="graph" viewBox={`0 0 ${w} ${h}`} style={{ minWidth: w }}>
      <defs>
        <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" className="arrowhead" /></marker>
      </defs>
      {g.edges.map((e, k) => {
        const x1 = x(e.from);
        const x2 = x(e.to);
        const lift = (e.back ? 1 : -1) * (24 + (k % 4) * 9);
        const last = k === g.edges.length - 1;
        if (e.from === e.to) {
          return <path key={k} d={`M${x1 - 10},${mid - 22} C${x1 - 30},${mid - 70} ${x1 + 30},${mid - 70} ${x1 + 10},${mid - 22}`} className={`edge self ${last ? "last" : ""}`} markerEnd="url(#arr)" />;
        }
        return <path key={k} d={`M${x1},${mid + (e.back ? 20 : -20)} Q${(x1 + x2) / 2},${mid + lift * 2} ${x2},${mid + (e.back ? 20 : -20)}`} className={`edge ${e.back ? "back" : "fwd"} ${last ? "last" : ""}`} markerEnd="url(#arr)" />;
      })}
      {g.nodes.map((n) => {
        const visited = g.path.includes(n);
        return (
          <g key={n} className={`node ${n === g.current ? "now" : visited ? "visited" : "idle"} ${n === target ? "target" : ""}`}>
            <rect x={x(n) - 62} y={mid - 18} width={124} height={36} rx={18} />
            <text x={x(n)} y={mid + 4} textAnchor="middle">{pretty(n)}</text>
          </g>
        );
      })}
    </svg>
  );
}

export function Strip({ run, link, cursor, currentTurn }: { run: Run; link: Link; cursor: number; currentTurn: number }) {
  const target = link.recommendation?.to ?? null;
  const networked = run.org === "networked";
  const claimed = networked ? claimTurns(link, cursor) : null;
  const turns = claimed ? claimed.turns : link.turns;
  const turnIdx = claimed ? claimed.current : currentTurn;
  const by = claimed && turnIdx >= 0 ? ` (claimed by ${pretty(claimed.turns[turnIdx].agent)})` : "";
  if (run.handler === "graph_routed") {
    const g = graphModel(turns, turnIdx);
    return (
      <section className="panel strip">
        <h2>
          Graph-routed path{networked ? " (TODO roles claimed on the board)" : ""}{" "}
          <span className="muted">forward moves above, backward moves below (red); grey = not current</span>
        </h2>
        <div className="graph-wrap"><GraphView g={g} target={target} /></div>
        <div className="muted small">
          {g.path.length} {networked ? "claims" : "role runs"} so far{by} · {g.edges.filter((e) => e.back).length} backward moves
          {link.graph_stop_reason ? ` · stop: ${link.graph_stop_reason}` : ""}
        </div>
        {link.route_events.length > 0 && (
          <details className="route-events">
            <summary>Routing events ({link.route_events.length})</summary>
            {link.route_events.map((e, k) => <div key={k} className="small"><code>{e.event}</code> {pretty(e.from)}{e.to ? ` → ${pretty(e.to)}` : ""}: {e.reason}</div>)}
          </details>
        )}
      </section>
    );
  }
  // the strip shows workers; the orchestrator's own turns are left out
  const workers = turns.filter((t) => t.agent !== "orchestrator");
  const workerIdx = turns.slice(0, turnIdx + 1).filter((t) => t.agent !== "orchestrator").length - 1;
  const named = claimed ? workers.map((t) => ({ ...t, agent: t.route?.node ?? t.agent })) : workers;
  const nodes = run.handler === "staged_pipeline" ? pipelineStrip(named, workerIdx) : feedbackStrip(named, workerIdx);
  return (
    <section className="panel strip">
      <h2>
        {run.handler === "staged_pipeline" ? "Agents in sequence" : networked ? "TODO roles as peers claim them" : "Agents as they come up"}
        {by && <span className="muted"> {by}</span>}
      </h2>
      <div className="pipeline">
        {nodes.length === 0 && <span className="muted">{link.turns.length ? "orchestrator planning" : "no turns recorded yet"}</span>}
        {nodes.map((n, k) => (
          <span key={k} className="pipe-item">
            {k > 0 && <span className="pipe-arrow">→</span>}
            <span className={`pipe-node ${n.active ? "now" : ""} ${n.agent === target ? "target" : ""}`}>
              {pretty(n.agent)}{n.count > 1 ? ` ×${n.count}` : ""}
            </span>
          </span>
        ))}
      </div>
      {link.steps[cursor]?.agent === "orchestrator" && <div className="muted small">The orchestrator is acting at this step.</div>}
    </section>
  );
}
