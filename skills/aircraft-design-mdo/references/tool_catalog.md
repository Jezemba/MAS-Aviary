# Tool Catalog

> **Source of truth**: Discovered via live MCP tool listing (2026-03-29).
> Do NOT manually edit tool schemas — re-run discovery if servers are updated.

## Summary

| # | Server | Domain | URL | Tools |
|---|--------|--------|-----|-------|
| 1 | **tigl-mcp** | Geometry / CPACS Lifecycle | `http://127.0.0.1:8500/mcp` | 15 |
| 2 | **su2-mcp** | CFD Aerodynamics | `http://127.0.0.1:8200/mcp` | 17 |
| 3 | **mass-mcp** | Structural Mass Estimation | `http://127.0.0.1:8700/mcp` | 3 |
| 4 | **pycycle-mcp** | Engine Thermodynamic Cycles | `http://127.0.0.1:8400/mcp` | 10 |
| 5 | **aviary-mcp** | Mission Trajectory Optimization | `http://127.0.0.1:8600/mcp` | 9 |

---

## tigl-mcp — Geometry / CPACS Lifecycle

- **URL**: `http://127.0.0.1:8500/mcp`
- **Transport**: streamable-http
- **Session-based**: Yes
- **Session creation**: `open_cpacs`
- **Tool count**: 15

### `ping`

Lightweight health check that confirms the server is reachable without requiring a CPACS session or TiGL runtime.

**Input parameters:**
  - `message` (string | null, optional, default: `None`): 

### `open_cpacs`

Open a CPACS session from a file path or an XML string.

**Input parameters:**
  - `source_type` (string, required): 
  - `source` (string, required): 

### `close_cpacs`

Close a CPACS session and free resources.

**Input parameters:**
  - `session_id` (string, required): 

### `get_configuration_summary`

Return component lists and the overall bounding box.

**Input parameters:**
  - `session_id` (string, required): 

### `list_geometric_components`

List CPACS geometric components.

**Input parameters:**
  - `session_id` (string, required): 
  - `type_filter` (string | null, optional, default: `None`): 

### `get_component_metadata`

Return metadata for a geometric component.

**Input parameters:**
  - `session_id` (string, required): 
  - `component_uid` (string, required): 

### `get_wing_summary`

Return key geometric metrics for a wing.

**Input parameters:**
  - `session_id` (string, required): 
  - `wing_uid` (string, required): 

### `get_fuselage_summary`

Return key geometric metrics for a fuselage.

**Input parameters:**
  - `session_id` (string, required): 
  - `fuselage_uid` (string, required): 

### `sample_component_surface`

Sample 3D points on a component surface.

**Input parameters:**
  - `session_id` (string, required): 
  - `component_uid` (string, required): 
  - `parameterization` (string, required): 
  - `samples` (array, required): 

### `intersect_with_plane`

Intersect a component with a plane and sample polylines.

**Input parameters:**
  - `session_id` (string, required): 
  - `component_uid` (string, required): 
  - `plane_point` (object, required): 
  - `plane_normal` (object, required): 
  - `n_points_per_curve` (integer, optional, default: `50`): 

### `intersect_components`

Intersect two components and return sampled curves.

**Input parameters:**
  - `session_id` (string, required): 
  - `component_uid_one` (string, required): 
  - `component_uid_two` (string, required): 
  - `n_points_per_curve` (integer, optional, default: `50`): 

### `export_component_mesh`

Export a component mesh as base64-encoded content.

**Input parameters:**
  - `session_id` (string, required): 
  - `component_uid` (string, required): 
  - `format` (string, required): 
  - `meshing_options` (object | null, optional, default: `None`): 

### `export_configuration_cad`

Export the full configuration CAD and return it encoded.

**Input parameters:**
  - `session_id` (string, required): 
  - `format` (string, required): 

### `get_high_level_parameters`

Return high-level design parameters for a component.

**Input parameters:**
  - `session_id` (string, required): 
  - `component_uid` (string, required): 

### `set_high_level_parameters`

Update high-level design parameters and return the new values.

**Input parameters:**
  - `session_id` (string, required): 
  - `component_uid` (string, required): 
  - `updates` (object, required): 

---

## su2-mcp — CFD Aerodynamics

