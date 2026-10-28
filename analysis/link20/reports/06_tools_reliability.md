# 06 — MCP tool use and run reliability (20-link DLR-F25 study)

Scope: 8 combinations x 20 links = 160 links. Main source: every link's `knowledge_base.jsonl` (one record per MCP
call: agent, tool, server, status, inputs, outputs, error_code), plus `links.csv` (out_20261028), `stat_progress.json`
and the runner logs. Script: `analysis/link20/reports/scripts/tools_reliability.py`; tables (CSV) and the full printout
(`run_output.txt`) in ws8 `~/Jessica/avion_analysis/reports_20261028/tools_reliability/`.

Who made a call. `framework` records (the runner's `link_start`) are not MCP calls and are dropped. Records with
agent `unknown` are the **graph-routed pre-initialisation** (`src/coordination/graph_preinit.py`): before the first
agent turn of S_GR / O_GR / N_GR links the framework opens the TiGL session, reads the wing and fuselage, meshes, opens
the SU2 and pyCycle sessions (and on link 1 runs the baseline mass, SU2 solve, set_inputs and run_cycle). They are
counted separately as "pre-init" (368 calls, all successful). Everything below is **agent calls** unless it says
otherwise. All runner attempts of a link are counted (the KB keeps every attempt), as in `links.csv`.

Datasets differ in code version (S_SP S_IF S_GR O_SP O_IF = v2full, 2026-10-14 code; O_GR N_IF = v3combos;
N_GR = v4net, fixed code `integration/2026-10-21`). Several refusal codes exist only in some code versions, so
refusal mixes are partly a code-version effect (see Caveats).

## Key findings

1. **Agents made 9,139 MCP calls over 160 links: 78.7% succeeded, 10.9% failed, 10.3% were refused by the
   framework** (6 calls, 0.1%, have no outcome recorded). Sequential staged / iterative links are clean (94–95%
   success, about 26–28 calls per link, n=20 each). Networked iterative feedback is the worst: 57.2% success and
   **30.3% refused** (17.25 refusals per link, n=20). Networked graph-routed makes by far the most calls
   (156.8 per link, n=20; 120.05 successful), against 26.8–57.2 for the other combinations.
2. **Refusals are dominated by the coupling contract.** `MISSING_REQUIRED_PARAMETERS` (a tool called before the
   discipline result it needs exists or while it is stale) is 413 of 941 refusals; 315 of them hit
   `run_simulation`. It is almost entirely a networked / O_IF phenomenon: 8.05 per link in N_IF, 10.00 in N_GR,
   2.35 in O_IF, at most 0.10 elsewhere (n=20 each). N_IF also has 130 `CLAIMED_BY_ANOTHER_AGENT` refusals
   (6.50 per link). Plot: `out_20261028/plots/4_coordination/refused_by_error_code_avg_bar.png`.
3. **run_simulation is the bottleneck tool: 61.8% of its 1,093 calls failed or were refused.** In N_IF only
   11 of 242 calls succeeded (4.5%; 13.4% of the calls that actually ran), and 13 of 20 N_IF links never flew.
   N_GR flew the most missions (14.85 per link, n=20) but 127 of its successful missions repeated the previous
   mission's fuel exactly (15 links).
4. **Every did-not-fly link (20 of 160) broke at the mission, not upstream.** Every one had a mesh; 19 of 20
   ran `run_simulation` and got `ZERO_FUEL` (the design flew and burned no fuel); the 20th (O_IF link 8) was only
   refused (`MISSING_REQUIRED_PARAMETERS` x6; mass and cycle never succeeded). 8 of the 20 had at least one
   mission flown with the B187 engine scale 0.994 (S_GR 7, 13, 14; N_IF 1, 2, 5, 9, 15).
5. **Run reliability is good at the runner level:** 156 of 160 links completed; 4 failed (O_IF 8: zero fuel after
   3 attempts; O_IF 19, N_GR 6, N_GR 8: 480-min timeout, all three flew before timing out). 17 links needed a
   second or third runner attempt; every recorded retry reason is "zero fuel". Earlier attempts account for 734
   agent calls (309 in N_IF, 256 in O_IF).
6. **B187 on the fixed code (N_GR links 10 and 14): both are unsized `run_cycle` calls (Fn = 5,900 lbf) on the
   pre-init pyCycle session, with no `set_inputs` on that session before.** Link 10: the thrust was captured,
   scale 0.994 reached aviary once (mission seq 42 → ZERO_FUEL), then the framework restored 4.2116; the counted
   mission (seq 98) flew at 4.2116, so link 10's fuel is not B187-contaminated. Link 14: never reached aviary
   (the next `run_simulation` was refused as `engine_stale`); counted mission (seq 106) at 4.2116. Over all
   combinations only S_GR links 2 and 19 have a counted fuel that was flown at scale 0.994.
7. **Errors and refusals do not fall along the chain.** Failed calls per link are higher in links 11–20 than in
   links 1–10 in all 8 combinations (n=10 vs 10), refusals are lower in 6 of 8; Spearman correlations with the link
   index are weak (|rho| <= 0.33 per combination; over all 160 links rho = 0.017 for refusals, 0.157 for failures).
   N_GR grows: 122.3 → 191.4 calls, 14.8 → 27.2 refusals, 12.5 → 18.9 failures per link (n=10 each).
8. **17 of the 60 tools the five MCP servers expose were never called by any agent** (the 3 `ping`s, and analysis
   tools such as SU2 `analyze_mesh`, `generate_deformed_mesh`, `sample_component_surface`, pyCycle `sweep_inputs`,
   `compute_totals`). The workflow uses a narrow, fixed subset.

## 1. MCP calls per link, by server (agent calls)

Mean per link, n = 20 links per combination. Pre-init calls (graph-routed only) shown separately.

| Comb. | Calls/link mean | median | min–max | tigl | su2 | mass | pycycle | aviary | Pre-init/link | Calls in earlier runner attempts (total) |
|---|---|---|---|---|---|---|---|---|---|---|
| S_SP | 26.8 | 25.0 | 18–50 | 9.0 | 3.6 | 1.2 | 5.2 | 7.8 | 0.0 | 51 |
| S_IF | 27.6 | 25.0 | 20–66 | 8.6 | 3.3 | 1.4 | 5.2 | 9.1 | 0.0 | 33 |
| S_GR | 50.4 | 42.5 | 15–83 | 5.4 | 3.4 | 2.3 | 14.0 | 25.4 | 6.2 | 0 |
| O_SP | 29.4 | 25.5 | 20–61 | 8.4 | 4.1 | 2.5 | 6.2 | 8.2 | 0.0 | 85 |
| O_IF | 51.8 | 41.0 | 22–110 | 13.6 | 7.4 | 2.4 | 8.2 | 20.2 | 0.0 | 256 |
| O_GR | 57.2 | 59.0 | 20–108 | 3.0 | 4.4 | 3.4 | 27.0 | 19.5 | 6.2 | 0 |
| N_IF | 56.9 | 46.5 | 28–148 | 14.6 | 7.0 | 3.0 | 11.6 | 20.6 | 0.0 | 309 |
| N_GR | 156.8 | 137.5 | 50–272 | 33.5 | 34.0 | 3.8 | 14.4 | 71.2 | 6.0 | 0 |

- Aviary carries most traffic everywhere except O_GR, where pyCycle dominates (27.0 per link; `set_inputs`
  6.30, `list_variables` 5.30, `run_cycle` 8.65 per link).
- N_GR's volume is spread over everything: per link 26.20 `run_simulation`, 18.75 `set_aircraft_parameters`,
  11.45 `run_su2_solver`, 9.75 `generate_volume_mesh`, 9.60 `check_constraints` (n=20). Sequential staged
  pipeline calls each core tool about once per link (1.1–1.3).

Full per-tool table: `calls_per_link_by_tool.csv`.

## 2. Tools used and never used

60 tools in the inventory (Outil `docs/MCP_TOOL_INVENTORY.md`, live snapshot 2026-10-01: tigl 19, su2 19,
pycycle 11, aviary 8, mass 3). Every tool agents called is in it.

| Server | Never called by any agent in any combination |
|---|---|
| tigl | ping, sample_component_surface, intersect_with_plane, export_component_mesh, export_configuration_cad |
| su2 | ping, parse_config, get_valid_config_options, generate_mesh_from_step, analyze_mesh, generate_deformed_mesh, get_su2_status, list_result_files, get_result_file_base64 |
| pycycle | ping, sweep_inputs, compute_totals |
| aviary | (all used) |
| mass | (all used) |

Used but rare (<= 5 agent calls in all 160 links): tigl `morph_fuselage` 3, `intersect_components` 2,
`get_component_metadata` 1, aviary `create_session` 1 (refused, `SESSION_EXISTS`). `morph_wing` was used 19 times,
only in networked combinations (0.10 per link N_IF, 0.85 N_GR). Per-combination unused lists:
`tool_usage_vs_inventory.csv`.

## 3. Status mix

### By combination (agent calls)

| Comb. | Calls | Success | Failed | Refused | Success share | Failed / link | Refused / link (n=20) |
|---|---|---|---|---|---|---|---|
| S_SP | 535 | 507 | 23 | 5 | 0.948 | 1.15 | 0.25 |
| S_IF | 552 | 521 | 28 | 3 | 0.944 | 1.40 | 0.15 |
| S_GR | 1008 | 789 | 146 | 73 | 0.783 | 7.30 | 3.65 |
| O_SP | 588 | 540 | 42 | 5 | 0.918 | 2.10 | 0.25 |
| O_IF | 1036 | 829 | 139 | 67 | 0.800 | 6.95 | 3.35 |
| O_GR | 1145 | 956 | 165 | 23 | 0.835 | 8.25 | 1.15 |
| N_IF | 1138 | 651 | 141 | 345 | 0.572 | 7.05 | 17.25 |
| N_GR | 3137 | 2401 | 314 | 420 | 0.765 | 15.70 | 21.00 |

(1 call each in O_SP, O_IF, O_GR, N_IF and 2 in N_GR have status `unknown`, empty outputs: `sample_surface_solution`
x5 and `estimate_mass` x1.)

### By server (agent calls, all combinations)

| Server | Calls | Success share | Failed share | Refused share |
|---|---|---|---|---|
| aviary | 3637 | 0.675 | 0.174 | 0.151 |
| tigl | 1922 | 0.872 | 0.101 | 0.027 |
| pycycle | 1839 | 0.798 | 0.078 | 0.123 |
| su2 | 1345 | 0.911 | 0.017 | 0.068 |
| mass | 396 | 0.932 | 0.013 | 0.053 |

Worst server/combination cells: N_IF aviary 24.5% success (48.5% refused), N_IF pycycle 54.5% success (44.2%
refused), S_GR aviary 25.8% failed, O_GR pycycle 21.7% failed. Per combination x server: `status_by_server_combo.csv`.

### Tools with the most failures + refusals (agent calls, all combinations)

| Tool | Calls | Success | Failed | Refused | Failed+refused share |
|---|---|---|---|---|---|
| run_simulation | 1093 | 418 | 167 | 508 | 0.618 |
| set_aircraft_parameters | 962 | 587 | 365 | 10 | 0.390 |
| run_cycle | 602 | 397 | 103 | 102 | 0.341 |
| generate_volume_mesh | 438 | 257 | 164 | 17 | 0.413 |
| create_cycle_model | 247 | 123 | 0 | 124 | 0.502 |
| get_results | 493 | 399 | 70 | 24 | 0.191 |
| run_su2_solver | 447 | 370 | 17 | 60 | 0.172 |
| create_su2_session | 245 | 214 | 0 | 31 | 0.127 |
| open_cpacs | 193 | 167 | 0 | 26 | 0.135 |

## 4. Refusals by error code

Refused = stopped by a framework guard before the MCP server (none by pre-init). Counts; every combination has
n = 20 links (divide by 20 for the per-link mean shown in the plot).

| Code | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR | Total |
|---|---|---|---|---|---|---|---|---|---|
| MISSING_REQUIRED_PARAMETERS | 1 | 1 | 0 | 2 | 47 | 1 | 161 | 200 | 413 |
| CLAIMED_BY_ANOTHER_AGENT | 0 | 0 | 0 | 0 | 0 | 0 | 130 | 0 | 130 |
| ALREADY_DONE | 0 | 0 | 17 | 2 | 9 | 13 | 21 | 66 | 128 |
| SESSION_ALREADY_OPEN | 0 | 0 | 56 | 0 | 1 | 5 | 11 | 48 | 121 |
| GEOMETRY_NOT_APPLIED | 2 | 0 | 0 | 1 | 1 | 4 | 17 | 28 | 53 |
| NO_CHANGE_SINCE_FAILED_RUN | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 51 | 51 |
| AERO_NONPHYSICAL | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 20 | 20 |
| NO_MESH | 2 | 2 | 0 | 0 | 9 | 0 | 2 | 0 | 15 |
| CLAIM_FIRST | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 7 | 7 |
| MORPH_NOT_EXPORTED | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 2 |
| SESSION_EXISTS | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |

What each code means (read from the code in the MAS-Aviary-analysis worktree; bug ids from `.claude/BUGS.md` /
commit messages):

| Code | Where | Meaning | Main tools refused |
|---|---|---|---|
| MISSING_REQUIRED_PARAMETERS | `src/tools/coupling_contract.py` (B84) | A coupled input does not exist for the current design: `run_simulation` needs SU2 CL/CD, mass-mcp wing mass and a cycle run; `run_cycle` needs mass-mcp MTOM. A result from an earlier geometry, or (fixed code, B187) an engine not sized on the current mass/drag (`engine_stale`), counts as missing. | run_simulation 315, run_cycle 98 |
| CLAIMED_BY_ANOTHER_AGENT | `src/tools/work_claims.py` (B86), networked only | The tool belongs to a TODO another peer has claimed. | run_simulation 69, get_results 24, generate_volume_mesh 12 |
| CLAIM_FIRST | same (B86) | A peer's first domain call with nothing claimed; issued once per peer. | set_high_level_parameters 5 |
| ALREADY_DONE | `src/tools/duplicate_guard.py` (B81) | Once-only work already done for the same design fingerprint; refused once per (agent, tool), the next call runs. | run_su2_solver 44, create_su2_session 31, open_cpacs 26, estimate_mass 19 |
| SESSION_ALREADY_OPEN | `src/tools/data_plane.py` | `create_cycle_model` while a pyCycle session is already open. | create_cycle_model 121 |
| SESSION_EXISTS | `data_plane.session_creation_refusal` | Aviary `create_session` while the runner's session exists (mission is a fixed control). | create_session 1 |
| GEOMETRY_NOT_APPLIED | `src/tools/geometry_guard.py` | The mission would fly a wing differing by > 2% from the geometry. | run_simulation 53 |
| NO_CHANGE_SINCE_FAILED_RUN | `src/tools/mission_guard.py` (B188, fixed code) | `run_simulation` repeating a failed mission with nothing set since. | run_simulation 51 |
| AERO_NONPHYSICAL | `src/tools/aero_guard.py` (B111) | SU2 CD <= 0 or CL outside aviary's cruise range. | run_simulation 20 |
| NO_MESH | `src/tools/mesh_guard.py` | SU2 solve on a session with no mesh attached. | run_su2_solver 15 |
| MORPH_NOT_EXPORTED | `geometry_guard.py` (B108) | SU2/mass would read the CPACS file while a morph is not yet exported. | estimate_mass 2 |

Plots: `out_20261028/plots/4_coordination/refused_by_error_code_avg_bar.png` (mean refusals per link by code; it
uses `links.csv` `refused_by_code`, the same counts as this table) and `refused_mcp_calls_avg_box.png`,
`refused_mcp_calls_avg_strip.png`, `refused_mcp_calls_line_panels.png`, `refused_mcp_calls_line_marker.png`,
`refused_mcp_calls_line_linestyle.png` in the same folder.

## 5. Failures by error code, and typical messages

Failed = the call reached the server (or the middleware's result check) and did not succeed.

| Code | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR | Total | Meaning |
|---|---|---|---|---|---|---|---|---|---|---|
| (no code) | 6 | 6 | 15 | 14 | 50 | 126 | 30 | 119 | 366 | server exception or tool-level failure, see below |
| ZERO_FUEL | 0 | 0 | 106 | 0 | 0 | 4 | 76 | 39 | 225 | flown mission burned <= 100 kg (`data_plane._flag_zero_fuel`, B92); run_simulation 162, get_results 63 |
| UNKNOWN_PARAMETER | 12 | 14 | 0 | 24 | 78 | 20 | 17 | 21 | 186 | aviary-mcp: parameter name not in the design space |
| READ_ONLY_PARAMETER | 0 | 6 | 23 | 1 | 7 | 9 | 1 | 129 | 176 | aviary-mcp: derived parameter set directly |
| NO_RESULTS | 2 | 0 | 0 | 1 | 3 | 0 | 14 | 0 | 20 | aviary-mcp: results asked before any run_simulation in the session |
| UNKNOWN_VARIABLE | 3 | 1 | 2 | 2 | 1 | 6 | 0 | 0 | 15 | aviary-mcp: unknown trajectory / constraint variable |
| AVIARY_SETUP_ERROR | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 4 | 5 | aviary-mcp: problem setup / simulation exception |
| TYPE_ERROR | 0 | 1 | 0 | 0 | 0 | 0 | 2 | 0 | 3 | aviary-mcp: value not a number |
| INVALID_OPERATOR | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 2 | aviary-mcp: check_constraints operator not <=, >=, == |

ZERO_FUEL is a physics outcome (the optimiser's mission burned no fuel), not a tool error, but the framework records
it as `failed`. Typical text, from the KB (UUIDs masked, cut at ~200 characters):

| Tool | Count | Typical message |
|---|---|---|
| set_aircraft_parameters, READ_ONLY_PARAMETER | 174 of 176 | `'Aircraft.Wing.SPAN' is derived (sqrt(ASPECT_RATIO * AREA)) and cannot be set directly. Set its source parameters instead.` |
| set_aircraft_parameters, UNKNOWN_PARAMETER | 38 / 35 (top two) | `Unknown parameter: 'Aircraft.Design.LIFT_COEFFICIENT'. Did you mean 'Mission.Design.LIFT_COEFFICIENT' ...` / `Unknown parameter: 'Aircraft.Fuselage.DIAMETER'. Did you mean ...` |
| run_simulation, ZERO_FUEL | 104 / 53 | aviary optimiser: `Iteration limit reached` / `Positive directional derivative for linesearch` |
| generate_volume_mesh, no code | 149 of 164 | `Error calling tool 'generate_volume_mesh': Refusing this mesh: estimated ~429,593,941 cells exceeds the 3,000,000 cap.` (tigl-mcp cost cap, B69-A) |
| generate_volume_mesh, no code | 10 | `Error calling tool 'generate_volume_mesh': Unknown session_id '<id>'` |
| generate_volume_mesh, no code | 5 | `gmsh meshing failed: Invalid boundary mesh (overlapping facets) on surface 35 surface 36` |
| run_cycle, no code | 101 of 103 | `'SFC' is not an output of this cycle model. Do not guess another name: call list_variables ...` (also `'Fn'`, `'TSFC'`, `'SFC_CRUISE'`) |
| create_cycle_model, SESSION_ALREADY_OPEN (refused) | 121 | `A pycycle session is already open (session_id <id>). Use it, or close it with close_cycle_model first; create_cycle_model will then run.` |
| check_constraints | 13 / 6 | `run_simulation has not been called yet in this session.` / `Unknown constraint variable: 'oem_kg'. Supported: [...]` |
| set_inputs, no code | 25 of 27 | `{'type': 'UnknownVariable', ...}` (text shortened in the KB) |

- **Mesh cap:** the 149 capped mesh requests are spread over all combinations (S_SP 6, S_IF 6, S_GR 9, O_SP 10,
  O_IF 11, O_GR 1, N_IF 22, N_GR 84 calls). The capped calls ask for boundary layers with `mesh_size_min` 0.1 and
  `surface_mesh_size` 0.5 / 0.2 / 0.1; the successful agent calls mostly use `mesh_size_min` 0.33,
  `mesh_size_max` 12.0 (the two most common successful input sets, 110 and 95 calls). The cap works as designed (B69-A); it costs a call each time.
- **Expired / unknown session ids:** 42 failed calls (O_IF 22, N_GR 11, N_IF 5), mostly tigl
  (`set_high_level_parameters` 15, `generate_volume_mesh` 10, `get_high_level_parameters` 8).
- 848 of the 1,939 failed + refused records keep only `<N chars>` for the message (the KB shortens long strings),
  so refusal texts are described from the code, not quoted.

Tables: `failed_by_code.csv`, `failed_by_code_and_tool.csv`, `refused_by_code_and_tool.csv`,
`top_failing_tools_messages.csv`.

## 6. The canonical pipeline: which stages ran

Share of links where the stage succeeded at least once. "agent" = by an agent; "any" = by an agent or the
graph-routed pre-init. n = 20 links per combination. Geometry morph = a successful `set_high_level_parameters`,
`morph_wing` or `morph_fuselage`; cycle sized = a successful `run_cycle` with Fn != 5,900 lbf; mission = a successful
`run_simulation`.

| Comb. | Morph | Mesh agent / any | SU2 solve | Mass | Cycle | Cycle sized | Mission | Flew |
|---|---|---|---|---|---|---|---|---|
| S_SP | 0.85 | 1.00 / 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| S_IF | 0.35 | 1.00 / 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| S_GR | 0.40 | 0.40 / 1.00 | 0.55 | 0.60 | 0.95 | 0.50 | 0.80 | 0.80 |
| O_SP | 0.45 | 1.00 / 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| O_IF | 0.55 | 1.00 / 1.00 | 1.00 | 0.95 | 0.95 | 0.95 | 0.95 | 0.95 |
| O_GR | 0.40 | 0.40 / 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| N_IF | 0.55 | 1.00 / 1.00 | 1.00 | 1.00 | 0.95 | 0.70 | 0.35 | 0.35 |
| N_GR | 0.95 | 0.95 / 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.90 | 0.90 |

(SU2 solve, mass, cycle and mission come out identical for "agent" and "any". Graph-routed agents may legitimately
skip mesh, SU2 and mass because pre-init seeds those results from the previous link, which is why S_GR shows SU2
0.55 and mass 0.60.)

- Geometry was morphed in only 35–55% of links in 6 combinations; S_SP (0.85) and N_GR (0.95) morph most.
- Only S_GR (0.50) and N_IF (0.70) often ran no correctly sized cycle — the B187 combinations.

**Where the chain breaks on the 20 did-not-fly links** (`did_not_fly_chain_break.csv`):

| Comb. | Did-not-fly links | What happened |
|---|---|---|
| S_GR | 7, 13, 14, 15 | run_simulation ran 6 / 9 / 7 / 4 times, all ZERO_FUEL; no geometry morph; 13–15 also no successful agent SU2 solve or mass |
| O_IF | 8 (failed link) | run_simulation only refused (MISSING_REQUIRED_PARAMETERS x6); mass and cycle never succeeded |
| N_IF | 1, 2, 5, 8, 9, 11, 12, 13, 14, 15, 16, 17, 19 | ZERO_FUEL every time it ran (1–20 runs), plus 1–33 refusals per link; link 15 also 1 AVIARY_SETUP_ERROR |
| N_GR | 5, 11 | ZERO_FUEL x6 each, plus 14 / 34 refusals |

No did-not-fly link lacked a mesh. Missing stages among the 20: geometry morph 12, SU2 solve 3, mass 4, cycle 3.
So the chain usually runs end to end and **breaks at the mission itself (19 of 20 ZERO_FUEL)**; 8 of these 20 links
flew at least one mission with the B187 scale 0.994 (S_GR 7, 13, 14; N_IF 1, 2, 5, 9, 15).

## 7. run_simulation and missions flown

| Comb. | Calls | Success | Failed | Refused | Success / all calls | Success / executed | Missions flown per link mean (median, max) | Links flew | Same-fuel repeats (links) |
|---|---|---|---|---|---|---|---|---|---|
| S_SP | 25 | 22 | 0 | 3 | 0.880 | 1.000 | 3.10 (3.0, 7) | 20 | 2 (1) |
| S_IF | 22 | 21 | 0 | 1 | 0.955 | 1.000 | 3.10 (3.0, 5) | 20 | 1 (1) |
| S_GR | 107 | 50 | 57 | 0 | 0.467 | 0.467 | 7.15 (9.0, 29) | 16 | 24 (7) |
| O_SP | 23 | 20 | 0 | 3 | 0.870 | 1.000 | 2.15 (2.0, 3) | 20 | 0 (0) |
| O_IF | 74 | 30 | 0 | 44 | 0.405 | 1.000 | 3.20 (2.0, 11) | 19 | 10 (5) |
| O_GR | 76 | 68 | 3 | 5 | 0.895 | 0.958 | 6.30 (6.0, 12) | 20 | 36 (16) |
| N_IF | 242 | 11 | 71 | 160 | 0.045 | 0.134 | 1.00 (0.0, 5) | 7 | 4 (4) |
| N_GR | 524 | 196 | 36 | 292 | 0.374 | 0.845 | 14.85 (13.5, 37) | 18 | 127 (15) |

"Missions flown" is `links.csv` `n_missions_flown` (successful `run_simulation` / `get_results` with fuel > 100 kg;
n = 20 links each, did-not-fly links count 0). "Success / executed" leaves out refused calls. "Same-fuel repeats"
counts successful run_simulation calls whose fuel equals the previous successful mission's in the same link
(re-flying an unchanged mission). Non-success run_simulation by code: refused MISSING_REQUIRED_PARAMETERS 315,
failed ZERO_FUEL 162, refused CLAIMED_BY_ANOTHER_AGENT 69, GEOMETRY_NOT_APPLIED 53, NO_CHANGE_SINCE_FAILED_RUN 51,
AERO_NONPHYSICAL 20, failed AVIARY_SETUP_ERROR 5.

## 8. Run reliability

| Comb. | Completed | Failed | Did not fly | Links with > 1 runner attempt | Links with a retry line in the log |
|---|---|---|---|---|---|
| S_SP | 20 | 0 | 0 | 2 | 2 |
| S_IF | 20 | 0 | 0 | 1 | 1 |
| S_GR | 20 | 0 | 4 | 0 | 4 |
| O_SP | 20 | 0 | 0 | 3 | 3 |
| O_IF | 18 | 2 | 1 | 6 | 6 |
| O_GR | 20 | 0 | 0 | 0 | 0 |
| N_IF | 20 | 0 | 13 | 5 | 15 |
| N_GR | 18 | 2 | 2 | 0 | 2 |

- **Failed links** (`stat_progress.json`): O_IF 8 — `zero fuel_burned_kg — simulation produced no output`, 3
  attempts; O_IF 19, N_GR 6, N_GR 8 — `TimeoutError: Run exceeded 480m timeout`, 1 attempt. The three timed-out
  links flew before the timeout.
- **Retries** (runner log): every retry was `attempt k/3: zero fuel, retrying...`. Links with 2 attempts: S_SP 8,
  19; S_IF 18; O_SP 9, 16, 19; O_IF 4, 20; N_IF 2, 7, 15, 17. Three attempts: O_IF 7, 8, 13, 16; N_IF 3.
  N_IF 15 shows two "attempt 1" retry lines because the run restarted in a second log
  (v3combos_..._20261016 and _20261018). On the other links the log line is
  `the mission was flown and the design did not fly (ZERO_FUEL) -- recorded as infeasible`: no retry; this is how
  S_GR 7/13/14/15, most N_IF did-not-fly links and N_GR 5/11 were closed. The runner retried pre-fix zero-fuel links
  and recorded later ones as infeasible, so retry counts are not comparable across datasets.
- Earlier attempts' calls (734 in total) are in the counts above; per combination in section 1.

Details: `run_reliability.csv`, `run_reliability_links.csv`.

## 9. Contamination per link (B187, B214)

| Comb. | B187: unsized cycle in this link | B187: only re-applied inherited 0.994 | B214 | Failed | Did not fly |
|---|---|---|---|---|---|
| S_SP | none | none | 1 | none | none |
| S_IF | none | none | none | none | none |
| S_GR | 2, 4, 6, 7, 12, 13, 14, 17, 19 | 3, 5, 8, 16, 18, 20 | none | none | 7, 13, 14, 15 |
| O_SP | none | none | none | none | none |
| O_IF | 4 | none | none | 8, 19 | 8 |
| O_GR | 11 | none | none | none | none |
| N_IF | 1, 5, 6, 9, 15, 17 | 2 | none | none | 1, 2, 5, 8, 9, 11, 12, 13, 14, 15, 16, 17, 19 |
| N_GR | 10, 14 | none | none | 6, 8 | 5, 11 |

B214 (S_SP link 1): `export_cpacs -> ~/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple_modified.xml` (seq 9).

**Did the 0.994 engine reach the counted fuel?** For each B187 link the script follows the KB: the SCALE_FACTOR of
the last successful `set_aircraft_parameters` before each mission (KB inputs are the resolved call).

| Comb. link | Cycle hits on the pre-init pyCycle session | set_inputs before the hit | 0.994 set (seq) | Missions run at 0.994 | Counted mission: seq, scale |
|---|---|---|---|---|---|
| S_GR 2 | 5 of 5 | no | 20, 33, 56, 62 | 4 | 34, **0.9939** |
| S_GR 4 | 7 of 7 | no | 6 sets | 6 | 10, 1.7767 |
| S_GR 6 | 1 of 4 | no | 22, 32, 39 | 3 | 10, 2.0363 |
| S_GR 7 / 13 / 14 | all | no | 5–6 sets | 6 / 9 / 7, all ZERO_FUEL | did not fly |
| S_GR 12 / 17 | all | no | 4 / 6 sets | 4 / 5 | 10, 1.9122 / 10, 1.9122 |
| S_GR 19 | all | no | 30, 55, 77 | 3 | 56, **0.9939** |
| S_GR 3, 5, 8, 16, 18, 20 (inherited) | — | — | seq 9 (and 12) | 1–3 | not 0.994 |
| O_IF 4 | not graph-routed | yes | 11 | 0 | 25, 1.6968 |
| O_GR 11 | all | no | 21, 23, 29, 35 | 3, all ZERO_FUEL | 45, 1.9780 |
| N_IF 1, 2, 5, 9, 15 | not graph-routed | yes (1, 5, 9, 15) | yes | 1–6, all ZERO_FUEL | did not fly |
| N_IF 6 | not graph-routed | yes | none in KB | 0 | 39, not set in the link's KB (chain end in links.csv: 0.9939) |
| N_IF 17 | not graph-routed | no | none | 0 | did not fly |
| **N_GR 10** | 1 of 1 (seq 39, agent_2) | no | 41 | 1 (seq 42, ZERO_FUEL) | 98, 4.2116 |
| **N_GR 14** | 1 of 1 (seq 19, agent_1) | no | none | 0 | 106, 4.2116 |

So of the 17 B187 links that flew, the counted fuel was flown at scale 0.994 in S_GR 2 and 19 only; N_IF 6 can't be
decided from the KB (no scale set in the link; its chain-end scale is 0.994). In graph-routed combinations nearly every
unsized cycle ran on the session the pre-init opened without sizing it.

**Classification of the new N_GR hits (fixed code):**
- **N_GR link 10 — unsized cycle that reached aviary once; counted fuel unaffected.** agent_2's `run_cycle` (seq 39)
  on the pre-init session `0f1cb331…` (created by pre-init at seq 7, no `set_inputs` on it before) returned
  Fn = 5,899.99 lbf. That thrust was captured: agents later saw `prop.fn_lbf: 5899.999…` in `get_design_state`
  (2 observations) and design.json's mission block `Aircraft.Engine.SCALE_FACTOR: 0.9939353099730429`. agent_1
  passed 0.99394 at seq 41 (aviary echoed old 4.2116 → new 0.9939); the mission at seq 42 burned 3.8e-05 kg
  (ZERO_FUEL). Afterwards the framework replaced the asked 0.9939 by 4.21159 (`framework_applied.overridden`), the
  agents re-sized with `set_inputs` (Fn_DES 25,000 at seq 57) and the counted mission (seq 98, 41,560.9 kg) flew at
  4.2116.
- **N_GR link 14 — unsized cycle that never reached aviary.** agent_1's `run_cycle` (seq 19) on the pre-init session
  `867b1eea…` (no `set_inputs` before) returned Fn = 5,899.99 lbf; no 0.994 scale was ever set; the next
  `run_simulation` (seq 24) was refused `MISSING_REQUIRED_PARAMETERS` with `state: engine_stale` for
  `prop.sfc_cruise`; agent_2 then ran `set_inputs` (Fn_DES 25,000, alt 33,000 ft, M 0.78) and `run_cycle`
  (Fn = 25,000). Agents never saw a 5,899 thrust in the registry. Counted mission seq 106 at 4.2116.

Tables: `contamination_links.csv`, `contamination_summary.csv`.

## 10. Trend over links

Agent calls; means over links 1–10 and 11–20 (n = 10 each); Spearman rho of the per-link count with the link index
(n = 20).

| Comb. | Refused 1–10 → 11–20 | Failed 1–10 → 11–20 | (Failed+refused)/calls 1–10 → 11–20 | Calls 1–10 → 11–20 | rho refused | rho failed | rho calls |
|---|---|---|---|---|---|---|---|
| S_SP | 0.3 → 0.2 | 0.9 → 1.4 | 0.044 → 0.045 | 26.1 → 27.4 | 0.157 | -0.126 | 0.167 |
| S_IF | 0.0 → 0.3 | 1.0 → 1.8 | 0.034 → 0.054 | 24.9 → 30.3 | 0.298 | 0.249 | 0.317 |
| S_GR | 4.0 → 3.3 | 5.4 → 9.2 | 0.190 → 0.292 | 51.0 → 49.8 | -0.182 | 0.325 | 0.169 |
| O_SP | 0.5 → 0.0 | 2.0 → 2.2 | 0.084 → 0.070 | 28.0 → 30.8 | -0.306 | 0.193 | 0.526 |
| O_IF | 4.3 → 2.4 | 5.9 → 8.0 | 0.177 → 0.170 | 51.4 → 52.2 | 0.024 | 0.158 | 0.073 |
| O_GR | 1.4 → 0.9 | 6.2 → 10.3 | 0.146 → 0.180 | 50.8 → 63.7 | 0.062 | 0.277 | 0.296 |
| N_IF | 20.0 → 14.5 | 5.4 → 8.7 | 0.391 → 0.410 | 60.5 → 53.3 | -0.222 | 0.237 | 0.014 |
| N_GR | 14.8 → 27.2 | 12.5 → 18.9 | 0.230 → 0.274 | 122.3 → 191.4 | 0.312 | 0.267 | 0.402 |

All 160 links: rho = 0.017 (refused), 0.157 (failed). Nothing is learned along the chain: failures creep up in every
combination, refusals mostly stay flat or fall a little, and the error share per call barely moves. N_GR is the one
combination that clearly grows (more calls, more refusals). Per-link counts: `failed_plus_refused_per_link_wide.csv`;
plots `refused_mcp_calls_line_*.png` in `out_20261028/plots/4_coordination/`.

## Possible new issues (for Jessica to confirm)

1. **B187 fix gap on graph-routed pre-init sessions (N_GR link 10).** Evidence above: on the fixed code an unsized
   `run_cycle` on the pre-init pyCycle session was captured (agents saw `prop.fn_lbf` 5,899.99, design.json held scale
   0.9939) and one mission flew at 0.994. Commit efcb1a2 says "graph pre-init seeds count as sized": `seed_from_design`
   records `cycle_sizing = {session: <pre-init session>, key: <current key>}`, although the pre-init only called
   `create_cycle_model` on that session (no `set_inputs`). A plausible reading: `cycle_is_sized()` then returns True
   for the unsized session, so `_auto_size_cycle` skips it and the outputs are captured. Not reproduced; link 14 shows
   the same start but was caught (engine_stale), and the difference was not traced.
2. **N_GR sizes pyCycle on a hand-picked 25,000 lbf.** 75 of the 95 successful `set_inputs` calls in N_GR pass
   `Fn_DES = 25000.0` (17 of 20 links; median Fn_DES 25,000 lbf), and that value reaches pyCycle (in link 14 the next
   `run_cycle` returned Fn = 25,000). No other combination ever passed 25,000 (their Fn_DES runs from 5,900 lbf
   (N_IF minimum) to 18,921 lbf (O_IF maximum), apart from N_IF, whose maximum is 90,744 lbf). If the fix's intent is "Fn_DES from mass and drag", an agent-supplied Fn_DES is not replaced. The value
   is not in the MAS-Aviary code (`git grep` on integration/2026-10-21); where agents take it from was not checked.
3. **aviary-mcp echoes a read-only parameter.** 174 of 176 `READ_ONLY_PARAMETER` failures are agents setting
   `Aircraft.Wing.SPAN`; N_GR alone has 129 `READ_ONLY_PARAMETER` failures (6.45 per link, n=20). `aviary_mcp_server.py` (lines 173–175) adds
   `Aircraft.Wing.SPAN` (marked `derived`) to the `applied` list of its `set_aircraft_parameters` reply, and agents
   pass SPAN back on the next call (seen in N_GR link 10's step log: agent_1 steps 3–5 sent
   `Aircraft.Wing.SPAN: 33.4664`). Not in BUGS.md.
4. **Repeated identical missions on the fixed code.** B188 refuses repeats of a *failed* mission only; N_GR re-flew an
   unchanged successful mission 127 times (15 links; e.g. link 10 flew the same 6,668.9 kg mission 7 times, seq
   10–34). In the N_GR links 10 and 14 inspected, each of these missions reported 54–71 s of `runtime_seconds`.

## Caveats

- **Code versions differ.** v2full (5 combinations) predates B187/B188/B214 fixes and the record_recommendation fix;
  O_GR and N_IF are v3combos; N_GR is the fixed code. Some guards exist only in some versions (e.g.
  `CLAIMED_BY_ANOTHER_AGENT` appears only in N_IF, `CLAIM_FIRST`, `NO_CHANGE_SINCE_FAILED_RUN` and
  `AERO_NONPHYSICAL` only in N_GR). The code meanings above were read from the current MAS-Aviary-analysis worktree,
  not from each dataset's exact commit.
- The 2026-10-28 fixed-code N_IF re-run (`v4net_iter`, still running) is not used; N_IF here is the complete v3combos
  run.
- "Failed" mixes tool errors with physics outcomes: ZERO_FUEL missions (225 records) are flown missions the
  optimiser ended with no fuel burn.
- Counts include every runner attempt of a link (734 agent calls come from attempts that did not count).
- 848 of 1,939 failure / refusal records store only `<N chars>` for the text; messages quoted here come from the
  others.
- Tools "never used" is against the 2026-10-01 inventory snapshot; whether every agent's tool list contained every tool
  was not checked.
- B187 tracing uses `set_aircraft_parameters` inputs in the KB; a scale applied without such a call (N_IF 6) is not
  visible there.
- Trend statistics are descriptive (20 links per combination, one chain each); no significance testing.

How to reproduce (aipexws8, Avion venv, from `~/Jessica/avion_analysis/code`):
`python -m analysis.link20.reports.scripts.tools_reliability --links ../out_20261028/links.csv --out ../reports_20261028/tools_reliability`
(consistency check printed first: refused / failed totals per link equal `links.csv` `kb_refused` / `kb_failed`).
