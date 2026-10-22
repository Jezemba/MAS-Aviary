import { describe, expect, it } from "vitest";
import { claimTurns, currentTurnIndex, feedbackStrip, graphModel, paramDiff, pipelineStrip, trend } from "./playback";
import type { ParamGroups, Step, Turn } from "./types";

const turn = (agent: string, start: number, node?: string): Turn => ({
  agent, turn: null, start, end: start + 5, completion_met: true, attempt_number: null, aspiration_met: null,
  route: node ? { node, next: null, level: null, rule: null, reason: null } : null, error: false,
});
const step = (agent: string, start: number): Step => ({
  i: 0, agent, agent_step: 1, start, end: start + 1, llm_output: null, generation_attempts: 1, prompt_tokens: 1,
  prompt_tokens_spent: 1, output_tokens: 1, tool_calls: [], observation: null, error: null, is_final_answer: false,
});

describe("trend", () => {
  it("colours up / down / same and refuses to guess on missing values", () => {
    expect(trend(1, 2)).toBe("up");
    expect(trend(2, 1)).toBe("down");
    expect(trend(2, 2)).toBe("same");
    expect(trend(null, 2)).toBe("unknown");
    expect(trend(2, null)).toBe("unknown");
  });
});

describe("paramDiff", () => {
  it("pairs N with N+1 per parameter", () => {
    const g = (v: number | null): ParamGroups => ({
      geometry: [{ key: "a", label: "A", unit: "", value: v }], mission: [], aero: [], engine: [],
    });
    const d = paramDiff(g(1), g(3));
    expect(d.geometry[0]).toMatchObject({ before: 1, value: 3, trend: "up" });
    expect(paramDiff(g(1), null).geometry[0].trend).toBe("unknown");
  });
});

describe("currentTurnIndex", () => {
  it("picks the latest turn of the step's agent that had started", () => {
    const turns = [turn("a", 0), turn("b", 10), turn("a", 20)];
    expect(currentTurnIndex(turns, step("a", 5))).toBe(0);
    expect(currentTurnIndex(turns, step("a", 25))).toBe(2);
    expect(currentTurnIndex(turns, step("b", 12))).toBe(1);
    expect(currentTurnIndex(turns, step("c", 12))).toBe(-1);
  });
});

describe("claimTurns", () => {
  it("builds the networked path from successful claims only", () => {
    const comm = [
      { i: 0, t: 1, agent: "agent_1", tool: "claim_todo", src: "agent_1", dst: "board", text: null, todo: "GEOMETRY_SETUP", ok: true },
      { i: 1, t: 2, agent: "agent_2", tool: "claim_todo", src: "agent_2", dst: "board", text: null, todo: "GEOMETRY_SETUP", ok: false },
      { i: 2, t: 3, agent: "agent_2", tool: "read_todos", src: "agent_2", dst: "board", text: null, todo: null, ok: null },
      { i: 3, t: 4, agent: "agent_2", tool: "claim_todo", src: "agent_2", dst: "board", text: null, todo: "MISSION_CONFIG", ok: true },
    ];
    const link = { comm } as unknown as import("./types").Link;
    const r = claimTurns(link, 2);
    expect(r.turns.map((t) => t.route?.node)).toEqual(["GEOMETRY_SETUP", "MISSION_CONFIG"]);
    expect(r.current).toBe(0);
    expect(claimTurns(link, 3).current).toBe(1);
  });
});

describe("strips", () => {
  const turns = [turn("geo", 0), turn("aero", 10), turn("aero", 20), turn("mass", 30)];
  it("pipeline shows every stage, only the current active", () => {
    const s = pipelineStrip(turns, 1);
    expect(s.map((n) => n.agent)).toEqual(["geo", "aero", "aero", "mass"]);
    expect(s.filter((n) => n.active).length).toBe(1);
  });
  it("feedback grows as agents come up and folds repeats into ×n", () => {
    expect(feedbackStrip(turns, 0).map((n) => [n.agent, n.count])).toEqual([["geo", 1]]);
    expect(feedbackStrip(turns, 2).map((n) => [n.agent, n.count])).toEqual([["geo", 1], ["aero", 2]]);
  });
  it("graph flags backward moves and skips the orchestrator", () => {
    const g = graphModel([turn("orchestrator", 0), turn("x", 1, "geo"), turn("x", 2, "aero"), turn("x", 3, "geo")], 3);
    expect(g.nodes).toEqual(["geo", "aero"]);
    expect(g.edges.map((e) => [e.from, e.to, e.back])).toEqual([["geo", "aero", false], ["aero", "geo", true]]);
    expect(g.current).toBe("geo");
  });
});
