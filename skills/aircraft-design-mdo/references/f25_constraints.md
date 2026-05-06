# DLR-F25 Benchmark Constraints

> Optimization formulation, top-level aircraft requirements (TLARs), and constraint
> evaluation mapping for the DLR-F25 reference aircraft.

## Top-Level Aircraft Requirements (TLARs)

| Requirement | Value | Unit |
|-------------|-------|------|
| Design range | 2500 | nmi |
| Passenger count | 239 | pax |
| Cruise Mach | 0.78 | - |
| Initial cruise altitude (ICA) | 33 000 | ft |
| Max operating Mach (MMO) | 0.82 | - |
| Take-off field length (TOFL) | <= 2200 | m |
| Approach speed (Vref) | <= 136 | KCAS |
| 2nd segment climb gradient | >= 2.4 | % |

---

## DLR-F25 Baseline Values

| Parameter | Value | Unit |
|-----------|-------|------|
| MTOM (maximum take-off mass) | 85 700 | kg |
| MLM (maximum landing mass) | 74 200 | kg |
| OEM (operating empty mass) | 46 300 | kg |
| Design fuel mass | 12 100 | kg |
| Wing span | 45.0 | m |
| Wing aspect ratio | 15.6 | - |
| Wing reference area | 130.1 | m2 |
| CL at cruise | 0.593 | - |
| L/D at cruise | 19.5 | - |

---

## Optimization Formulation

```
minimize   MTOM

subject to:
  Range                >= 2500 nmi
  TOFL                 <= 2200 m
  Vref                 <= 136 KCAS
  Wing fuel tank ratio >= 1.1
  2nd segment climb    >= 2.4%
  ICA                  == 33000 ft
  Rear spar height     >= 0.15 m
  No flow separation   (Cf and pressure distribution check)
  No structural buckling (FEM margin >= 1.0)
  No buffet/flutter    (aeroelastic stability)

design variables:
  Wing: aspect ratio, area, span, sweep, taper ratio
  Fuselage: length, max height, max width
  Engine: scale factor
  Propulsion cycle: BPR, OPR, fan pressure ratio
```

---

## Constraint Evaluation Map

### C1 — Design Range

| Property | Value |
|----------|-------|
| **Constraint** | Range >= 2500 nmi |
| **MCP** | aviary-mcp |
| **Tool** | `check_constraints` or `get_results` |
| **Output field** | `Mission.Summary.RANGE` (nmi) |
| **Operator** | `>=` |
| **Limit** | 2500 |
| **Unit** | nmi |
| **Fidelity** | MEDIUM — Aviary uses Breguet-based trajectory optimization with FLOPS-level aero/propulsion models |

### C2 — Take-Off Field Length

| Property | Value |
|----------|-------|
| **Constraint** | TOFL <= 2200 m |
| **MCP** | aviary-mcp |
| **Tool** | `get_results` |
| **Output field** | `Mission.Takeoff.FIELD_LENGTH` (m) |
| **Operator** | `<=` |
| **Limit** | 2200 |
| **Unit** | m |
| **Fidelity** | LOW — empirical FLOPS/GASP takeoff model, no CFD in the loop |

### C3 — Approach Speed

| Property | Value |
|----------|-------|
| **Constraint** | Vref <= 136 KCAS |
| **MCP** | aviary-mcp |
| **Tool** | `get_results` |
| **Output field** | `Mission.Landing.APPROACH_SPEED` (KCAS) |
| **Operator** | `<=` |
| **Limit** | 136 |
| **Unit** | KCAS |
| **Fidelity** | LOW — derived from wing loading and CLmax (empirical) |

### C4 — Wing Fuel Tank Volume Ratio

| Property | Value |
|----------|-------|
| **Constraint** | Available fuel volume / required fuel volume >= 1.1 |
| **MCP** | NOT directly checkable with current MCPs |
| **Tool** | N/A |
| **Output field** | N/A |
| **Operator** | `>=` |
| **Limit** | 1.1 |
| **Unit** | dimensionless |
| **Fidelity** | N/A |
| **Note** | Requires a dedicated fuel-tank geometry tool or manual cross-check. tigl can provide wing internal volume via section intersections, but no MCP currently computes the ratio against required fuel volume automatically. The orchestrator should flag this as an unchecked constraint. |

### C5 — 2nd Segment Climb Gradient

| Property | Value |
|----------|-------|
| **Constraint** | Climb gradient >= 2.4% |
| **MCP** | aviary-mcp |
| **Tool** | `get_results` |
| **Output field** | `Mission.Takeoff.SECOND_SEGMENT_CLIMB_GRADIENT` (%) |
| **Operator** | `>=` |
| **Limit** | 2.4 |
| **Unit** | % |
| **Fidelity** | LOW — one-engine-inoperative calculation using empirical T/W and drag estimate |

