import { useEffect, useMemo, useRef, useState } from "react";
import { LinkChart, Spark, TrajectoryChart } from "./charts";
import {
  agentStepsSoFar, claimTurns, commSoFar, cursorTime, feedbackStrip, firstSuccess, fmt, graphModel, paramDiff, pipelineStrip,
  signalsSoFar, type GraphModel,
} from "./playback";
import type { Link, MetricDef, Run, Step } from "./types";

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
        <h2>{pretty(step.agent)}</h2>
        <span className="muted">step {step.agent_step ?? "?"} of this agent · link step {cursor + 1}/{link.steps.length}</span>
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

function Img({ run, link, kind, label }: { run: Run; link: Link | null; kind: "aircraft" | "su2"; label: string }) {
  if (!link) return <figure className="img-cell"><figcaption>{label}</figcaption><div className="img-missing">no link</div></figure>;
  const info = link.images[kind];
  return (
    <figure className="img-cell">
      <figcaption>
        {label}
        {kind === "su2" && info.CL != null && <span className="muted"> · CL {fmt(info.CL as number)} CD {fmt(info.CD as number)}</span>}
        {kind === "aircraft" && info.changed_this_link === false && <span className="muted"> · unchanged</span>}
      </figcaption>
      {info.file ? <img src={`/api/runs/${run.run_id}/img/${info.file}`} alt={`${kind} link ${link.link}`} /> : <div className="img-missing">{info.status}</div>}
    </figure>
  );
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
  const pending = (what: string) => <div className="img-missing">{what} not run yet at this step</div>;
  return (
    <>
      <section className="panel images">
        <h2>Aircraft (TiGL) <span className="muted">N vs N-1</span></h2>
        <div className="pair">
          <Img run={run} link={prev} kind="aircraft" label={prev ? `Link ${prev.link} (N-1)` : "N-1"} />
          <Img run={run} link={link} kind="aircraft" label={`Link ${link.link} (N, end)`} />
        </div>
        <h2>Simulation (SU2 surface Cp) <span className="muted">N vs N-1</span></h2>
        <div className="pair">
          <Img run={run} link={prev} kind="su2" label={prev ? `Link ${prev.link} (N-1)` : "N-1"} />
          {su2Shown ? <Img run={run} link={link} kind="su2" label={`Link ${link.link} (N)`} /> : <figure className="img-cell"><figcaption>Link {link.link} (N)</figcaption>{link.images.su2.file ? pending("SU2") : <div className="img-missing">{link.images.su2.status}</div>}</figure>}
        </div>
      </section>
      <section className="panel traj-panel">
        <h2>Mission flown <span className="muted">N (solid) vs N-1 (dashed)</span></h2>
        <select value={y} onChange={(e) => setY(e.target.value)}>
          <option value="altitude">altitude</option><option value="mach">Mach</option><option value="mass">mass</option><option value="throttle">throttle</option>
        </select>
        <TrajectoryChart now={simShown ? flownPhases(link) : undefined} prev={flownPhases(prev)} yName={y} />
        <div className="muted small">
          N: {simShown ? trajStatus(link) : "mission not flown yet at this step"} · N-1: {trajStatus(prev)}
        </div>
        <div className="muted small">From aviary's recorder (last simulation of the session). Fuel at N: {fmt(link.summary.fuel_burned_kg as number, "kg")}{link.summary.flown === false ? " (did not fly)" : ""}</div>
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
      <h2>Metrics <span className="muted">links N-1, N, N+1 shaded</span></h2>
      <h3>Physics</h3>
      <MetricBlock run={run} defs={run.metric_defs.physics} current={cur} link={link} cursor={cursor}
        pick={(r, k) => (r[k] as number | null) ?? null} stepKey={(k) => k === "fuel_burned_kg" && stepKeys.has(k)} />
      <h3>Coordination ({run.label})</h3>
      {run.metric_defs.step.length > 0 && (
        <div className="step-metrics">
          {run.metric_defs.step.map((d) => (
            <div key={d.key} className="kv"><span>{d.label}</span><b>{fmt(m[d.key] as number)}</b></div>
          ))}
          <div className="muted small">running values at this step, from the step log</div>
        </div>
      )}
      <MetricBlock run={run} defs={run.metric_defs.coordination} current={cur} link={link} cursor={cursor}
        pick={(r, k) => r.coordination?.[k] ?? null} />
      <div className="muted small">per link, from result.json org_theory_metrics</div>
      <h3>General</h3>
      <MetricBlock run={run} defs={run.metric_defs.general} current={cur} link={link} cursor={cursor}
        pick={(r, k) => (r[k] as number | null) ?? null} stepKey={(k) => stepKeys.has(k)} />
    </section>
  );
}

// ---------------------------------------------------------------- parameters

const GROUP_TITLES: Record<string, string> = { geometry: "Geometry", mission: "Mission", aero: "Aero", engine: "Engine" };

export function Params({ link, side }: { link: Link; side: "N" | "N+1" }) {
  const diff = paramDiff(link.params_start, link.params_end);
  return (
    <section className="panel params">
      <h2>Parameters at {side === "N" ? `start of link ${link.link} (N)` : `end of link ${link.link} (N+1)`}</h2>
      {side === "N+1" && !link.params_end && <p className="muted">Link not finished; no end state yet.</p>}
      <div className="param-grid">
        {Object.entries(diff).map(([g, rows]) => (
          <div className="param-card" key={g}>
            <h4>{GROUP_TITLES[g]}</h4>
            {rows.map((p) => (
              <div className="kv" key={p.key}>
                <span>{p.label}</span>
                {side === "N" ? <b>{fmt(p.before, p.unit)}</b> : <b className={`trend-${p.trend}`}>{fmt(p.value, p.unit)}</b>}
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
  const h = 190;
  const mid = 95;
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