- **URL**: `http://127.0.0.1:8200/mcp`
- **Transport**: streamable-http
- **Session-based**: Yes
- **Session creation**: `create_su2_session`
- **Tool count**: 17

### `ping`

Return a simple health check response without requiring SU2.   The tool intentionally avoids importing SU2 or touching the filesystem so that  MCP clients can verify connectivity even when SU2 binaries are unavailable.

**Input parameters:**
  - `message` (string | null, optional, default: `None`): Optional message to echo back in the response.

### `create_su2_session`

Create a new SU2 session and return basic paths.

**Input parameters:**
  - `base_name` (string | null, optional, default: `None`): 
  - `initial_config` (string | null, optional, default: `None`): 
  - `initial_mesh` (string | null, optional, default: `None`): 
  - `mesh_file_name` (string, optional, default: `mesh.su2`): 

### `close_su2_session`

Close a session and optionally delete its working directory.

**Input parameters:**
  - `session_id` (string, required): 
  - `delete_workdir` (boolean, optional, default: `False`): 

### `get_session_info`

Return session paths and last run metadata.

**Input parameters:**
  - `session_id` (string, required): 

### `get_config_text`

Return raw config text for the session.

**Input parameters:**
  - `session_id` (string, required): 

### `parse_config`

Parse the session configuration into key/value entries.

**Input parameters:**
  - `session_id` (string, required): 

### `update_config_entries`

Update configuration entries for a session.

**Input parameters:**
  - `session_id` (string, required): 
  - `updates` (object, required): 
  - `create_if_missing` (boolean, optional, default: `True`): 

### `set_mesh`

Persist a mesh file for the session and update the config.

**Input parameters:**
  - `session_id` (string, required): 
  - `mesh_base64` (string, required): 
  - `mesh_file_name` (string, optional, default: `mesh.su2`): 
  - `update_config` (boolean, optional, default: `True`): 

### `generate_mesh_from_step`

Generate a 3D SU2 mesh from a STEP file and attach it to the given session.   Uses a .geo template that merges the STEP, builds an aircraft volume (surface  loop -> volume), creates a farfield box, and meshes the fluid domain with  FARFIELD and WALL markers. Requires the `gmsh` CLI to be on PATH....

**Input parameters:**
  - `session_id` (string, required): 
  - `step_base64` (string, required): 
  - `output_mesh_name` (string, optional, default: `mesh.su2`): 
  - `geo_template_path` (string | null, optional, default: `None`): 
  - `gmsh_timeout_seconds` (integer, optional, default: `600`): 

### `analyze_mesh`

Analyze the mesh attached to a session and return diagnostics.   Reports element counts by type, node count, boundary marker summary,  and estimated solver runtime scaling factors to help diagnose latency.

**Input parameters:**
  - `session_id` (string, required): 

### `run_su2_solver`

Run a SU2 solver process and capture output metadata.

**Input parameters:**
  - `session_id` (string, required): 
  - `solver` (string, optional, default: `SU2_CFD`): 
  - `config_override_path` (string | null, optional, default: `None`): 
  - `max_runtime_seconds` (integer, optional, default: `600`): 
  - `capture_log_lines` (integer, optional, default: `100`): 

### `generate_deformed_mesh`

Run SU2_DEF to create a deformed mesh.

**Input parameters:**
  - `session_id` (string, required): 
  - `def_config_path` (string | null, optional, default: `None`): 
  - `output_mesh_name` (string, optional, default: `mesh_def.su2`): 
  - `max_runtime_seconds` (integer, optional, default: `600`): 

### `get_su2_status`

Return SU2 availability information for the host.   Returns:   Mapping that indicates whether any SU2 binaries are available alongside   per-binary details.   Examples:   >>> status = get_su2_status()   >>> set(status.keys()) == {"installed", "binaries", "missing"}   True

**Input parameters:**
  - *(none)*

### `list_result_files`

List result files produced within a session directory.

**Input parameters:**
  - `session_id` (string, required): 
  - `extensions` (array | null, optional, default: `None`): 

### `get_result_file_base64`

Return a base64-encoded slice of a result file.

**Input parameters:**
  - `session_id` (string, required): 
  - `relative_path` (string, required): 
  - `max_bytes` (integer, optional, default: `104857600`): 

### `read_history_csv`

