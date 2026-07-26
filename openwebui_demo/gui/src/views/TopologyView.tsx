// Topology - animated "spawn" visualization of each org structure. Ported from
// the prototype's topology.jsx. Shows how agents come up, which MCP each binds
// to, and the tools it holds. Honors prefers-reduced-motion (spawn immediately).

import { Fragment, useEffect, useRef, useState } from "react";

import { AGENTS, GRAPH_EDGES, MCP_TOOLS, TOPOLOGY_PATTERNS, agentById } from "../data/reference";
import type { Agent, AgentId } from "../data/types";

const prefersReducedMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// Fade/scale-in after `delay` ms; re-armed when `epoch` changes.
function useSpawn(delay: number, epoch: number): boolean {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (prefersReducedMotion()) {
      setShown(true);
      return;
    }
    setShown(false);
    const id = setTimeout(() => setShown(true), delay);
    return () => clearTimeout(id);
  }, [delay, epoch]);
  return shown;
}

function SpawnSlot({
  children,
  delay,
  epoch,
  className = "",
  style,
}: {
  children: React.ReactNode;
  delay: number;
  epoch: number;
  className?: string;
  style?: React.CSSProperties;
}) {
  const shown = useSpawn(delay, epoch);
  return (
    <div className={`${className} ${shown ? "is-shown" : ""}`} style={style}>
      {children}
    </div>
  );
}

function AgentCardSpawn({
  agent,
  delay = 0,
  epoch = 0,
  compact = false,
  accent = false,
  nameOverride,
  roleOverride,
}: {
  agent: Agent;
  delay?: number;
  epoch?: number;
  compact?: boolean;
  accent?: boolean;
  nameOverride?: string;
  roleOverride?: string;
}) {
  const shown = useSpawn(delay, epoch);
  const tools = MCP_TOOLS[agent.mcp] ?? [];
  return (
    <div className={`topo-card ${accent ? "accent" : ""} ${shown ? "is-shown" : ""}`}>
      <div className="topo-card-head">
        <span className="agent-emoji" style={{ width: 30, height: 30, fontSize: 16 }} aria-hidden>
          {agent.emoji}
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="topo-card-name">{nameOverride ?? agent.name}</div>
          <div className="topo-card-mcp">{agent.mcp}</div>
        </div>
      </div>
      {!compact && <p className="topo-card-role">{roleOverride ?? agent.role}</p>}
      {tools.length > 0 ? (
        <div className="topo-tools">
          {tools.map((t) => (
            <span key={t.name} className="topo-tool" title={t.desc}>
              {t.name}
            </span>
          ))}
        </div>
      ) : (
        <div className="topo-tools-empty">no MCP - synthesizes results in-context</div>
      )}
    </div>
  );
}

// SVG cubic path that animates its stroke-dashoffset → 0 once shown.
function DrawnPath({
  d,
  delay,
  epoch,
  stroke = "var(--line)",
  strokeWidth = 1.5,
  markerEnd,
  dashed = false,
}: {
  d: string;
  delay: number;
  epoch: number;
  stroke?: string;
  strokeWidth?: number;
  markerEnd?: string;
  dashed?: boolean;
}) {
  const ref = useRef<SVGPathElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (prefersReducedMotion()) {
      el.style.strokeDasharray = dashed ? "4 4" : "";
      el.style.strokeDashoffset = "0";
      return;
    }
    const len = el.getTotalLength();
    el.style.strokeDasharray = String(len);
    el.style.strokeDashoffset = String(len);
    el.getBoundingClientRect(); // force reflow
    const id = setTimeout(() => {
      el.style.transition = "stroke-dashoffset 600ms ease";
      el.style.strokeDashoffset = "0";
      // Restore the intended dash pattern (retry edges) after the draw-in.
      if (dashed) {
        setTimeout(() => {
          el.style.transition = "";
          el.style.strokeDasharray = "4 4";
          el.style.strokeDashoffset = "0";
        }, 650);
      }
    }, delay);
    return () => clearTimeout(id);
  }, [delay, epoch, d, dashed]);
  return (
    <path ref={ref} d={d} stroke={stroke} strokeWidth={strokeWidth} fill="none" markerEnd={markerEnd} />
  );
}

