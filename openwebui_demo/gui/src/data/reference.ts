// Static reference data the frontend owns (handoff README § "Static reference
// data the frontend owns"). Agent roster, per-MCP tool catalog for the Topology
// chips, org structures, handlers, mission presets, the graph edges, and the
// DLR-F25 reference values.
//
// Tool names are the REAL tool names the pipeline fires - validated against
// narration.py (openwebui_demo) and skills/aircraft-design-mdo/references/
// tool_catalog.md. The design prototype's data.jsx used simplified fictional
// names (load_cpacs, run_su2_cfd, …); those are replaced here with the actual
// MCP tools. generate_volume_mesh post-dates the 2026-03-29 catalog snapshot
// and is confirmed live via narration.py + the tigl-mcp CFD-mesh work.

import type {
  Agent,
  GraphEdge,
  HandlerDef,
  McpTool,
  MissionPreset,
  StructureDef,
} from "./types";

export const AGENTS: Agent[] = [
  { id: "geometry_engineer",    name: "Geometry Engineer",    short: "Geometry",     mcp: "tigl-mcp",    emoji: "📐", slow: true,  role: "Read CPACS, record design intent, generate CFD volume mesh." },
  { id: "aerodynamics_analyst", name: "Aerodynamics Analyst", short: "Aerodynamics", mcp: "su2-mcp",     emoji: "💨", slow: true,  role: "Configure SU2 (Euler), run solver, extract CL/CD/L/D from history." },
  { id: "structures_analyst",   name: "Structures Analyst",   short: "Structures",   mcp: "mass-mcp",    emoji: "⚖️",              role: "FLOPS mass estimate (OEM, wing mass, fuselage mass)." },
  { id: "propulsion_analyst",   name: "Propulsion Analyst",   short: "Propulsion",   mcp: "pycycle-mcp", emoji: "🔥", slow: true,  role: "Turbofan cycle: BPR, OPR, T4 → SFC, net thrust, engine mass." },
  { id: "mission_architect",    name: "Mission Architect",    short: "Mission",      mcp: "aviary-mcp",  emoji: "✈️",              role: "Configure Aviary mission + aircraft parameters, validate feasibility." },
  { id: "simulation_executor",  name: "Simulation Executor",  short: "Simulation",   mcp: "aviary-mcp",  emoji: "📊", slow: true,  role: "Run SLSQP trajectory optimization → FUEL_BURNED, GROSS_MASS." },
  { id: "mdo_integrator",       name: "MDO Integrator",       short: "Integrator",   mcp: "-",           emoji: "🧮",              role: "Synthesize results, check constraints, decide next iteration." },
];

export function agentById(id: string): Agent | undefined {
  return AGENTS.find((a) => a.id === id);
}

// Per-MCP tool catalog - real tool names, plain-language descriptions taken
// from narration.py (source of truth for the run-time copy). The Topology view
// renders these as mono chips with the description as a `title` tooltip.
export const MCP_TOOLS: Record<string, McpTool[]> = {
  "tigl-mcp": [
    { name: "open_cpacs",               desc: "Load the aircraft definition from CPACS (XML schema for wings, fuselage, engines)." },
    { name: "get_wing_summary",         desc: "Read the wing geometry - span, sweep, area, aspect ratio, MAC." },
    { name: "set_high_level_parameters", desc: "Record design intent (span, sweep). Stored as session annotations." },
    { name: "generate_volume_mesh",     desc: "Build a 3-D CFD mesh - TiGL→STL, gmsh far-field box + tetrahedralize. ~30-90 s." },
    { name: "close_cpacs",              desc: "Close the CPACS session and flush the file so mass-mcp can read it." },
  ],
  "su2-mcp": [
    { name: "create_su2_session",       desc: "Initialize the SU2 CFD workspace (config file, scratch dir)." },
    { name: "set_mesh",                 desc: "Load the volume mesh into the SU2 session." },
    { name: "update_config_entries",    desc: "Configure SU2 - Mach, AoA, Reynolds, solver type, freestream P/T, markers." },
    { name: "run_su2_solver",           desc: "Run SU2_CFD - Euler equations, 200 iterations. Typically 1-3 min." },
    { name: "read_history_csv",         desc: "Read the residual convergence trace from the history CSV." },
  ],
  "mass-mcp": [
    { name: "validate_cpacs_inputs",    desc: "Check the CPACS file has all parameters mass estimation needs." },
    { name: "estimate_mass",            desc: "Compute aircraft mass - OEM, wing, fuselage. FLOPS by default." },
    { name: "get_cpacs_mass_breakdown", desc: "Read mass results already written into the CPACS file." },
  ],
  "pycycle-mcp": [
    { name: "create_cycle_model",       desc: "Build a high-bypass turbofan thermodynamic cycle (gas-path components)." },
    { name: "set_inputs",               desc: "Set the engine design point - BPR, OPR, fan PR, T4, flight condition." },
    { name: "run_cycle",                desc: "Solve the cycle - Newton iteration → SFC, net thrust, fuel flow." },
    { name: "get_outputs",              desc: "Read cycle outputs (SFC at cruise, net thrust, engine mass)." },
  ],
  "aviary-mcp": [
    { name: "create_session",           desc: "Spin up an Aviary session, loading the default A320-class baseline." },
    { name: "configure_mission",        desc: "Set the mission profile - range, passengers, cruise Mach/altitude." },
    { name: "set_aircraft_parameters",  desc: "Override the baseline with upstream geometry/mass/propulsion values." },
    { name: "validate_parameters",      desc: "Quick feasibility check - single Newton eval to catch NaN residuals." },
    { name: "run_simulation",           desc: "Run Aviary's coupled trajectory optimization (SLSQP). ~40 s, 200 iters." },
    { name: "check_constraints",        desc: "Check every F25 constraint - range, climb gradient, TOFL, approach speed." },
  ],
};

