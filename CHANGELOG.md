## [Unreleased]

### 2026-05-11 (Phase D — AR feasibility cliff + simulator no-loop on setup error)

Run #4 reached simulation_executor for the first time, then hit a new
failure mode: `run_simulation` returned `AVIARY_SETUP_ERROR` with the
message "Solver 'NL: Newton' on system 'traj.phases.climb.rhs_all.
solver_sub': residuals contain 'inf' or 'NaN' after 0 iterations". The
agent recovered partially (lowered Aircraft.Wing.ASPECT_RATIO from
15.6 → 14 → 13.5) but each retry still NaN'd, and the simulator agent
then looped on run_simulation/get_results in successive
iterative_feedback iterations.

Diagnosis: aviary-mcp's default aircraft baseline
(aircraft_for_bench_FwFm.csv, A320-class) has its FLOPS aero buildup
and engine deck tabulated for AR ~= 9-12. Setting AR > ~12 causes the
climb-phase Newton solver to evaluate residuals out of the table range
and produce inf/NaN. The previous advisory bounds [7.0, 14.0] both
rejected legitimate high-AR targets (DLR-F25 AR=15.6) and hid the real
feasibility cliff — agent obeyed the bound (set AR=14.0) and still
NaN'd because the cliff is at ~12, not 14.

aviary-mcp fix (separate PR on Jezemba/aviary-mcp, branch
fix/ar-reliable-range):
- AR advisory bounds widened to [7.0, 17.0] (covers F25 + modern
  high-AR transports as advisory targets)
- Added explicit "reliable_range": [9.0, 11.5] field documenting the
  cleanly-interpolating window for the default aircraft
