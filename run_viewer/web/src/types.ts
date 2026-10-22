// Shapes of the bundle written by run_viewer/extract/build.py.

export interface CatalogEntry {
  run_id: string;
  label: string | null;
  note: string | null;
  live: boolean;
  combination: string;
  built: boolean;
  error: string | null;
  title: string | null;
  inputs: RunInputs | null;
  links_built: number;
  built_utc: string | null;
}

export interface RunInputs {
  links_planned: number | null;
  links_present: number;
  model: string | null;
  thinking: boolean | null;
  chain_mode: string | null;
  seed: number | null;
}

export interface MetricDef {
  key: string;
  label: string;
  unit?: string;
}

export interface LinkRow {
  link: number;
  repeat: string;
  complete: boolean;
  status: string;
  flown: boolean | null;
  eval_result: string | null;
  coordination: Record<string, number | null>;
  cross_strategy: Record<string, number | null>;
  [key: string]: unknown;
}

export interface Run {
  run_id: string;
  combination: string;
  label: string;
  title: string;
  org: "sequential" | "orchestrated" | "networked";
  handler: "staged_pipeline" | "iterative_feedback" | "graph_routed";
  live: boolean;
  inputs: RunInputs;
  token_budget: { prompt_budget: number; hard_limit: number };
  links: LinkRow[];
  metric_defs: {
    physics: MetricDef[];
    general: MetricDef[];
    coordination: MetricDef[];
    cross_strategy: MetricDef[];
    step: MetricDef[];
  };
  built_utc: string;
}

export interface ToolCall {
  name: string;
  arguments: string | null;
}

export interface Step {
  i: number;
  agent: string;
  agent_step: number | null;
  start: number | null;
  end: number | null;
  llm_output: string | null;
  generation_attempts: number;
  prompt_tokens: number | null;
  prompt_tokens_spent: number;
  output_tokens: number;
  tool_calls: ToolCall[];
  observation: string | null;
  error: string | null;
  is_final_answer: boolean;
}

export interface Turn {
  agent: string;
  turn: number | null;
  start: number | null;
  end: number | null;
  completion_met: boolean | null;
  attempt_number: number | null;
  aspiration_met: boolean | null;
  route: { node: string; next: string | null; level: string | null; rule: string | null; reason: string | null } | null;
  error: boolean;
}

export interface CommEntry {
  i: number;
  t: number | null;
  agent: string;
  tool: string;
  src: string;
  dst: string | null;
  text: string | null;
  todo?: string | null;
  ok?: boolean | null;
}

export interface Param {
  key: string;
  label: string;
  unit: string;
  value: number | null;
}

export type ParamGroups = Record<"geometry" | "mission" | "aero" | "engine", Param[]>;

export interface KbCall {
  seq: number;
  t: number | null;
  agent: string;
  tool: string;
  server: string;
  status: string;
  error_code: string | null;
}

export interface TrajPhase {
  name: string;
  time_s?: number[];
  altitude_ft?: number[];
  mach?: number[];
  mass_kg?: number[];
  distance_nmi?: number[];
  throttle?: number[];
  drag_lbf?: number[];
  thrust_lbf?: number[];
}

export interface ImageInfo {
  status: string;
  file?: string | null;
  note?: string;
  changed_this_link?: boolean;
  CL?: number | null;
  CD?: number | null;
  solves_in_link?: number;
  [key: string]: unknown;
}

export interface Signal {
  kind: string;
  agent?: string;
  time?: number;
  how_addressed?: string;
  recommendation_to?: string;
  recommended_change?: string;
  verdict?: string;
  level?: string;
  reason?: string;
}

export interface Link {
  link: number;
  repeat: string;
  attempt: number;
  complete: boolean;
  summary: LinkRow;
  steps: Step[];
  step_metrics: Record<string, number | null>[];
  turns: Turn[];
  route_events: { event: string; from: string | null; to: string | null; reason: string | null; reopened: string[] | null }[];
  graph_stop_reason: string | null;
  comm: CommEntry[];
  physics_timeline: { t: number | null; flown: boolean | null; fuel_burned_kg: number | null }[];
  kb_calls: KbCall[];
  params_start: ParamGroups;
  params_end: ParamGroups | null;
  recommendation: { from: string; to: string | null; verdict: string | null; change: string | null; integrator_state: string | null } | null;
  signals: Signal[];
  images: {
    aircraft: ImageInfo;
    su2: ImageInfo;
    trajectory: { status: string; session?: string; data: { phases: TrajPhase[] } | null };
  };
}