### C6 — Initial Cruise Altitude

| Property | Value |
|----------|-------|
| **Constraint** | ICA == 33 000 ft |
| **MCP** | aviary-mcp |
| **Tool** | `configure_mission` |
| **Output field** | `cruise_altitude_ft` (configurable input, not a computed constraint) |
| **Operator** | `==` |
| **Limit** | 33 000 |
| **Unit** | ft |
| **Fidelity** | Configurable — set as mission input; aviary optimizer will determine if the aircraft can reach this altitude given the design |

### C7 — Rear Spar Height

| Property | Value |
|----------|-------|
| **Constraint** | Rear spar structural height >= 0.15 m |
| **MCP** | mass-mcp |
| **Tool** | `estimate_mass` (with `wing_mass_method="oas"`) |
| **Output field** | Structural model minimum spar height from OAS FEM results |
| **Operator** | `>=` |
| **Limit** | 0.15 |
| **Unit** | m |
| **Fidelity** | LOW — OpenAeroStruct uses a simplified beam FEM; spar height is a geometric check against the wing thickness distribution |

### C8 — Flow Separation

| Property | Value |
|----------|-------|
| **Constraint** | No flow separation at cruise condition |
| **MCP** | su2-mcp |
| **Tool** | `sample_surface_solution` (inspect Cf distribution) or `read_history_csv` (check residual convergence) |
| **Output field** | Skin friction coefficient (Cf) distribution; negative Cf indicates separation |
| **Operator** | `>` (all Cf values must be positive in attached-flow regions) |
| **Limit** | 0.0 |
| **Unit** | dimensionless |
| **Fidelity** | MEDIUM — RANS CFD captures separation reasonably well but depends on turbulence model and mesh resolution |

### C9 — Structural Buckling

| Property | Value |
|----------|-------|
| **Constraint** | Structural buckling margin >= 1.0 (no buckling) |
| **MCP** | mass-mcp |
| **Tool** | `estimate_mass` (with `wing_mass_method="oas"`) |
| **Output field** | Buckling safety factor from OpenAeroStruct FEM |
| **Operator** | `>=` |
| **Limit** | 1.0 |
| **Unit** | dimensionless |
| **Fidelity** | LOW — OAS beam model with simplified panel buckling criteria; does not capture local skin buckling modes |

### C10 — Buffet and Flutter

| Property | Value |
|----------|-------|
| **Constraint** | No buffet onset below Mach 0.82; no flutter within flight envelope |
| **MCP** | NOT checkable with current MCPs |
| **Tool** | N/A |
| **Output field** | N/A |
| **Operator** | N/A |
| **Limit** | N/A |
| **Unit** | N/A |
| **Fidelity** | N/A |
| **Note** | Buffet prediction requires unsteady RANS or wind-tunnel data. Flutter analysis requires aeroelastic eigenvalue solver (e.g., NASTRAN). Neither is available in the current MCP stack. The orchestrator should flag this as an unchecked constraint, especially dangerous at AR=15.6. |

---

## Constraint Summary Table

| ID | Constraint | Limit | Operator | MCP | Fidelity | Checkable |
|----|-----------|-------|----------|-----|----------|-----------|
| C1 | Range | 2500 nmi | >= | aviary | MEDIUM | Yes |
| C2 | TOFL | 2200 m | <= | aviary | LOW | Yes |
| C3 | Vref | 136 KCAS | <= | aviary | LOW | Yes |
| C4 | Fuel tank volume ratio | 1.1 | >= | — | — | No |
| C5 | 2nd segment climb | 2.4% | >= | aviary | LOW | Yes |
| C6 | ICA | 33000 ft | == | aviary | Config | Yes (input) |
| C7 | Rear spar height | 0.15 m | >= | mass | LOW | Yes |
| C8 | Flow separation | Cf > 0 | > | su2 | MEDIUM | Yes |
| C9 | Structural buckling | margin >= 1.0 | >= | mass (OAS) | LOW | Yes |
| C10 | Buffet/flutter | envelope clear | — | — | — | No |

## Notes

- Constraints C4 and C10 cannot be evaluated with the current MCP tool stack. The orchestrator must log these as unchecked and include a warning in any optimization report.
- Constraint C6 (ICA) is not a computed output but a mission input — it constrains the mission profile definition rather than the optimizer output.
- All "LOW" fidelity constraints use empirical or simplified structural models. Results should be treated as screening-level; detailed analysis requires higher-fidelity tools outside the current MCP stack.
- The F25 baseline values above represent the DLR-published reference point. Optimized designs may deviate significantly, especially in MTOM and fuel mass.