- set_aircraft_parameters emits a SECOND warning ("outside
  reliable_range") when the value is in advisory bounds but outside
  the reliable window, so agents see the real cliff before
  run_simulation

MAS-Aviary prompt fixes:
- mission_architect: default Aircraft.Wing.ASPECT_RATIO = 11.0 (down
  from F25-target 15.6). F25 noted as aspirational; full fidelity
  requires a custom aircraft CSV (filed as future work). Upstream
  mapping table caps AR at 11.5 regardless of geometry intent.
- mission_architect: default Aircraft.Engine.SCALE_FACTOR = 1.3 (up
  from 1.0). The F25-target mission mass (~85t) is heavier than
  Aviary's baseline (~67t); SCALE_FACTOR=1.0 underpowers the climb
  phase.
- simulation_executor: explicit handling of AVIARY_SETUP_ERROR as a
  PERMANENT failure for the current parameter set. Report once, do
  NOT call run_simulation/get_results/get_trajectory again — those
  return NO_RESULTS in this state and burn steps.

New live regression scenario:
- test_simulator_does_not_loop_on_aviary_setup_error
  Forces AR=15.6 to trigger the cliff, asserts run_simulation called
  at most once and get_results/get_trajectory skipped on
  AVIARY_SETUP_ERROR. PASS on the updated prompt.

All 10 live regressions pass.

The deeper finding: this framework as-shipped is doing "A320-class MDO
with F25 mission targets" — the trajectory optimizer will find an
A320-shape optimum because the aero/engine tables are A320-shape. To
genuinely optimize an F25-shape aircraft requires a custom F25
aircraft CSV in aviary-mcp (calibrated aero/engine tables for AR > 12;
~half-day work; could derive from the Abu Zurayk paper in .llm/). That
is "Phase E" and out of current scope.

### 2026-05-11 (Phase C — investigations and follow-on prompt corrections)

Investigated the run #3 D150-fixture symptoms ("get_high_level_parameters
returns empty", "set_high_level_parameters claims success but
get_wing_summary unchanged"). Root cause is a fundamental design
property of the tigl-mcp tool surface, not a fixture bug:

  - tigl-mcp ComponentDefinition.parameters is an in-memory python dict
    populated at parse time ONLY from CPACS XML element attributes
    (uID, name, symmetry — never numeric like span/sweep, because CPACS
    encodes those in child elements, not attributes). So the dict is
    effectively always empty for any real CPACS file.
  - set_high_level_parameters writes to that local dict only. It does
    NOT modify the CPACS XML, does NOT re-load TiGL, does NOT change
    what get_wing_summary or generate_volume_mesh produce.
  - get_high_level_parameters reads the same dict and so always returns
    {} unless the agent has set_high_level_parameters in the same
    session.
  - TiGL geometry is loaded once at open_cpacs and is the immutable
    ground truth for the session.

This is a fundamental property of the framework: geometry never
actually varies between iterations at the TiGL level. Only Aviary's
set_aircraft_parameters (which operates on Aviary's own aircraft
model, not on CPACS) actually responds to agent intent. The
"multidisciplinary" character of the MDO is therefore:
   tigl     — baseline geometry only (intent recorded as a memo)
   su2      — CFD on the baseline mesh
   mass     — reads baseline CPACS from disk
   pycycle  — independent engine design, drives no geometry
   aviary   — actually optimizes mission against an Aviary-internal
              aircraft sized via set_aircraft_parameters

Phase B-1 geometry prompt was REVISED to be honest about this:
  - set_high_level_parameters explicitly framed as "record design
    intent" (memo-only). Prompt now states the mesh will reflect the
    BASELINE, not the intent.
  - Post-set verify (step 5) is reframed: read get_wing_summary to
    capture the BASELINE values (which ARE the mesh truth) for
    DESIGN_STATE. Do NOT halt on baseline/intent mismatch — that
    mismatch is structural to the framework, not a bug.
  - New DESIGN_INTENT field in DESIGN_STATE output: a YAML mapping of
    intended param → value. mission_architect's upstream-mapping table
    (Phase B-4) already consumes this.
  - Null fields from D150 (some Wing1 attributes like sweep_deg
    legitimately come back null on this fixture) are recorded but do
    NOT abort the pipeline.

Regression test test_geometry_set_then_verify_then_close updated:
  - removed the "halt on mismatch" expectation
  - added requirement that the agent ALWAYS attempts mesh
  - close_cpacs requirement only enforced on a successful mesh

Filing notes for future work (out of current scope per
"do NOT modify MCP server code"):
  - tigl-mcp could optionally read baseline param values from
    get_wing_summary into ComponentDefinition.parameters at open
    time, so get_high_level_parameters returns useful defaults
  - tigl-mcp could optionally implement set→TIXI-write→TiGL-reload
    to make geometry actually change. This is a substantial
    redesign of the tool — a candidate issue to file on Jezemba/tigl-mcp
  - su2-mcp and pycycle-mcp numpy truth-value ambiguity in
    update_config_entries and get_cycle_summary are minor server-side
    bugs; agent recovers gracefully but worth filing

### 2026-05-11 (Phase B — skill / prompt fixes for run #3 P2 bugs)

Each agent's prompt in config/mdo_f25_sequential_agents.yaml updated to
eliminate a specific class of run #3 wasteful behavior. Each change has
a matching live regression scenario in tests/test_run3_regressions.py.

- geometry_engineer prompt (Phase B-1):
  - set_high_level_parameters called ONCE (was: redundantly re-called)
  - response warnings surfaced into DESIGN_STATE.COUPLING_NOTES (was:
    silently swallowed)
  - mandatory post-set verification via get_wing_summary; on null/>5%
    mismatch, halt the pipeline with MESH_BASE64="skipped — geometry
    invalid" rather than meshing a partial geometry
  - close_cpacs at end so mass-mcp reads fresh CPACS from disk (was:
    sometimes omitted, leaving mass to read stale data)
  - one final_answer block only (was: same DESIGN_STATE repeated 3x,
    eating downstream context)
  - regression: test_geometry_set_then_verify_then_close
- aerodynamics_analyst (SU2) prompt (Phase B-2):
  - complete F25 cruise config preset baked into the step 3
    update_config_entries call (was: two piecemeal calls, first
    returned 7-key missing_required)
  - mesh_path=null on create_su2_session documented as expected
  - run_su2_solver max_runtime_seconds=300 hard ceiling (was: 600;
    risked busting the per-repeat timeout when the LLM picked 600)
  - residual interpretation: log10 values, smaller = better,
    drop = INITIAL - FINAL; explicit "common mistake" warning
    pointing out the run #3 agent's sign error
  - sample_surface_solution always called with marker_name='aircraft'
  - second update_config_entries call permitted only as a tiny
    fill-in for server-flagged missing_required (no third call)
  - regression: test_su2_config_preset_in_one_call
- propulsion_analyst (pycycle) prompt (Phase B-3):
  - list_variables called ONCE with max_parameters cap (was: 6+ times
    with growing limits, +30K tokens each → 377K input at step 11)
  - explicit F25 cruise design-point inputs (fc.alt=33000,
    fc.MN=0.78, throttle=0.85, BPR=11, OPR=40, fan.PR=1.45,
    T4=1700) so cycle is sized for cruise not SLS
  - sanity check bounds for SFC and Fn; outside-range outputs trigger
    fallback to F25 reference values
  - regression: test_pycycle_list_variables_called_at_most_once
- mission_architect (aviary) prompt (Phase B-4):
  - get_design_space called FIRST, before create_session, to discover
    exact parameter names (was: agent guessed and retried)
  - create_session called with no initial_parameters (omit kwarg)
  - explicit upstream-param mapping table for set_aircraft_parameters:
    each Aviary param maps to a specific DESIGN_STATE field with an
    F25-baseline fallback when upstream returned UPSTREAM_ERROR or
    null
  - Aircraft.Wing.SPAN explicitly NOT to be set (avoids the
    aviary-mcp "SPAN read-only derived param" injection bug)
  - validate_parameters called once; on invalid, report and STOP
    rather than loop (this was the run #3 20-iteration trap on top
    of the Phase A create_session blocker)
  - regression: test_mission_calls_design_space_first_and_validates_once

Live regression suite is now 9 scenarios; all pass.
Full unit suite is 1319/1319 passing.

### 2026-05-11 (Phase A — middleware fixes)
- Fixed: type-coercion middleware now treats the literal string "null"
  (or "None"/"") as Python None for any schema that accepts null in any
  of these ways:
    - anyOf includes {type: null}
    - anyOf is present (mcpadapt may have stripped the null option)
    - nullable: true is set
    - schema has explicit "default": null
- Fixed (uncovered by the live regression test): coercing "null" → None
  is not enough — aviary's create_session pydantic wrapper rejects
  explicit None even when its own schema says default: null. The
  middleware now DROPS the kwarg entirely when the coerced value is
  None AND the schema declares default: null, letting the server's
  native default kick in. End-to-end create_session now succeeds.
  Resolves the run #3 P0 blocker where mission_architect was stuck in
  a 20-iteration loop because every create_session attempt was
  rejected with "Input should be a valid dictionary [type=dict_type]".
- Added: data-plane middleware now intercepts large structured payloads
  in addition to base64 binary. Triggers on:
    - lists with > 30 items (e.g. pycycle list_variables 200-item tree)
    - dicts whose JSON-serialized form exceeds 2KB (e.g. aviary
      get_trajectory 60-point timeseries)
  Intercepted payloads are stored in DesignState.data_store under
  "<tool>__<field>" and the LLM sees a compact summary (preview + ref +
  total_count + note). Downstream tool calls can pass the ref to fetch
  the full payload via resolve_request. Eliminates the +30K-tokens-
  per-list_variables-call growth that hit 377K input tokens by step 11
  in run #1.
- Tests: tests/test_type_coercion.py (22 tests, +4 for the drop-key
  fix) and tests/test_data_plane.py (13 tests) cover the coercion paths
  and large-payload interception against pycycle list_variables and
  aviary get_trajectory shapes. Full unit suite 1319/1319 passing.
- Added: live regression harness at tests/test_run3_regressions.py and
  the live_mcp_llm pytest marker. Each test spins up a minimal
  smolagents ToolCallingAgent with Claude Sonnet 4 via LiteLLM and ONLY
  the tools needed to reproduce one run #3 bug. End-to-end against the
  live MCPs. Run on demand: pytest -m live_mcp_llm -v. Five scenarios
  cover: create_session pydantic blocker, pycycle list_variables
  context bloat, aviary get_trajectory bloat, sample_surface_solution
  marker recovery, set_aircraft_parameters dict handling. All 5 pass
  on the Phase A + drop-key middleware. This harness was responsible
  for catching the "coerce-to-None isn't enough, must drop key" gap
  that the unit tests alone missed.

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
  - geometry agent: doesn't acknowledge that some parameters are null
    before generating the mesh — risks meshing on a partial parameter
    set, hides upstream data-quality issues
  - su2 agent: update_config_entries called step-by-step rather than
    with a complete preset; could be one call with the F25 cruise preset
  - su2 agent: max_runtime_seconds=600 used despite skill update to 300
    (LLM didn't pick up the new default reliably; consider hard-cap)
  - su2 agent: hardcoded mesh_path=null on create_su2_session is fine
    (mesh is attached later via set_mesh) but worth documenting
  - su2 agent: tool responses are unstructured prose with file paths
    embedded — LLM has to grep paths from text rather than receiving a
    typed schema. Workable but lossy; consider a structured response
    envelope (status, working_dir, log_tail, marker_names) for SU2 tools.
  - pycycle agent: list_variables dumps the entire variable tree into
    context multiple times — same +30K-token-per-call bloat as run #1
  - pycycle agent: design-point inputs lead to unrealistic engine
    (SFC=0.652 lb/hr/lbf high, thrust=5900 lbf low — F25 needs ~15k
    lbf/engine at cruise)
  - mission_architect: aircraft parameter set from upstream stages
    unclear; need to verify TiGL geometry params (span, sweep, area,
    etc.) and engine cycle params actually flow into aviary
    set_aircraft_parameters
  - mission_architect: aviary lacks a structured "design space" query
    (analogous to pycycle list_variables) that the LLM can call to
    discover what aircraft parameters are settable, with units and
    bounds. get_design_space exists but isn't as discoverable. Consider
    a lightweight parameters-info surface so the mission agent doesn't
    have to remember constants from the prompt.
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