function DrawnLine({
  x1,
  y1,
  x2,
  y2,
  delay,
  epoch,
  stroke = "var(--line)",
}: {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  delay: number;
  epoch: number;
  stroke?: string;
}) {
  const len = Math.hypot(x2 - x1, y2 - y1);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (prefersReducedMotion()) {
      setShown(true);
      return;
    }
    setShown(false);
    const id = setTimeout(() => setShown(true), delay);
    return () => clearTimeout(id);
  }, [delay, epoch]);
  return (
    <line
      x1={x1}
      y1={y1}
      x2={x2}
      y2={y2}
      stroke={stroke}
      strokeWidth="1.5"
      strokeDasharray={len}
      strokeDashoffset={shown ? 0 : len}
      style={{ transition: "stroke-dashoffset 600ms ease" }}
    />
  );
}

// ─── 1. Sequential ───────────────────────────────────────────────────────────
function TopoSequential({ epoch }: { epoch: number }) {
  return (
    <div className="topo-seq" key={epoch}>
      <div className="topo-seq-track">
        {AGENTS.map((a, i) => (
          <Fragment key={a.id}>
            <SpawnSlot delay={i * 280} epoch={epoch} className="topo-seq-slot">
              <div className="topo-seq-num">{i + 1}</div>
              <AgentCardSpawn agent={a} delay={i * 280} epoch={epoch} />
            </SpawnSlot>
            {i < AGENTS.length - 1 && (
              <SpawnSlot delay={i * 280 + 220} epoch={epoch} className="topo-seq-arrow-wrap">
                <svg className="topo-seq-arrow" width="22" height="100" viewBox="0 0 22 100">
                  <line x1="2" y1="50" x2="18" y2="50" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                  <path d="M 14 45 L 19 50 L 14 55" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </SpawnSlot>
            )}
          </Fragment>
        ))}
      </div>
      <div className="topo-caption">
        <b>Linear pipeline.</b> Each stage runs once (or iteratively under iterative_feedback) and
        hands off via <span className="mono">DesignState</span>. Exactly one agent is active at any
        time.
      </div>
    </div>
  );
}

// ─── 2. Orchestrated ─────────────────────────────────────────────────────────
function TopoOrchestrated({ epoch }: { epoch: number }) {
  const coord = agentById("mdo_integrator")!;
  const specialists = AGENTS.filter((a) => a.id !== "mdo_integrator");
  return (
    <div className="topo-orch" key={epoch}>
      <SpawnSlot delay={0} epoch={epoch} className="topo-orch-head">
        <div className="topo-coord-badge">Coordinator</div>
        <AgentCardSpawn
          agent={coord}
          delay={0}
          epoch={epoch}
          accent
          nameOverride="Coordinator (mdo_integrator)"
          roleOverride="Decides which specialist to call next based on returned results. Synthesizes once all sub-tasks resolve."
        />
      </SpawnSlot>

      <svg className="topo-orch-lines" viewBox="0 0 600 80" preserveAspectRatio="none">
        {specialists.map((_, i) => {
          const x2 = ((i + 0.5) / specialists.length) * 600;
          return <DrawnPath key={i} d={`M 300 0 C 300 40, ${x2} 40, ${x2} 80`} delay={600 + i * 80} epoch={epoch} />;
        })}
      </svg>

      <div className="topo-orch-row">
        {specialists.map((a, i) => (
          <SpawnSlot key={a.id} delay={1000 + i * 140} epoch={epoch} className="topo-orch-cell">
            <AgentCardSpawn agent={a} delay={1000 + i * 140} epoch={epoch} compact />
          </SpawnSlot>
        ))}
      </div>

      <div className="topo-caption">
        <b>One-to-many delegation.</b> The coordinator stays omnipresent; specialists are spawned on
        demand to handle a sub-task and return their result to the coordinator. One specialist active
        per phase.
      </div>
    </div>
  );
}

