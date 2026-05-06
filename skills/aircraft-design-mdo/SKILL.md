---
name: aircraft-design-mdo
description: >
  Multi-disciplinary aircraft design optimization using 5 MCP servers
  (TiGL geometry, SU2 CFD, mass estimation, PyCycle propulsion, Aviary
  mission). Use when agents need to compose tool calls across multiple
  aircraft design MCPs, understand inter-disciplinary data flow, evaluate
  design constraints, or reason about aerodynamic/structural/propulsion
  tradeoffs.
---

# Aircraft Design MDO Pipeline

## 1. Pipeline Overview

Five MCP servers form the MDO loop. Each owns one discipline.

| MCP | Domain | Port | Session? | Session Creator |
|-----|--------|------|----------|-----------------|
| **tigl-mcp** | Geometry / CPACS | 8500 | Yes | `open_cpacs` |
| **su2-mcp** | CFD Aerodynamics | 8200 | Yes | `create_su2_session` |
| **mass-mcp** | Structural Mass | 8700 | No | -- |
| **pycycle-mcp** | Propulsion Cycles | 8400 | Yes | `create_cycle_model` |
| **aviary-mcp** | Mission Trajectory | 8600 | Yes | `create_session` |

**Data contracts:**
- The **CPACS XML file** is the shared geometry contract between tigl-mcp and mass-mcp. Both read/write the same file on disk.
- **Scalar results** (CL, CD, OEM, SFC, MTOM, fuel burn) flow between MCPs via a `DesignState` dict maintained by the orchestrating agent.
- **Binary artifacts** (meshes, STEP files) are passed as base64 strings between tigl-mcp and su2-mcp.

**Session management:** Each session-based MCP returns a `session_id` on creation. The orchestrator must track all active session IDs in `DesignState` and close them when the MDO loop terminates.

---

## 2. Discipline Roles

### 2.1 Geometry (tigl-mcp)

- **Controls:** Wing planform (span, sweep, AR, taper, dihedral), airfoil shape, fuselage length/diameter, nacelle position, control surface sizing.
- **Produces:** CPACS file (consumed by mass-mcp), SU2 surface mesh or STEP CAD (consumed by su2-mcp), wetted areas, reference areas (Sref), MAC.
- **Consumes:** Design variable updates from the optimizer (span, sweep, AR changes).
- **Tradeoffs:** Increasing AR raises L/D but increases wing weight and flutter risk. Sweep improves transonic drag but reduces low-speed CL_max.

### 2.2 Aerodynamics (su2-mcp)

- **Controls:** Mesh resolution, turbulence model, angle of attack sweep range, flight condition (Mach, altitude, Reynolds).
- **Produces:** Drag polar (CL vs CD), pressure distribution, moment coefficients, L/D at cruise. These feed into aviary-mcp as aerodynamic performance tables.
- **Consumes:** Surface mesh from tigl-mcp, flight conditions (Mach, altitude) from mission definition.
- **Tradeoffs:** Higher-fidelity meshes improve accuracy but increase solve time (10s to 600s). Drag divergence Mach constrains cruise speed.

### 2.3 Structures (mass-mcp)

- **Controls:** Wing mass method (FLOPS regression vs OpenAeroStruct VLM+FEM), material selection (aluminum/composite), design load factor.
- **Produces:** OEM and component mass breakdown (wing, fuselage, tail, landing gear, systems). OEM is a primary input to aviary-mcp.
- **Consumes:** CPACS geometry file from tigl-mcp (span, areas, volumes, structural layout).
- **Tradeoffs:** Composites reduce wing mass ~15-20% but are not yet captured in FLOPS regression. Higher load factor increases structural mass but improves safety margin.

### 2.4 Propulsion (pycycle-mcp)

- **Controls:** Bypass ratio (BPR), overall pressure ratio (OPR), fan pressure ratio, turbine inlet temperature (T4), design altitude/Mach.
- **Produces:** Thrust-specific fuel consumption (SFC), net thrust, engine mass flow, engine weight estimate. SFC and thrust tables feed into aviary-mcp.
- **Consumes:** Design cruise Mach and altitude from mission definition; thrust requirement derived from drag at cruise.
- **Tradeoffs:** Higher BPR lowers SFC but increases nacelle diameter and drag. Higher OPR improves thermal efficiency but increases compressor weight and T4.

