// Domain types for the MAS-Aviary GUI. Mirrors the data contract in the
// handoff README (§ State Management → Server state).

export type Status = "pending" | "running" | "success" | "partial" | "failed";
export type ToolStatus = "pending" | "running" | "success" | "error" | "retry";

export type AgentId =
  | "geometry_engineer"
  | "aerodynamics_analyst"
  | "structures_analyst"
  | "propulsion_analyst"
  | "mission_architect"
  | "simulation_executor"
  | "mdo_integrator";

export interface Agent {
  id: AgentId;
  name: string;
  short: string;
  mcp: string; // "tigl-mcp" | ... | "-"
  emoji: string;
  role: string;
  slow?: boolean;
}

export interface McpTool {
  name: string;
  desc: string;
}

export interface StructureDef {
  id: string;
  label: string;
  available: boolean;
  desc: string;
}

export interface HandlerDef {
  id: string;
  label: string;
  available: boolean;
  desc: string;
}

export interface MissionPreset {
  id: string;
  label: string;
  desc: string;
  target?: {
    range_nmi: number;
    pax: number;
    mach: number;
    alt_ft: number;
    fuel_kg: number;
  };
}

export interface GraphEdge {
  from: AgentId;
  to: AgentId;
  label: string;
  retry?: boolean;
}

export interface ToolCall {
  name: string;
  mcp: string;
  dur_s: number;
  status: ToolStatus;
  desc: string; // narrated_summary from narration.py
}

export interface Iter {
  iter: number; // 1-based invocation_index
  duration_s: number;
  tokens_in: number;
  tokens_out: number;
  summary: string | null; // one-line headline numbers (collapsed view)
  intro: string; // first-person, from AGENT_INTROS
  currently?: { tool: string; desc: string; elapsed_s: number };
  design_state?: Record<string, string | number>; // final_answer
  tools: ToolCall[];
}

export interface Stage {
  stage: AgentId;
  status: Status;
  iters: Iter[];
}

export interface RunMeta {
  combo: string;
  run_id: string;
  structure: string;
  handler: string;
  mission: string;
  model: string;
  output_dir: string;
  wandb_url: string;
  started_at: string; // ISO 8601
  elapsed_s: number;
  total_tokens_in: number;
  total_tokens_out: number;
  tokens_budget: number; // 200_000 for Sonnet
  initial_params: Record<string, number>;
}

export interface RunResult {
  status: "completed" | "failed" | "timeout" | "manually_stopped";
  fuel_burned_kg: number;
  gross_mass_kg: number;
  ld_cruise: number;
  ld_fallback: boolean;
  converged: boolean;
  constraints_failed: string[];
  optimality_gap_pct: number;
}

export interface RunReference {
  fuel_burned_kg: number;
  gross_mass_kg: number;
  ld_cruise: number;
}

export interface PerStageRow {
  stage: AgentId;
  status: Status;
  iters: number;
  tokens: number;
  duration_s: number;
  note?: string;
}

export interface Run {
  meta: RunMeta;
  stages: Stage[];
  result?: RunResult;
  reference?: RunReference;
  per_stage?: PerStageRow[];
}

export type RunPhase = "idle" | "running" | "paused" | "done" | "failed";