// ─── 3. Networked ────────────────────────────────────────────────────────────
function TopoNetworked({ epoch }: { epoch: number }) {
  const N = AGENTS.length;
  const R = 230;
  const CX = 360;
  const CY = 280;
  const positions = AGENTS.map((_, i) => {
    const angle = (i / N) * Math.PI * 2 - Math.PI / 2;
    return { x: CX + Math.cos(angle) * R, y: CY + Math.sin(angle) * R };
  });
  return (
    <div className="topo-net" key={epoch}>
      <div className="topo-net-canvas">
        <svg className="topo-net-svg" viewBox="0 0 720 560">
          {positions.map((p, i) => (
            <DrawnLine key={i} x1={CX} y1={CY} x2={p.x} y2={p.y} delay={500 + i * 60} epoch={epoch} />
          ))}
        </svg>

        <SpawnSlot delay={0} epoch={epoch} className="topo-net-bb" style={{ left: CX, top: CY }}>
          <div className="topo-bb-inner">
            <div className="topo-bb-title">Blackboard</div>
            <div className="topo-bb-sub">shared DesignState</div>
            <div className="topo-bb-pulse" />
          </div>
        </SpawnSlot>

        {AGENTS.map((a, i) => (
          <SpawnSlot
            key={a.id}
            delay={300 + i * 100}
            epoch={epoch}
            className="topo-net-node"
            style={{ left: positions[i].x, top: positions[i].y }}
          >
            <AgentCardSpawn agent={a} compact delay={300 + i * 100} epoch={epoch} />
          </SpawnSlot>
        ))}
      </div>
      <div className="topo-caption">
        <b>Blackboard pattern.</b> Every agent both posts to and subscribes from the shared state.
        Multiple agents may run in parallel; ordering emerges from message flow rather than wiring.
      </div>
    </div>
  );
}

// ─── 4. Graph ────────────────────────────────────────────────────────────────
const GRAPH_LAYOUT: Record<AgentId, { x: number; y: number }> = {
  geometry_engineer: { x: 80, y: 80 },
  aerodynamics_analyst: { x: 290, y: 80 },
  structures_analyst: { x: 500, y: 80 },
  propulsion_analyst: { x: 710, y: 80 },
  mission_architect: { x: 80, y: 270 },
  simulation_executor: { x: 290, y: 270 },
  mdo_integrator: { x: 500, y: 270 },
};
const NODE_W = 180;
const NODE_H = 110;

function edgePath(from: AgentId, to: AgentId, retry?: boolean): string {
  const p1 = GRAPH_LAYOUT[from];
  const p2 = GRAPH_LAYOUT[to];
  const x1 = p1.x + NODE_W / 2;
  const y1 = p1.y + NODE_H;
  const x2 = p2.x + NODE_W / 2;
  const y2 = p2.y;
  if (retry) {
    return `M ${x1} ${p1.y + NODE_H / 2} C ${x1 + 80} ${p1.y - 60}, ${x2 - 80} ${p2.y - 60}, ${x2} ${p2.y + NODE_H / 2}`;
  }
  if (Math.abs(y1 - y2) < 30) {
    const yy = p1.y + NODE_H / 2;
    return `M ${p1.x + NODE_W} ${yy} L ${p2.x} ${yy}`;
  }
  return `M ${x1} ${y1} C ${x1} ${y1 + 50}, ${x2} ${y2 - 50}, ${x2} ${y2}`;
}

