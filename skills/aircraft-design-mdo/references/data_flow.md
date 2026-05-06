# Data Flow

> Inter-MCP data exchange schemas and transformation rules for the MDO skill.

## DesignState Schema

The `DesignState` is the central data object maintained by the MDO integrator agent
across all optimization iterations. It is never serialized to disk — it lives in
the orchestrator's working memory.

```python
class DesignState:
    cpacs_file_path: str              # Absolute path to the current CPACS XML file on disk
    sessions: dict[str, str]          # MCP name -> active session_id  {"tigl": "...", "su2": "...", ...}
    results: dict[str, dict]          # MCP name -> latest result payload  {"su2": {"CL": ..., "CD": ...}, ...}
    constraints: dict[str, dict]      # constraint_label -> {value, limit, operator, satisfied}
    iteration: int                    # Current MDO iteration counter (0-based)
    history: list[dict]               # One entry per iteration: {iteration, objective, constraints, design_vars, timestamp}
    data_store: dict[str, str]        # Large binary payloads (meshes, CAD files) — the data plane
```

### Data Plane / Control Plane Separation

The LLM is the **control plane** — it decides which tools to call and in what order.
The `data_store` is the **data plane** — it carries large binary payloads (base64
meshes, STEP files, etc.) between MCP tools without passing through the LLM context.

**How it works:**

1. When a tool returns a response containing a large binary field (>1KB base64 data,
   or a field named `*_base64`, `mesh_data`, etc.), the framework's response middleware
   automatically stores the payload in `data_store` and replaces the field with a
   lightweight reference: `{"ref": "tool__field", "size_bytes": N}`.

2. The LLM sees the reference (not the payload) and can pass it to the next tool.

3. When the next tool call includes a reference (`{"ref": "key"}` or a string matching
   a data store key), the framework's request middleware resolves it to the actual
   payload before sending to the MCP server.

**Example:** TiGL generates a volume mesh (~1-10MB base64). The LLM sees:
```json
{"format": "su2", "mesh_base64": {"ref": "generate_volume_mesh__mesh_base64", "size_bytes": 1234567}}
```
When the aero stage calls `set_mesh(mesh_base64={"ref": "generate_volume_mesh__mesh_base64"})`,
the framework resolves the ref and sends the full payload to SU2.

### Field Details

| Field | Type | Updated By | Read By |
|-------|------|-----------|---------|
| `cpacs_file_path` | `str` | tigl-mcp (geometry writes) | mass-mcp, tigl-mcp |
| `sessions` | `dict[str, str]` | Each MCP on session create/close | All MCP calls (session routing) |
| `results` | `dict[str, dict]` | Each MCP after computation | MDO integrator (convergence check) |
| `constraints` | `dict[str, dict]` | aviary-mcp `check_constraints` | MDO integrator (feasibility check) |
| `iteration` | `int` | MDO integrator | MDO integrator (termination) |
| `history` | `list[dict]` | MDO integrator (end of each iteration) | MDO integrator (convergence trend) |
| `data_store` | `dict[str, str]` | Response middleware (auto) | Request middleware (auto) |

---

## Inter-MCP Data Transfers

### 1. tigl-mcp --> su2-mcp: Volume Mesh for CFD

| Property | Value |
|----------|-------|
| **Source tool** | `tigl:generate_volume_mesh(session_id, component_uid)` (uses gmsh internally to embed the STL surface in a far-field box and produce a complete 3D volume mesh with "aircraft" wall + "farfield" markers) |
| **Target tool** | `su2:set_mesh(session_id, mesh_base64)` |
| **Data format** | Base64-encoded SU2 volume mesh string |
| **Transformation** | None — the base64 blob returned by tigl is passed directly to su2 `set_mesh`. |
| **Typical size** | 1-50 MB (base64), depending on mesh density |
| **Why not `export_component_mesh(format="su2")`?** | That tool produces a *surface-only* mesh (zero volume elements). SU2's `DistributeColoring` check rejects it and the solver immediately fails. `generate_volume_mesh` is the only tigl-mcp tool that produces a CFD-solveable mesh. |

```
tigl:generate_volume_mesh  --(mesh_base64: str)-->  su2:set_mesh
```

### 2. tigl-mcp --> mass-mcp: CPACS File on Disk

| Property | Value |
|----------|-------|
| **Source tool** | `tigl:close_cpacs(session_id)` (writes modified CPACS to disk) |
| **Target tool** | `mass:estimate_mass(cpacs_file_path)` or `mass:validate_cpacs_inputs(cpacs_file_path)` |
| **Data format** | File path string pointing to the CPACS XML file |
| **Transformation** | None — mass-mcp reads the file directly from the filesystem. The orchestrator passes `DesignState.cpacs_file_path` as the `cpacs_file_path` argument. |