Read a subset of a history CSV file.

**Input parameters:**
  - `session_id` (string, required): 
  - `relative_path` (string, required): 
  - `columns` (array | null, optional, default: `None`): 
  - `max_rows` (integer, optional, default: `1000`): 
  - `skip_rows` (integer, optional, default: `0`): 

### `sample_surface_solution`

Sample surface solution fields from a CSV-like file.

**Input parameters:**
  - `session_id` (string, required): 
  - `relative_path` (string, required): 
  - `marker_name` (string | null, required): 
  - `fields` (array, required): 
  - `max_points` (integer, optional, default: `5000`): 

---

## mass-mcp — Structural Mass Estimation

- **URL**: `http://127.0.0.1:8700/mcp`
- **Transport**: streamable-http
- **Session-based**: No
- **Tool count**: 3

### `estimate_mass`

Run a full mass estimation on a CPACS file. Computes the operational empty mass (OEM) and component breakdown using Aviary FLOPS/GASP regression and/or OpenAeroStruct physics-based VLM+FEM for the wing.

**Input parameters:**
  - `cpacs_file_path` (string, required): 
  - `wing_mass_method` (string, optional, default: `flops`): 
  - `aviary_mass_method` (string, optional, default: `FLOPS`): 
  - `oas_wing_weight_ratio` (number, optional, default: `1.25`): 
  - `material` (string, optional, default: `aluminum`): 
  - `design_load_factor` (number, optional, default: `2.5`): 
  - `write_back_to_cpacs` (boolean, optional, default: `True`): 

### `validate_cpacs_inputs`

Inspect a CPACS file and report which parameters are present, missing, or out of valid range before running estimation. Run this first when debugging.

**Input parameters:**
  - `cpacs_file_path` (string, required): 
  - `wing_mass_method` (string, optional, default: `flops`): 

### `get_cpacs_mass_breakdown`

Read and return mass data already written into a CPACS file from a prior estimate_mass run. Does not re-run any computation.

**Input parameters:**
  - `cpacs_file_path` (string, required): 

---

## pycycle-mcp — Engine Thermodynamic Cycles

- **URL**: `http://127.0.0.1:8400/mcp`
- **Transport**: streamable-http
- **Session-based**: Yes
- **Session creation**: `create_cycle_model`
- **Tool count**: 10

### `ping`

Simple healthcheck for the pyCycle MCP server.

**Input parameters:**
  - `message` (string | null, optional, default: `None`): Optional echo message included in the response.

### `create_cycle_model`

Instantiate a pyCycle/OpenMDAO Problem for a specified engine cycle.

**Input parameters:**
  - `cycle_type` (string, required): 
  - `mode` (string, required): 
  - `options` (object | null, optional, default: `None`): 
  - `cycle_module_path` (string | null, optional, default: `None`): 

### `close_cycle_model`

Close a pyCycle session and free resources.

**Input parameters:**
  - `session_id` (string, required): 

### `get_cycle_summary`

Return a succinct summary of the current cycle model.

**Input parameters:**
  - `session_id` (string, required): 

### `list_variables`

List variables in the cycle model.

**Input parameters:**
  - `session_id` (string, required): 
  - `kind` (string, optional, default: `both`): 
  - `promoted_only` (boolean, optional, default: `True`): 
  - `name_filter` (string | null, optional, default: `None`): 
  - `max_variables` (integer, optional, default: `200`): 

### `set_inputs`

Set one or more input variables in the cycle model.

**Input parameters:**
  - `session_id` (string, required): 
  - `values` (object, required): 
  - `allow_missing` (boolean, optional, default: `False`): 

### `get_outputs`

Fetch values for one or more outputs after a run.

**Input parameters:**
  - `session_id` (string, required): 
  - `names` (array, required): 
  - `allow_missing` (boolean, optional, default: `False`): 

### `run_cycle`

Run the cycle model and return selected outputs.

**Input parameters:**
  - `session_id` (string, required): 
  - `outputs_of_interest` (array | null, optional, default: `None`): 
  - `use_driver` (boolean, optional, default: `False`): 

### `sweep_inputs`

Perform a parametric sweep over input variables.