function TopoGraph({ epoch }: { epoch: number }) {
  return (
    <div className="topo-graph" key={epoch}>
      <div className="topo-graph-canvas">
        <svg className="topo-graph-svg" viewBox="0 0 920 420">
          <defs>
            <marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 Z" fill="var(--ink-3)" />
            </marker>
            <marker id="arr-retry" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 Z" fill="var(--warn)" />
            </marker>
          </defs>
          {GRAPH_EDGES.map((e, i) => {
            const d = edgePath(e.from, e.to, e.retry);
            const p1 = GRAPH_LAYOUT[e.from];
            const p2 = GRAPH_LAYOUT[e.to];
            const lbl = e.retry
              ? { x: (p1.x + p2.x) / 2 + NODE_W / 2, y: Math.min(p1.y, p2.y) - 30 }
              : { x: (p1.x + p2.x + NODE_W) / 2, y: (p1.y + p2.y + NODE_H) / 2 };
            return (
              <g key={i}>
                <DrawnPath
                  d={d}
                  stroke={e.retry ? "var(--warn)" : "var(--ink-3)"}
                  delay={1100 + i * 120}
                  epoch={epoch}
                  markerEnd={e.retry ? "url(#arr-retry)" : "url(#arr)"}
                  dashed={e.retry}
                />
                <g transform={`translate(${lbl.x}, ${lbl.y})`}>
                  <rect x="-44" y="-9" width="88" height="18" rx="9" fill="var(--surface)" stroke={e.retry ? "var(--warn)" : "var(--line)"} strokeWidth="1" />
                  <text x="0" y="3.5" textAnchor="middle" className="topo-edge-label" fill={e.retry ? "var(--warn)" : "var(--ink-3)"}>
                    {e.label}
                  </text>
                </g>
              </g>
            );
          })}
        </svg>

        {AGENTS.map((a, i) => {
          const pos = GRAPH_LAYOUT[a.id];
          return (
            <SpawnSlot
              key={a.id}
              delay={i * 130}
              epoch={epoch}
              className="topo-graph-node"
              style={{ left: pos.x, top: pos.y, width: NODE_W, height: NODE_H }}
            >
              <div className="topo-graph-node-inner">
                <span className="agent-emoji" style={{ width: 26, height: 26, fontSize: 14 }} aria-hidden>
                  {a.emoji}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div className="topo-graph-node-name">{a.short}</div>
                  <div className="topo-graph-node-mcp">{a.mcp}</div>
                </div>
              </div>
            </SpawnSlot>
          );
        })}
      </div>
      <div className="topo-caption">
        <b>Explicit state machine.</b> Each stage's output triggers a named transition. Edges are
        documented per stage; non-linear flows (loops, branches) are first-class.
      </div>
    </div>
  );
}

const RENDERERS: Record<string, ({ epoch }: { epoch: number }) => React.ReactElement> = {
  sequential: TopoSequential,
  orchestrated: TopoOrchestrated,
  networked: TopoNetworked,
  graph: TopoGraph,
};

export function TopologyView({
  selectedStructure,
  pattern,
  onPattern,
  canLaunch,
  onLaunch,
}: {
  selectedStructure: string;
  pattern: string;
  onPattern: (p: string) => void;
  canLaunch: boolean;
  onLaunch: () => void;
}) {
  const [epoch, setEpoch] = useState(0);
  // Re-trigger the spawn animation whenever the previewed pattern changes.
  useEffect(() => {
    setEpoch((e) => e + 1);
  }, [pattern]);

  const meta = TOPOLOGY_PATTERNS.find((s) => s.id === pattern)!;
  const Renderer = RENDERERS[pattern] ?? TopoSequential;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Topology</h1>
          <p className="page-sub">
            How the crew spawns. Pick a pattern to preview the wiring; this run will launch with{" "}
            <span className="mono">{selectedStructure}</span>.
          </p>
        </div>
        <div className="topo-actions">
          <button className="btn btn-sm" onClick={() => setEpoch((e) => e + 1)}>
            ↻ Replay spawn
          </button>
          <button className="btn btn-sm btn-primary" onClick={onLaunch} disabled={!canLaunch}>
            Launch run →
          </button>
        </div>
      </div>

      <div className="topo-tabs">
        {TOPOLOGY_PATTERNS.map((s) => (
          <button
            key={s.id}
            className={`topo-tab ${pattern === s.id ? "is-active" : ""}`}
            onClick={() => onPattern(s.id)}
          >
            <span className="topo-tab-label">{s.label}</span>
            {s.id === selectedStructure ? (
              <span className="topo-tab-flag is-selected">selected</span>
            ) : (
              <span className="topo-tab-flag">preview</span>
            )}
          </button>
        ))}
      </div>

      <div className="topo-meta">
        <div>
          <div className="topo-meta-eyebrow">{meta.label} pattern</div>
          <p className="topo-meta-desc">{meta.desc}</p>
        </div>
        <dl className="kv">
          <dt>Agents</dt>
          <dd>{pattern === "orchestrated" ? "1 coord + 6 specialists" : "7"}</dd>
          <dt>MCPs</dt>
          <dd>5 (tigl, su2, mass, pycycle, aviary)</dd>
          <dt>Active at once</dt>
          <dd>
            {pattern === "sequential"
              ? "1"
              : pattern === "orchestrated"
                ? "1 + coord"
                : pattern === "networked"
                  ? "multiple"
                  : "1 per edge"}
          </dd>
        </dl>
      </div>

      <div className="topo-stage">
        <Renderer epoch={epoch} />
      </div>
    </div>
  );
}
