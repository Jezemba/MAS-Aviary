// Pure playback logic: what is visible at the cursor (a step index within the link).
import type { CommEntry, Link, Param, ParamGroups, Signal, Step, Turn } from "./types";

export function cursorTime(link: Link, cursor: number): number {
  const s = link.steps[cursor];
  if (!s) return -Infinity;
  return s.end ?? s.start ?? -Infinity;
}

/** The agent turn the current step belongs to: the latest turn of that agent started by then. */
export function currentTurnIndex(turns: Turn[], step: Step | undefined): number {
  if (!step) return -1;
  const t = step.start ?? 0;
  let best = -1;
  turns.forEach((tn, i) => {
    if (tn.agent === step.agent && tn.start !== null && tn.start <= t + 1) best = i;
  });
  return best;
}

/** Steps of one agent up to the cursor (for the "Tools Called" dropdown). */
export function agentStepsSoFar(link: Link, cursor: number, agent: string): Step[] {
  return link.steps.slice(0, cursor + 1).filter((s) => s.agent === agent);
}

export function commSoFar(comm: CommEntry[], cursor: number): CommEntry[] {
  return comm.filter((c) => c.i <= cursor);
}

export function signalsSoFar(signals: Signal[], t: number): Signal[] {
  return signals.filter((s) => s.time !== undefined && s.time <= t);
}

/** First time a tool succeeded in this link, or null if it never did. */
export function firstSuccess(link: Link, tool: string, last = false): number | null {
  const hits = link.kb_calls.filter((c) => c.tool === tool && c.status === "success" && c.t !== null);
  if (!hits.length) return null;
  return (last ? hits[hits.length - 1] : hits[0]).t;
}

export type Trend = "up" | "down" | "same" | "unknown";

export function trend(before: number | null, after: number | null): Trend {
  if (before === null || after === null || before === undefined || after === undefined) return "unknown";
  const tol = 1e-9 * Math.max(1, Math.abs(before));
  if (after > before + tol) return "up";
  if (after < before - tol) return "down";
  return "same";
}

export function paramDiff(start: ParamGroups, end: ParamGroups | null): Record<string, (Param & { before: number | null; trend: Trend })[]> {
  const out: Record<string, (Param & { before: number | null; trend: Trend })[]> = {};
  for (const g of Object.keys(start) as (keyof ParamGroups)[]) {
    out[g] = start[g].map((p, k) => {
      const after = end ? end[g][k]?.value ?? null : null;
      return { ...p, value: after, before: p.value, trend: trend(p.value, after) };
    });
  }
  return out;
}

// ---------------------------------------------------------------- bottom strips

export interface StripNode {
  agent: string;
  count: number;
  active: boolean;
}

/** Staged pipeline: every stage of the link, greyed except the current one. */
export function pipelineStrip(turns: Turn[], currentTurn: number): StripNode[] {
  const cur = turns[currentTurn]?.agent;
  return turns.map((t, i) => ({ agent: t.agent, count: 1, active: i === currentTurn && t.agent === cur }));
}

/** Iterative feedback: turns appear as they happen; a repeat of the same agent becomes ×n. */
export function feedbackStrip(turns: Turn[], currentTurn: number): StripNode[] {
  const out: StripNode[] = [];
  turns.slice(0, currentTurn + 1).forEach((t) => {
    const last = out[out.length - 1];
    if (last && last.agent === t.agent) last.count += 1;
    else out.push({ agent: t.agent, count: 1, active: false });
  });
  if (out.length) out[out.length - 1].active = true;
  return out;
}

export interface GraphModel {
  nodes: string[];
  edges: { from: string; to: string; back: boolean; k: number }[];
  current: string | null;
  path: string[];
}

/** Graph-routed: nodes in first-visit order, every transition up to the cursor (back moves flagged). */
export function graphModel(turns: Turn[], currentTurn: number, allNodes = true): GraphModel {
  const seq = turns.map((t) => t.route?.node ?? t.agent).filter((a) => a !== "orchestrator");
  const nodes: string[] = [];
  seq.forEach((n) => {
    if (!nodes.includes(n)) nodes.push(n);
  });
  const upto = turns.slice(0, currentTurn + 1).map((t) => t.route?.node ?? t.agent).filter((a) => a !== "orchestrator");
  const edges = upto.slice(1).map((to, k) => {
    const from = upto[k];
    return { from, to, back: nodes.indexOf(to) <= nodes.indexOf(from), k };
  });
  return { nodes: allNodes ? nodes : nodes.filter((n) => upto.includes(n)), edges, current: upto[upto.length - 1] ?? null, path: upto };
}

/**
 * Networked runs: peers are generic (agent_1, agent_2), the role is the TODO they claim. The
 * path is the successful claim_todo calls in order; returns turn-like rows and the cursor's index.
 */
export function claimTurns(link: Link, cursor: number): { turns: Turn[]; current: number } {
  const claims = link.comm.filter((c) => c.tool === "claim_todo" && c.ok !== false && c.todo);
  const turns: Turn[] = claims.map((c) => ({
    agent: c.agent, turn: null, start: c.t, end: c.t, completion_met: null, attempt_number: null,
    aspiration_met: null, route: { node: c.todo as string, next: null, level: null, rule: null, reason: null }, error: false,
  }));
  return { turns, current: claims.filter((c) => c.i <= cursor).length - 1 };
}

export function fmt(v: number | null | undefined, unit = ""): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  const s = Number.isInteger(v) ? v.toLocaleString() : a >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : a >= 1 ? v.toFixed(2) : v.toPrecision(3);
  return unit ? `${s} ${unit}` : s;
}