**Input parameters:**
  - `session_id` (string, required): 
  - `sweep` (array, required): 
  - `outputs_of_interest` (array | null, optional, default: `None`): 
  - `use_driver` (boolean, optional, default: `False`): 
  - `skip_on_failure` (boolean, optional, default: `True`): 

### `compute_totals`

Compute total derivatives using OpenMDAO.

**Input parameters:**
  - `session_id` (string, required): 
  - `of` (array, required): 
  - `wrt` (array, required): 
  - `return_format` (string, optional, default: `by_pair`): 

---

## aviary-mcp — Mission Trajectory Optimization

- **URL**: `http://127.0.0.1:8600/mcp`
- **Transport**: streamable-http
- **Session-based**: Yes
- **Session creation**: `create_session`
- **Tool count**: 9

### `get_design_space`

Returns the full list of modifiable aircraft design parameters with their current default values, units, and recommended bounds.  Read-only and stateless — no session_id required. Call this first to understand what can be changed before creating a session.  Args:  category: Filter by category. On...

**Input parameters:**
  - `category` (string, optional, default: `all`): 

### `create_session`

Creates a new Aviary session. Loads the default aircraft (737/A320-class single-aisle transport) and returns a session_id.  The session persists server-side until explicitly closed or the idle timeout elapses (30 minutes). This is always the first call in any design workflow.  Args:  initial_para...

**Input parameters:**
  - `initial_parameters` (object, optional, default: `None`): 

### `set_aircraft_parameters`

Modifies one or more aircraft design parameters in the session.  Does not run a simulation — only updates session state. Parameter names must be valid Aviary variable strings as returned by get_design_space. Values outside the recommended range are accepted with a warning.  Args:  session_id: Ses...

**Input parameters:**
  - `session_id` (string, required): 
  - `parameters` (object, required): 

### `configure_mission`

Defines the mission profile the aircraft will fly. Does not run a simulation.  The mission is defined by range, number of passengers, cruise Mach number, and cruise altitude. Defaults are set for a typical medium-haul mission.  Args:  session_id: Session handle.  range_nmi: Mission range in nauti...

**Input parameters:**
  - `session_id` (string, required): 
  - `range_nmi` (number, optional, default: `None`): 
  - `num_passengers` (integer, optional, default: `None`): 
  - `cruise_mach` (number, optional, default: `None`): 
  - `cruise_altitude_ft` (number, optional, default: `None`): 
  - `optimizer_max_iter` (integer, optional, default: `None`): 

### `validate_parameters`

Validates the current aircraft parameters and mission config WITHOUT running a full optimization. Use this between set_aircraft_parameters / configure_mission and run_simulation to catch bad inputs early.  Performs two layers of validation:   1. Static checks (instant): NaN/inf detection, bounds ...

**Input parameters:**
  - `session_id` (string, required): 
  - `timeout_seconds` (integer, optional, default: `30`): 

### `run_simulation`

Triggers the Aviary trajectory optimization for the current session.  Runs Aviary's internal SLSQP optimizer to solve the coupled aircraft-trajectory problem. Blocks until completion or timeout.  Args:  session_id: Session handle.  timeout_seconds: Wall-clock timeout in seconds. Default: 300.

**Input parameters:**
  - `session_id` (string, required): 
  - `timeout_seconds` (integer, optional, default: `300`): 

### `get_results`

Reads performance output variables from the most recently completed simulation.  Can be called after run_simulation, including after a timed-out run.  Args:  session_id: Session handle.  variables: List of specific output variable names to return. If omitted, all standard outputs returned.

**Input parameters:**
  - `session_id` (string, required): 
  - `variables` (array, optional, default: `None`): 

### `get_trajectory`

Returns trajectory timeseries data from the most recently completed simulation.  Provides per-phase (climb/cruise/descent) timeseries: time, altitude, Mach, mass, throttle, drag, and distance. Each array is concatenated across all phases with a matching phase_labels array to identify which phase ...

**Input parameters:**
  - `session_id` (string, required): 
  - `variables` (array, optional, default: `None`): 

### `check_constraints`

Evaluates whether the latest simulation results satisfy user-defined constraints.  Returns a structured pass/fail assessment with margin values.  Args:  session_id: Session handle.  constraints: Array of constraint objects. Each has: variable, operator, value, units (optional), label.   Supported...

**Input parameters:**
  - `session_id` (string, required):
  - `constraints` (array, required):

