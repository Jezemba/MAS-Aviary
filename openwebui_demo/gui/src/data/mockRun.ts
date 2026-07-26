// Mock run fixtures - ported from the design prototype's data.jsx (RUN /
// RUN_DONE). Used to build the Live/Results views before the real SSE stream
// is wired. Numbers are the brief's reference example run.
//
// NOTE: this fixture uses the design prototype's simplified tool names on the
// Live stage records so it matches the reference screenshots exactly. The real
// SSE stream (useRunStream) surfaces the actual MCP tool names via narration.py.

import type { Run } from "./types";
import { F25_REFERENCE, TOKENS_BUDGET } from "./reference";

export const MOCK_RUN: Run = {
  meta: {
    combo: "mdo_f25_sequential_iterative_feedback",
    run_id: "t4kdv9om",
    structure: "sequential",
    handler: "iterative_feedback",
    mission: "DLR-F25",
    model: "claude-sonnet-4-20250514",
    output_dir: "logs/stat_results/1778527085/",
    wandb_url: "https://wandb.ai/jessicae/mas-aviary-stat/runs/t4kdv9om",
    started_at: "2026-05-11T15:23:01Z",
    elapsed_s: 187,
    total_tokens_in: 184_523,
    total_tokens_out: 4_812,
    tokens_budget: TOKENS_BUDGET,
    initial_params: { AREA: 130.1, ASPECT_RATIO: 11.0, SCALE_FACTOR: 1.3, SEED: 42 },
  },
  stages: [
    {
      stage: "geometry_engineer",
      status: "success",
      iters: [
        {
          iter: 1, duration_s: 52, tokens_in: 28_412, tokens_out: 762,
          summary: "351 k cells · span 33.9 m · AR 18.7 · AREA 61.4 m²",
          intro: "I read the CPACS baseline, recorded design intent at AR 11.0 / AREA 130 m², and built a CFD-ready volume mesh.",
          design_state: {
            TIGL_SESSION_ID: "da272edf-2008-4627-9518-55a480e5c62a",
            CPACS_FILE: ".../D150_simple.xml",
            WING_SPAN_M: 33.91, WING_AREA_M2: 61.39,
            MESH_CELLS: 351391, MESH_NODES: 62569,
          },
          tools: [
            { name: "open_cpacs",           mcp: "tigl-mcp", dur_s: 0.9,  status: "success", desc: "Loaded CPACS baseline" },
            { name: "set_high_level_parameters", mcp: "tigl-mcp", dur_s: 2.1, status: "success", desc: "Recorded AR 11.0, AREA 130.1, scale 1.3" },
            { name: "export_configuration_cad", mcp: "tigl-mcp", dur_s: 4.2, status: "success", desc: "Exported STEP geometry" },
            { name: "get_wing_summary",     mcp: "tigl-mcp", dur_s: 1.4,  status: "success", desc: "Read wing summary - span 33.9 m" },
            { name: "generate_volume_mesh", mcp: "tigl-mcp", dur_s: 22.8, status: "success", desc: "Volume mesh - 351 k cells" },
          ],
        },
      ],
    },
    {
      stage: "aerodynamics_analyst",
      status: "running",
      iters: [
        {
          iter: 1, duration_s: 73, tokens_in: 31_840, tokens_out: 901,
          summary: null,
          intro: "I'm configuring SU2 for Mach 0.78 / AoA 2° / Euler, and running the solver to pull CL, CD, L/D from the residual history.",
          currently: { tool: "run_su2_solver", desc: "Running SU2_CFD (Euler, 200 iter)…", elapsed_s: 72 },
          tools: [
            { name: "create_su2_session",    mcp: "su2-mcp", dur_s: 1.5, status: "success", desc: "Initialized the SU2 workspace" },
            { name: "set_mesh",              mcp: "su2-mcp", dur_s: 3.2, status: "success", desc: "Loaded volume mesh into SU2" },
            { name: "update_config_entries", mcp: "su2-mcp", dur_s: 0.8, status: "success", desc: "Configured Mach 0.78 / AoA 2°" },
            { name: "run_su2_solver",        mcp: "su2-mcp", dur_s: 72,  status: "running", desc: "Running SU2_CFD (Euler, 200 iter)" },
            { name: "read_history_csv",      mcp: "su2-mcp", dur_s: 0,   status: "pending", desc: "Read residual convergence trace" },
          ],
        },
      ],
    },
    { stage: "structures_analyst",  status: "pending", iters: [] },
    { stage: "propulsion_analyst",  status: "pending", iters: [] },
    { stage: "mission_architect",   status: "pending", iters: [] },
    { stage: "simulation_executor", status: "pending", iters: [] },
    { stage: "mdo_integrator",      status: "pending", iters: [] },
  ],
};

export const MOCK_RUN_DONE: Run = {
  meta: { ...MOCK_RUN.meta, elapsed_s: 724, total_tokens_in: 1_241_902, total_tokens_out: 12_847 },
  stages: MOCK_RUN.stages,
  result: {
    status: "completed",
    fuel_burned_kg: 12747.47,
    gross_mass_kg: 73779.45,
    ld_cruise: 19.5,
    ld_fallback: true,
    converged: true,
    constraints_failed: [],
    optimality_gap_pct: 5.3,
  },
  reference: {
    fuel_burned_kg: F25_REFERENCE.fuel_burned_kg,
    gross_mass_kg: F25_REFERENCE.gross_mass_kg,
    ld_cruise: F25_REFERENCE.ld_cruise,
  },
  per_stage: [
    { stage: "geometry_engineer",    status: "success", iters: 1, tokens: 1845,  duration_s: 52 },
    { stage: "aerodynamics_analyst", status: "partial", iters: 1, tokens: 2402,  duration_s: 184, note: "SU2 underconverged - used fallback L/D" },
    { stage: "structures_analyst",   status: "success", iters: 1, tokens: 720,   duration_s: 24 },
    { stage: "propulsion_analyst",   status: "success", iters: 1, tokens: 1612,  duration_s: 71 },
    { stage: "mission_architect",    status: "success", iters: 3, tokens: 4501,  duration_s: 152 },
    { stage: "simulation_executor",  status: "success", iters: 1, tokens: 1604,  duration_s: 52 },
    { stage: "mdo_integrator",       status: "success", iters: 1, tokens: 612,   duration_s: 18 },
  ],
};