### 2.5 Mission (aviary-mcp)

- **Controls:** Mission profile (range, cruise Mach, cruise altitude, passenger count), optimizer iteration count.
- **Produces:** MTOM (GTOW), fuel burn, range achieved, takeoff field length (TOFL), approach speed (Vref), trajectory timeseries.
- **Consumes:** OEM from mass-mcp, drag polar from su2-mcp, SFC/thrust from pycycle-mcp, wing area and AR from tigl-mcp.
- **Tradeoffs:** This is the integrating discipline. It reveals whether the combined design closes: does the fuel required for the mission, plus OEM, plus payload, fit within MTOM? Increasing range demands more fuel which increases MTOM which increases drag.

---

## 3. Tool Call Sequences

### 3.1 Per-MCP Required Order

**tigl-mcp:**
```
open_cpacs(source_type, source)          → session_id
get_configuration_summary(session_id)    → component list, bbox
get_wing_summary / get_fuselage_summary  → geometry metrics
set_high_level_parameters(session_id, component_uid, updates)
export_component_mesh(session_id, component_uid, format="su2")  → mesh_base64
close_cpacs(session_id)
```

**su2-mcp:**
```
create_su2_session()                     → session_id
set_mesh(session_id, mesh_base64)
update_config_entries(session_id, updates)   # Mach, AoA, Reynolds, etc.
run_su2_solver(session_id, solver="SU2_CFD")
read_history_csv(session_id, relative_path)  → CL, CD convergence
sample_surface_solution(session_id, ...)     → Cp distribution
close_su2_session(session_id)
```

**mass-mcp (stateless):**
```
validate_cpacs_inputs(cpacs_file_path)       → missing/out-of-range warnings
estimate_mass(cpacs_file_path, wing_mass_method)  → OEM, component breakdown
```

**pycycle-mcp:**
```
create_cycle_model(cycle_type, mode)     → session_id
set_inputs(session_id, values)           # BPR, OPR, T4, etc.
run_cycle(session_id, outputs_of_interest)
get_outputs(session_id, names)           → SFC, thrust, mass flow
close_cycle_model(session_id)
```

**aviary-mcp:**
```
get_design_space(category)               → parameter names, bounds, defaults
create_session(initial_parameters)       → session_id
set_aircraft_parameters(session_id, parameters)  # OEM, Sref, AR, drag polars
configure_mission(session_id, range_nmi, num_passengers, cruise_mach, cruise_altitude_ft)
validate_parameters(session_id)          → static + quick-solve checks
run_simulation(session_id)               → SLSQP trajectory optimization
get_results(session_id)                  → MTOM, fuel burn, etc.
check_constraints(session_id, constraints)  → pass/fail per constraint
```

### 3.2 Cross-MCP Sequences

**Geometry to CFD (drag polar):**
1. tigl: `open_cpacs` -- modify wing -- `export_component_mesh(format="su2")` -- get `mesh_base64`
2. su2: `create_su2_session` -- `set_mesh(mesh_base64)` -- `update_config_entries({MACH_NUMBER, AoA, ...})`
3. su2: `run_su2_solver` -- `read_history_csv` -- extract converged CL, CD
4. Repeat step 2-3 at multiple AoA to build full drag polar

**Geometry to Mass:**
1. tigl: `open_cpacs` -- modify geometry -- `close_cpacs` (saves CPACS to disk)
2. mass: `validate_cpacs_inputs(cpacs_path)` -- `estimate_mass(cpacs_path)` -- get OEM

**Full MDO Iteration Loop:**
```
for iteration in 1..max_iterations:
    1. GEOMETRY:   tigl modify CPACS → export mesh + save CPACS
    2. AERO:       su2 run CFD → drag polar (CL, CD tables)
    3. STRUCTURES:  mass estimate → OEM
    4. PROPULSION: pycycle run → SFC, thrust
    5. MISSION:    aviary set params (OEM, drag, SFC) → run sim → get MTOM
    6. CONVERGE?   |MTOM_new - MTOM_old| / MTOM_old < 0.005 → STOP
    7. UPDATE:     adjust design variables → repeat
```

### 3.3 Approximate Runtimes