---

## Tool Call Sequences

### Per-MCP Session Lifecycle

**tigl-mcp:**
1. `open_cpacs(source_type, source)` → returns `session_id`
2. Inspect: `get_configuration_summary`, `list_geometric_components`, `get_wing_summary`, `get_fuselage_summary`
3. Modify: `set_high_level_parameters(session_id, component_uid, updates)`
4. Export: `export_component_mesh(session_id, component_uid, format="su2")` or `export_configuration_cad(session_id, format="step")`
5. `close_cpacs(session_id)`

**su2-mcp:**
1. `create_su2_session(initial_mesh, initial_config)` → returns `session_id`
2. Configure: `update_config_entries(session_id, updates)` to set Mach, AoA, Reynolds, etc.
3. Mesh: `set_mesh(session_id, mesh_base64)` or `generate_mesh_from_step(session_id, step_base64)`
4. Solve: `run_su2_solver(session_id, solver="SU2_CFD")`
5. Results: `list_result_files`, `read_history_csv`, `sample_surface_solution`, `get_result_file_base64`
6. `close_su2_session(session_id)`

**mass-mcp:**
1. `validate_cpacs_inputs(cpacs_file_path)` — pre-flight check
2. `estimate_mass(cpacs_file_path, wing_mass_method)` — full estimation
3. `get_cpacs_mass_breakdown(cpacs_file_path)` — read prior results from CPACS

**pycycle-mcp:**
1. `create_cycle_model(cycle_name)` → returns `session_id`
2. `list_variables(session_id)` — inspect available inputs/outputs
3. `set_inputs(session_id, variables)` — set BPR, OPR, fan pressure ratio, etc.
4. `run_cycle(session_id)` — execute the model
5. `get_outputs(session_id, variables)` — read SFC, thrust, etc.
6. Optional: `sweep_inputs`, `compute_totals` for parametric/sensitivity analysis
7. `close_cycle_model(session_id)`

**aviary-mcp:**
1. `get_design_space(category)` — understand available parameters (stateless)
2. `create_session(initial_parameters)` → returns `session_id`
3. `set_aircraft_parameters(session_id, parameters)` — set wing/fuselage/engine params
4. `configure_mission(session_id, range_nmi, num_passengers, cruise_mach, cruise_altitude_ft)`
5. `validate_parameters(session_id)` — catch bad inputs before simulation
6. `run_simulation(session_id)` — SLSQP trajectory optimization (~40-60s)
7. `get_results(session_id)` — fuel burn, GTOW, wing mass
8. `get_trajectory(session_id)` — timeseries data
9. `check_constraints(session_id, constraints)` — pass/fail assessment

### Cross-MCP Workflows

**Geometry → CFD (drag polar):**
1. tigl: `open_cpacs` → modify geometry → `export_component_mesh(format="su2")`
2. su2: `create_su2_session` → `set_mesh(mesh_base64)` → `update_config_entries` → `run_su2_solver`
3. su2: `read_history_csv` → extract CL, CD values

**Geometry → Mass estimation:**
1. tigl: `open_cpacs` → modify geometry → `close_cpacs` (file saved on disk)
2. mass: `validate_cpacs_inputs(cpacs_file_path)` → `estimate_mass(cpacs_file_path)`

**Full MDO iteration:**
1. tigl: Create/modify CPACS geometry
2. su2: Run CFD → get drag polar (CL, CD vs AoA)
3. mass: Estimate OEM from CPACS
4. pycycle: Size engine → get SFC, thrust tables
5. aviary: Set aircraft params (OEM, drag data, engine data) → configure mission → run simulation → check constraints
6. If not converged: adjust geometry/engine → repeat from step 1

### Approximate Runtimes

| Tool | Typical Runtime |
|------|----------------|
| tigl-mcp tools | < 1s (stub) |
| su2 config/mesh tools | < 1s |
| su2 `run_su2_solver` | 10s - 600s (mesh-dependent) |
| mass `estimate_mass` | 5-30s (method-dependent) |
| pycycle `run_cycle` | 1-5s |
| pycycle `sweep_inputs` | 5-30s (point-count-dependent) |
| aviary `validate_parameters` | 6-9s |
| aviary `run_simulation` | 40-60s (200 SLSQP iterations) |
