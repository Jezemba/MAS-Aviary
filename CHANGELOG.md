## [Unreleased]

### 2026-05-11
- Tested: ran mdo_f25_sequential_iterative_feedback with the coarse mesh +
  ITER=200 + 60-min timeout. Wandb run j0r9i0w3 (failed connect, tigl
  stale sessions from previous SIGKILL) then run #3 (logs/stat_results/
  1778517452, no wandb finalize). 50 steps reached mission_architect's
  2nd iteration before manual stop. Mesh fix end-to-end confirmed:
  - generate_volume_mesh produced a 62,569-node / 351,391-element volume
    mesh in coarse-fidelity mode
  - SU2 actually ran 200 Euler iterations in 120s (no DistributeColoring
    error, no immediate solver crash) — the TiGL→SU2 wire is fully
    operational
- Observed bugs to fix before next rerun:
  P0 (blocking):
  - aviary create_session called with initial_parameters="null" (string)
    instead of None. Same anyOf[dict, null] type-coercion gap that hits
    multiple aviary tools. mission_architect re-invoked indefinitely
    (was running its 20th iteration when manually stopped) because
    every create_session attempt fails. Pipeline never reaches
    simulation_executor.
  P1 (real bugs):
  - sample_surface_solution requires marker_name; aero agent omits it.
    On retry, server silently returns a different marker_name than
    requested (asked "mesh_marker_wall", got "aircraft").
  - numpy truth-value ambiguity ValueError fires inside pycycle
    get_cycle_summary AND su2 update_config_entries (same bug, two
    surfaces). Looks like an array-vs-scalar comparison server-side.
  - mass-mcp OAS solver NaN cascade ('array must not contain infs or
    NaNs' in solve_matrix). mass-mcp falls back to flops_only; not
    blocking but indicates upstream geometry passed in is degenerate.
  - mass-mcp logs "TIGL unavailable, using xpath fallback extraction.
    Missing optional parameters (n_engines, engine_bpr, cruise_mach,
    design_range_m, n_passengers)". TiGL session/result not flowing to
    mass — only the CPACS file path is. Need to verify whether mass
    actually needs the TiGL session or whether close_cpacs writes
    everything mass needs into the CPACS file.
  P2 (skill / prompt quality):
  - geometry agent: get_high_level_parameters returns empty for the D150
    fixture; agent proceeds without acknowledging the empties
  - geometry agent: set_high_level_parameters returns warnings that the
    agent ignores
  - geometry agent: re-runs set_high_level_parameters after an initial
    set without verifying it took
  - geometry agent: emits the same DESIGN_STATE final-answer body up to
    3 times in succession (final_answer call + same content in
    Observations + summary), eating ~4-6KB tokens each repeat
  - su2 agent: update_config_entries called step-by-step rather than
    with a complete preset; could be one call with the F25 cruise preset
  - su2 agent: max_runtime_seconds=600 used despite skill update to 300
    (LLM didn't pick up the new default reliably; consider hard-cap)
  - su2 agent: hardcoded mesh_path=null on create_su2_session is fine
    (mesh is attached later via set_mesh) but worth documenting
  - pycycle agent: list_variables dumps the entire variable tree into
    context multiple times — same +30K-token-per-call bloat as run #1
  - pycycle agent: design-point inputs lead to unrealistic engine
    (SFC=0.652 lb/hr/lbf high, thrust=5900 lbf low — F25 needs ~15k
    lbf/engine at cruise)
  - mission_architect: aircraft parameter set from upstream stages
    unclear; need to verify TiGL geometry params (span, sweep, area,
    etc.) and engine cycle params actually flow into aviary
    set_aircraft_parameters
  - aviary get_trajectory dumps a 60-point numeric array (~6KB JSON)
    into LLM context — same dataplane gap as run #1
  - aerodynamics_analyst's reported RESIDUAL_DROP_ORDERS arithmetic is
    wrong direction (residual rose, agent reported it as a drop)

### 2026-05-06
- Changed: Coarser default fidelity for sequential mdo_f25 SU2 stage to fit
  the per-repeat timeout. generate_volume_mesh call in geometry_engineer
  now passes surface_mesh_size=1.0, mesh_size_min=0.3, mesh_size_max=8.0,
  boundary_layer_enabled=false (targets ~50–200k cells solveable in
  <5 min for Euler). Aerodynamics_analyst default ITER lowered from 1000
  to 200 and max_runtime_seconds from 600 to 300. Refined RANS settings
  remain documented for later refined passes.
  Reason: 2026-05-06 12:15 verification run produced a 1.4M-cell mesh
  and hit the 20-min per-repeat wall during SU2 solve; the mesh fix
  itself worked correctly (volume cells present, no DistributeColoring).
- Fixed: TiGL→SU2 mesh handoff. Updated SKILL.md, data_flow.md,
  tool_catalog.md, and all four mdo_f25 pipeline configs to call
  `generate_volume_mesh(session_id, component_uid)` instead of
  `export_component_mesh(format="su2")` for the geometry→CFD step.
  `generate_volume_mesh` already existed in tigl-mcp (uses gmsh to embed
  the STL surface in a far-field box and emit a complete SU2 volume
  mesh with "aircraft" wall + "farfield" markers); the framework was
  simply never telling agents to call it. `export_component_mesh`
  produces a surface-only mesh (0 volume cells) which causes SU2's
  `DistributeColoring` to fail immediately. Tool list of geometry agent
  now includes both tools — surface mesh kept for visualization /
  non-CFD use, volume mesh required for CFD.
- Security: rotated leaked credentials and rewrote git history. Removed
  ANTHROPIC_API_KEY and WANDB_API_KEY hardcoded in run_batch.sh; replaced
  with .env loading. Added .env.example template. .env is gitignored.
- Changed: run_batch.sh now sources secrets from .env via `set -a; source .env`
  rather than hardcoding values, so contributors can keep their own keys
  out of the repo.
- Tested: ran one repeat of mdo_f25_sequential_iterative_feedback against
  Claude Sonnet 4 (LiteLLM), all 5 MCPs live. ~55 steps before manual stop.
  Wandb run: stat_1x1_1778069719 (mts2jmnb), project mas-aviary-stat.
- Confirmed working from this run:
  - Multi-MCP session handoff (each MCP gets its own session, IDs flow
    correctly between agents)
  - UPSTREAM_ERROR propagation in iterative_feedback handler (when SU2
    failed, downstream agents saw an error string instead of fabricating
    inputs)
  - Type coercion middleware (no "null"-as-string or JSON-as-string errors)
  - Data plane intercepts base64 mesh payloads from tigl as designed
  - All 6 agents executed: geometry_engineer, aerodynamics_analyst,
    structures_analyst, propulsion_analyst, mission_architect,
    simulation_executor
- Found bugs:
  - TiGL→SU2 mesh handoff: tigl-mcp `export_component_mesh` produces a
    surface-only mesh (0 volume elements per SU2 log: "17737 grid points,
    0 volume elements, 35470 boundary elements"). SU2 fails
    DistributeColoring, returns SOLVER_CONVERGED=false, RESIDUAL_DROP=0.
    Likely needs gmsh volume meshing step that was reverted in tigl-mcp
    commit 43e205e. Fix targeted next.
  - mass-mcp OAS solver: "array must not contain infs or NaNs" in
    SolveMatrix — cascade from bad upstream geometry. Fallback to
    flops_only works (OEM=35,725 kg, wing=7,677 kg, plausible).
  - numpy truth-value ambiguity ValueError in update_config_entries
    (su2-mcp side) — array-vs-scalar handling.
  - Data plane gap for large numeric payloads: 60-point trajectory dict
    (~6KB JSON) was not intercepted; only base64 patterns are. Step 8
    input tokens hit 249K, exceeding Sonnet 4.0's 200K context. Need to
    extend data_plane.py to also intercept large dict payloads, not only
    base64 binary.

### 2026-04-02
- Added: Data plane middleware (src/tools/data_plane.py) — intercepts large binary payloads
  (base64 meshes, STEP files) in tool responses, stores them in DesignState.data_store,
  and replaces them with lightweight references. Resolves references back to payloads on
  tool request. Prevents LLM context overflow from 100KB+ base64 mesh data.
- Added: DesignState.data_store field for large inter-MCP payloads (data plane)
- Added: Type coercion middleware (src/tools/type_coercion.py) — fixes mcpadapt bug that
  collapses anyOf types to "string", causing LLMs to send "null" instead of null and
  JSON strings instead of dicts. Coerces argument types before MCP calls.
- Added: LiteLLM backend support in model_loader.py for cloud API models (Claude, GPT, etc.)
- Added: mdo_f25_sequential_iterative_feedback combination in batch_runner.py
- Added: mdo_f25_run_claude.yaml config for Claude Sonnet 4 via LiteLLM
- Changed: Tool loading pipeline now applies full middleware stack (type coercion +
  data plane) to all MCP tools via apply_coercion_to_tools()

### 2026-03-29
- Added: DesignState class for centralized multi-MCP session/result/constraint tracking (src/coordination/design_state.py)
- Added: Multi-MCP connection support with per-server naming, tool routing, and graceful degradation (src/tools/mcp_connector.py)
- Added: Server-scoped tool patterns (e.g. "tigl.*", "su2.run_su2_solver") in pipeline templates
- Added: SkillLoader for LLM-agnostic skill injection — Mode A (Claude API) and Mode B (prompt injection) (src/skills/skill_loader.py)
- Added: MCPServerConfig.name field for named multi-server configs
- Added: SkillsConfig dataclass and AppConfig.skills field
- Added: Per-server environment variable URL overrides (MAS_AVIARY_TIGL_URL, etc.)
- Added: Aircraft Design MDO Skill (skills/aircraft-design-mdo/)
  - SKILL.md: Pipeline overview, discipline roles, tool sequences, DLR-F25 constraints, convergence criteria
  - references/tool_catalog.md: 54 tools across 5 MCPs discovered via live MCP tool listing
  - references/data_flow.md: DesignState schema and inter-MCP data transfers
  - references/f25_constraints.md: Full DLR-F25 optimization formulation with fidelity assessments
  - references/discipline_tradeoffs.md: 7 cross-disciplinary coupling descriptions
  - references/design_variables.md: Complete variable catalog across all 5 MCPs
  - scripts/validate_design_state.py: DesignState consistency checker
- Added: MDO F25 pipeline template YAMLs for all 4 organizational structures
  - config/mdo_f25_sequential_agents.yaml: 7-stage sequential pipeline (TiGL→SU2→Mass→PyCycle→Aviary)
  - config/aviary_mdo_f25_sequential.yaml: Sequential strategy config
  - config/mdo_f25_orchestrated_agents.yaml: Orchestrated config with 7 required tool phases
  - config/mdo_f25_networked_agents.yaml: Networked config with 7 workflow phases
  - config/mdo_f25_graph.yaml: Graph-routed state machine with MDO iteration loop
  - config/mdo_f25_staged_pipeline.yaml: Staged pipeline with per-stage completion criteria
  - config/mdo_f25_run.yaml: Main entry point config for 5-MCP runs
- Added: Tests for DesignState (21 tests) and SkillLoader (17 tests)
- Changed: MCPConnector now gracefully degrades when individual servers fail (logs warning, continues with available servers)
- Changed: Updated test_connection_error_propagates to test_connection_error_graceful_degradation