```
tigl:close_cpacs  --(cpacs_file_path: str on disk)-->  mass:estimate_mass
```

### 3. su2-mcp --> aviary-mcp: Drag Polar

| Property | Value |
|----------|-------|
| **Source tool** | `su2:read_history_csv(session_id, relative_path, columns=["CL", "CD"])` |
| **Target tool** | `aviary:set_aircraft_parameters(session_id, parameters)` |
| **Data format** | CL and CD arrays extracted from the SU2 convergence history CSV |
| **Transformation** | The orchestrator must: (1) Call `read_history_csv` for each AoA run to get the converged CL, CD pair. (2) Assemble the drag polar as paired scalar values. (3) Pass the drag data to aviary via `set_aircraft_parameters` using the appropriate Aviary variable names for aerodynamic inputs. |

```
su2:read_history_csv  --(CL, CD floats per AoA)-->  [orchestrator assembles polar]  -->  aviary:set_aircraft_parameters
```

### 4. mass-mcp --> aviary-mcp: OEM and Component Breakdown

| Property | Value |
|----------|-------|
| **Source tool** | `mass:estimate_mass(cpacs_file_path)` |
| **Target tool** | `aviary:set_aircraft_parameters(session_id, parameters)` |
| **Data format** | Scalar values — OEM in kg, component masses in kg |
| **Transformation** | The orchestrator extracts the OEM (kg) and relevant component masses from the mass-mcp result dict and maps them to Aviary parameter names (e.g., `Aircraft.Design.OPERATING_EMPTY_MASS`). Unit conversion may be needed if aviary expects lbm. |

```
mass:estimate_mass  --(OEM_kg, wing_mass_kg, ...)-->  [orchestrator maps to Aviary params]  -->  aviary:set_aircraft_parameters
```

### 5. pycycle-mcp --> aviary-mcp: SFC and Thrust

| Property | Value |
|----------|-------|
| **Source tool** | `pycycle:get_outputs(session_id, names=["SFC", "Fn", ...])` |
| **Target tool** | `aviary:set_aircraft_parameters(session_id, parameters)` |
| **Data format** | Scalar values — SFC in lbm/hr/lbf, net thrust (Fn) in lbf |
| **Transformation** | The orchestrator reads SFC and thrust from pycycle outputs and maps them to Aviary engine parameters. If aviary expects tabulated data, the orchestrator runs `pycycle:sweep_inputs` across altitude/Mach points and constructs the tables. |

```
pycycle:get_outputs  --(SFC, Fn scalars)-->  [orchestrator maps to Aviary engine params]  -->  aviary:set_aircraft_parameters
```

### 6. aviary-mcp --> MDO Integrator: Mission Results

| Property | Value |
|----------|-------|
| **Source tool** | `aviary:get_results(session_id)` and `aviary:check_constraints(session_id, constraints)` |
| **Target tool** | MDO integrator (orchestrator logic) |
| **Data format** | Scalar values — fuel burned (lbm), GTOW (lbm), convergence status (bool) |
| **Transformation** | The orchestrator reads fuel_burned, GTOW, and optimizer convergence from aviary results. It converts to SI units if needed, evaluates the objective function (minimize MTOM), updates `DesignState.constraints` and appends to `DesignState.history`. |

```
aviary:get_results        --(fuel_burned, GTOW, converged)-->  MDO integrator
aviary:check_constraints  --(constraint verdicts)-->           MDO integrator
```

---

## Data Flow Diagram (Per Iteration)

```
                    CPACS file path
    tigl-mcp ──────────────────────────> mass-mcp
       │                                    │
       │ mesh_base64 (SU2 format)           │ OEM, component masses (kg)
       v                                    v
    su2-mcp                             aviary-mcp <── pycycle-mcp
       │                                    │           (SFC, thrust)
       │ CL, CD (drag polar)                │
       └───────────────> aviary-mcp         │
                            │               │
                            v               v
                        MDO Integrator
                     (fuel_burned, GTOW,
                      constraint status,
                      convergence check)
```

## Notes

- All inter-MCP transfers pass through the orchestrator agent. MCPs never call each other directly.
- Base64 payloads (mesh, CAD) are opaque blobs — the orchestrator forwards them without decoding.
- Scalar values (OEM, CL, CD, SFC) require the orchestrator to extract, potentially convert units, and map to target parameter names.
- The CPACS file on disk is the only shared-filesystem coupling. All other data flows through MCP tool return values.