// The three real org structures (the framework's structure axis). All are wired
// for MDO-F25; combo validity is handled per-cell by isValidCombo().
export const STRUCTURES: StructureDef[] = [
  { id: "sequential",   label: "Sequential",   available: true, desc: "Linear pipeline - one agent active at a time." },
  { id: "orchestrated", label: "Orchestrated", available: true, desc: "Coordinator delegates to specialists." },
  { id: "networked",    label: "Networked",    available: true, desc: "Blackboard - agents post & subscribe." },
];

// The three real execution handlers.
export const HANDLERS: HandlerDef[] = [
  { id: "iterative_feedback", label: "Iterative feedback", available: true, desc: "Stage re-invokes until acceptance criteria met (1-N times)." },
  { id: "staged_pipeline",    label: "Staged pipeline",    available: true, desc: "Strict gate - advance only when all criteria met." },
  { id: "graph_routed",       label: "Graph-routed",       available: true, desc: "Named transitions drive non-linear flow." },
];

// Topology visualization patterns: the 3 org structures plus a "graph" view
// that visualizes the graph_routed handler's state machine. This is a preview
// surface, independent of the selected org structure.
export const TOPOLOGY_PATTERNS: StructureDef[] = [
  ...STRUCTURES,
  { id: "graph", label: "Graph", available: true, desc: "Explicit state machine with named transitions." },
];

// The 8 valid MDO-F25 combinations = 3 structures x 3 handlers, minus
// networked_staged_pipeline, which was never wired for MDO-F25.
export function isValidCombo(structure: string, handler: string): boolean {
  return !(structure === "networked" && handler === "staged_pipeline");
}

export function comboName(structure: string, handler: string): string {
  return `mdo_f25_${structure}_${handler}`;
}

export const MISSION_PRESETS: MissionPreset[] = [
  {
    id: "f25",
    label: "DLR-F25",
    desc: "2 500 nmi · 200 pax · M 0.78 · FL330",
    target: { range_nmi: 2500, pax: 200, mach: 0.78, alt_ft: 33000, fuel_kg: 12100 },
  },
  { id: "custom", label: "Custom…", desc: "Edit the YAML directly (v1)" },
];

// Graph-routed transitions (named edges between stages), used by the Topology
// Graph pattern. Matches GRAPH_EDGES in the design prototype and the framework
// graph config.
export const GRAPH_EDGES: GraphEdge[] = [
  { from: "geometry_engineer",    to: "aerodynamics_analyst", label: "mesh_ready" },
  { from: "aerodynamics_analyst", to: "structures_analyst",   label: "converged" },
  { from: "aerodynamics_analyst", to: "geometry_engineer",    label: "not_converged", retry: true },
  { from: "structures_analyst",   to: "propulsion_analyst",   label: "mass_done" },
  { from: "propulsion_analyst",   to: "mission_architect",    label: "sfc_done" },
  { from: "mission_architect",    to: "simulation_executor",  label: "mission_ok" },
  { from: "simulation_executor",  to: "mdo_integrator",       label: "trajectory_done" },
];

// DLR-F25 reference values.
export const F25_REFERENCE = {
  range_nmi: 2500,
  pax: 200,
  mach: 0.78,
  alt_ft: 33000,
  fuel_burned_kg: 12100,
  gross_mass_kg: 85700,
  ld_cruise: 19.5,
} as const;

export const TOKENS_BUDGET = 200_000; // Sonnet context window