| Step | Typical Time |
|------|-------------|
| tigl geometry ops | < 1s |
| su2 config/mesh setup | < 1s |
| su2 `run_su2_solver` | 10s - 600s |
| mass `estimate_mass` | 5 - 30s |
| pycycle `run_cycle` | 1 - 5s |
| aviary `validate_parameters` | 6 - 9s |
| aviary `run_simulation` | 40 - 60s |
| **Full MDO iteration** | **~2 - 12 min** |

---

## 4. Design Constraints (DLR-F25 Baseline)

### 4.1 Reference Aircraft

The DLR-F25 is a 239-passenger medium-range transport (A320neo class):

| Parameter | Target Value |
|-----------|-------------|
| MTOM | 85,700 kg (85.7 t) |
| OEM | 46,300 kg (46.3 t) |
| Aspect Ratio | 15.6 |
| Cruise Mach | 0.78 |
| Design Range | 2,500 nmi |
| Passengers | 239 (single-class) |

### 4.2 Optimization Problem

**Objective:** Minimize MTOM

**Subject to:**

| Constraint | Operator | Limit | Units |
|------------|----------|-------|-------|
| Range | >= | 2,500 | nmi |
| TOFL (takeoff field length) | <= | 2,200 | m |
| Approach speed (Vref) | <= | 136 | KCAS |
| Fuel burn (block) | <= | report | kg |
| OEM | <= | report | kg |
| Wing span | <= | 36 | m (ICAO Code C gate) |

Use `aviary-mcp` `check_constraints` to evaluate these after each simulation:
```json
[
  {"variable": "range", "operator": ">=", "value": 2500, "units": "nmi", "label": "min_range"},
  {"variable": "TOFL", "operator": "<=", "value": 2200, "units": "m", "label": "max_tofl"},
  {"variable": "Vref", "operator": "<=", "value": 136, "units": "kn", "label": "max_vref"}
]
```

See `references/f25_constraints.md` for the complete constraint set with margin bands and active/inactive classification.

### 4.3 Convergence Criteria

- **MTOM convergence:** Relative change < 0.5% between successive outer-loop iterations: `|MTOM[i] - MTOM[i-1]| / MTOM[i-1] < 0.005`
- **Maximum iterations:** 10 outer-loop iterations. If not converged, report best feasible design.
- **Inner-loop:** aviary-mcp runs up to 200 SLSQP iterations internally per call.

---

## 5. Convergence & Termination

After each MDO outer-loop iteration, classify the state as one of:

### RETRY
Simulation diverged or a solver failed (su2 NaN, aviary optimizer infeasible on first pass). Actions:
- Reduce step size on design variable changes
- Coarsen then re-refine mesh
- Reset pycycle to last known good inputs
- Maximum 2 retries per iteration before escalating

### CONTINUE
All solvers completed but constraints are violated while the design is still improving (MTOM decreasing or constraint violations shrinking). Actions:
- Log constraint violations and margins
- Update design variables toward feasibility
- Proceed to next outer-loop iteration

### COMPLETE
MTOM has converged (< 0.5% change) AND all checkable constraints are satisfied. Actions:
- Report final MTOM, OEM, fuel burn, range, TOFL, Vref
- Compute optimality gap vs DLR-F25 baseline: `gap = (MTOM_final - 85700) / 85700 * 100%`
- Close all MCP sessions
- Archive DesignState with full iteration history

### Optimality Assessment

| Gap vs Baseline | Rating |
|----------------|--------|
| < 2% | Excellent -- within noise of reference |
| 2 - 5% | Good -- minor discipline modeling differences |
| 5 - 10% | Acceptable -- review mass or drag assumptions |
| > 10% | Investigate -- likely a discipline is miscalibrated |

### Termination Summary Template

```
MDO Complete: {COMPLETE|CONTINUE|RETRY}
Iterations:   {n} / 10
MTOM:         {value} kg  (baseline: 85700 kg, gap: {pct}%)
OEM:          {value} kg  (baseline: 46300 kg)
Fuel burn:    {value} kg
Range:        {value} nmi  (req: >= 2500)
TOFL:         {value} m    (req: <= 2200)
Vref:         {value} KCAS (req: <= 136)
Constraints:  {n_pass}/{n_total} passed
```
