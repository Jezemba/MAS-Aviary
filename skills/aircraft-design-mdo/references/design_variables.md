# Design Variables

> Complete catalog of design variables for the MDO skill. For each variable: full
> name, controlling MCP(s), F25 baseline default, valid range, units, coupled
> variables, and physical effect.

---

## aviary-mcp Design Variables (10 parameters)

These are set via `aviary:set_aircraft_parameters(session_id, parameters)` and
queried via `aviary:get_design_space()`.

### Aircraft.Wing.ASPECT_RATIO

| Property | Value |
|----------|-------|
| **MCP** | aviary-mcp |
| **Default (F25)** | 15.6 |
| **Valid range** | 7 - 14 |
| **Units** | dimensionless |
| **Coupled variables** | Wing.SPAN, Wing.AREA (AR = span^2 / area); wing structural mass; induced drag |
| **Physical effect** | Higher AR reduces induced drag (improves L/D) but increases wing bending loads and structural mass. The F25 baseline of 15.6 exceeds the recommended optimization range upper bound of 14, reflecting an aggressive high-AR design. The optimizer should be aware that the baseline sits outside the recommended range. |

### Aircraft.Wing.AREA

| Property | Value |
|----------|-------|
| **MCP** | aviary-mcp |
| **Default (F25)** | 130.1 |
| **Valid range** | 100 - 160 |
| **Units** | m2 |
| **Coupled variables** | Wing.ASPECT_RATIO, Wing.SPAN (area = span^2 / AR); wing loading; CLmax sizing; fuel tank volume |
| **Physical effect** | Larger wing area reduces wing loading, lowering approach speed (Vref) and takeoff distance (TOFL). However, it increases wetted area and parasite drag, and adds structural weight. Too small an area violates landing speed constraints (C3). |

### Aircraft.Wing.SPAN

| Property | Value |
|----------|-------|
| **MCP** | aviary-mcp |
| **Default (F25)** | 45.0 |
| **Valid range** | 28 - 48 |
| **Units** | m |
| **Coupled variables** | Wing.ASPECT_RATIO, Wing.AREA; airport compatibility (ICAO code); wing bending moment |
| **Physical effect** | Span directly controls induced drag (longer span = lower induced drag) and wing root bending moment. The 45 m baseline exceeds ICAO Code C (36 m) gate limits. Span is geometrically linked to AR and area — changing one requires adjusting at least one of the other two for consistency. |

### Aircraft.Wing.SWEEP

| Property | Value |
|----------|-------|
| **MCP** | aviary-mcp |
| **Default (F25)** | 25.0 |
| **Valid range** | 15 - 40 |
| **Units** | deg |
| **Coupled variables** | Wave drag (Mach_dd); wing structural mass; flutter speed; CLmax |
| **Physical effect** | Sweep delays transonic drag divergence, enabling higher cruise Mach. More sweep increases structural mass (longer structural path), reduces CLmax (hurting low-speed performance), and shifts the flutter boundary. The F25 moderate sweep of 25 deg is consistent with Mach 0.78 cruise. |

### Aircraft.Wing.TAPER_RATIO

| Property | Value |
|----------|-------|
| **MCP** | aviary-mcp |
| **Default (F25)** | 0.278 |
| **Valid range** | 0.15 - 0.45 |
| **Units** | dimensionless |
| **Coupled variables** | Spanwise lift distribution; wing root bending moment; stall behavior |
| **Physical effect** | Taper ratio (tip chord / root chord) controls the spanwise lift distribution. Lower taper shifts lift inboard, reducing root bending moment (lighter structure) but moving away from the elliptical optimum (more induced drag). Very low taper (<0.2) causes tip stall risk. |

### Aircraft.Fuselage.LENGTH

| Property | Value |
|----------|-------|
| **MCP** | aviary-mcp |
| **Default (F25)** | 37.79 |
| **Valid range** | 28 - 50 |
| **Units** | m |
| **Coupled variables** | Fuselage wetted area; passenger capacity; tail arm; static margin |
| **Physical effect** | Longer fuselage increases wetted area and parasite drag but provides more cabin volume (passengers/cargo) and a longer moment arm for the horizontal tail (better static stability). Shortening the fuselage below ~34 m may not accommodate 239 pax in a reasonable layout. |

### Aircraft.Fuselage.MAX_HEIGHT

| Property | Value |
|----------|-------|
| **MCP** | aviary-mcp |
| **Default (F25)** | 4.06 |
| **Valid range** | 3.0 - 5.5 |
| **Units** | m |
| **Coupled variables** | Fuselage cross-section area; fuselage drag; cargo volume; structural hoop stress |
| **Physical effect** | Larger fuselage height increases cross-section area (more drag) but allows under-floor cargo containers and can improve cabin headroom. Reducing below ~3.8 m may preclude LD3-45 containers. |

### Aircraft.Fuselage.MAX_WIDTH

| Property | Value |
|----------|-------|
| **MCP** | aviary-mcp |
| **Default (F25)** | 3.76 |
| **Valid range** | 3.0 - 5.5 |
| **Units** | m |
| **Coupled variables** | Fuselage cross-section area; seating abreast count; fuselage drag |
| **Physical effect** | Wider fuselage enables more seats abreast (e.g., 3-3 vs. 2-3 layout), reducing fuselage length for the same pax count. However, it increases fuselage drag and structural weight. The F25 at 3.76 m supports a 3-3 single-aisle configuration. |

### Aircraft.Engine.SCALE_FACTOR

| Property | Value |
|----------|-------|
| **MCP** | aviary-mcp |
| **Default (F25)** | 1.0 |
| **Valid range** | 0.8 - 1.5 |
| **Units** | dimensionless |
| **Coupled variables** | Engine mass; nacelle drag; SLS thrust; SFC (indirectly); TOFL; 2nd segment climb |
| **Physical effect** | Scales the reference engine linearly in thrust, mass, and dimensions. Values > 1.0 increase available thrust (helps TOFL, climb) but add weight and nacelle drag. Values < 1.0 save weight but risk violating takeoff and climb constraints. |

### Aircraft.Engine.SCALED_SLS_THRUST

| Property | Value |
|----------|-------|
| **MCP** | aviary-mcp |
| **Default (F25)** | (computed) |
| **Valid range** | Read-only |
| **Units** | lbf |
| **Coupled variables** | Engine.SCALE_FACTOR (derived from it) |
| **Physical effect** | This is a **read-only output**, not a design variable. It reports the sea-level static thrust after applying the scale factor. Used for constraint checking (TOFL, 2nd segment climb) but not directly settable by the optimizer. |

---

## tigl-mcp Design Variables

Set via `tigl:set_high_level_parameters(session_id, component_uid, updates)`.
The exact parameter names are CPACS XPath-derived and depend on the component UID.

### Wing Geometry Parameters

| Parameter | CPACS Path (typical) | F25 Baseline | Range | Units | Physical Effect |
|-----------|---------------------|--------------|-------|-------|-----------------|
| Span | `wing/span` | 45.0 | 28-48 | m | Same as aviary Wing.SPAN — tigl is the geometry source |
| Root chord | `wing/sections/section[1]/chord` | ~7.0 | 4-12 | m | Affects wing area, t/c, fuel volume |
| Tip chord | `wing/sections/section[N]/chord` | ~1.9 | 0.8-4 | m | Sets taper ratio with root chord |
| Sweep (LE) | `wing/positionings/positioning/sweepAngle` | 25 | 15-40 | deg | Leading-edge sweep angle |
| Dihedral | `wing/positionings/positioning/dihedralAngle` | ~5 | 0-8 | deg | Lateral stability; ground clearance |
| Twist (tip) | `wing/sections/section[N]/transformation/rotation/y` | ~-2 | -5 to 0 | deg | Washout for stall behavior and load distribution |

**Note:** tigl parameters are the geometric source of truth. When tigl modifies wing geometry, the CPACS file is updated and mass-mcp reads the new values. aviary-mcp parameters (AR, area, span) are derived/aggregated values that should be kept consistent with tigl geometry.

### Fuselage Geometry Parameters

| Parameter | CPACS Path (typical) | F25 Baseline | Range | Units | Physical Effect |
|-----------|---------------------|--------------|-------|-------|-----------------|
| Length | `fuselage/length` | 37.79 | 28-50 | m | Total fuselage length |
| Nose length | `fuselage/sections/section[1]/...` | ~4 | 2-6 | m | Nose fineness ratio (drag) |
| Tail cone length | `fuselage/sections/section[N]/...` | ~8 | 5-12 | m | Upsweep angle, rear drag |
| Section radii | `fuselage/sections/section[i]/radius` | ~1.88 | 1.5-2.75 | m | Cross-section shape (derives height/width) |

---

## mass-mcp Design Variables

Set as arguments to `mass:estimate_mass(...)`.

| Parameter | Argument Name | Default | Options/Range | Units | Physical Effect |
|-----------|--------------|---------|---------------|-------|-----------------|
| Wing mass method | `wing_mass_method` | `"flops"` | `"flops"`, `"gasp"`, `"oas"`, `"both"` | — | Selects the wing mass estimation model. `"oas"` uses OpenAeroStruct VLM+FEM (higher fidelity). `"both"` runs FLOPS and OAS and returns both. |
| Aviary mass method | `aviary_mass_method` | `"FLOPS"` | `"FLOPS"`, `"GASP"` | — | Selects the empirical regression set for non-wing components (fuselage, empennage, landing gear, etc.) |
| OAS wing weight ratio | `oas_wing_weight_ratio` | 1.25 | 1.0 - 2.0 | dimensionless | Calibration factor applied to OAS-computed wing mass. Accounts for secondary structure not modeled in OAS. |
| Material | `material` | `"aluminum"` | `"aluminum"`, `"composite"` | — | Sets material properties (E, rho, sigma_yield) for OAS structural sizing. Composite reduces wing mass by ~15-25%. |
| Design load factor | `design_load_factor` | 2.5 | 1.5 - 3.8 | g | Ultimate load factor for structural sizing. Higher values produce heavier structure. FAR 25 requires 2.5g limit (3.75g ultimate). |

---

## pycycle-mcp Design Variables

Set via `pycycle:set_inputs(session_id, values)` after `create_cycle_model`.
Variable names depend on the cycle model but typical high-bypass turbofan variables include:

| Parameter | Typical Variable Name | Default | Range | Units | Physical Effect |
|-----------|----------------------|---------|-------|-------|-----------------|
| Bypass ratio (BPR) | `fan.BPR` or `cycle.BPR` | ~11 | 5 - 15 | dimensionless | Higher BPR lowers cruise SFC but increases fan diameter, nacelle drag, and engine mass |
| Overall pressure ratio (OPR) | `cycle.OPR` | ~40 | 25 - 50 | dimensionless | Higher OPR improves thermal efficiency and SFC but increases compressor weight, cost, and cooling requirements |
| Fan pressure ratio (FPR) | `fan.PR` | ~1.5 | 1.2 - 1.8 | dimensionless | Lower FPR favors propulsive efficiency (higher BPR); higher FPR gives more specific thrust (smaller engine) |
| Turbine inlet temperature | `burner.T4` or `cycle.T4max` | ~1600 | 1400 - 1900 | K | Higher T4 increases specific thrust and thermal efficiency but demands better turbine cooling and materials |
| Compressor polytropic efficiency | `HPC.poly_eff` | ~0.91 | 0.88 - 0.93 | dimensionless | Technology parameter — higher values represent better compressor design |
| Turbine polytropic efficiency | `HPT.poly_eff` | ~0.90 | 0.87 - 0.92 | dimensionless | Technology parameter — higher values represent better turbine design |

**Note:** Use `pycycle:list_variables(session_id, kind="inputs")` to discover the exact variable names for the loaded cycle model. The names above are representative and may vary.

---

## su2-mcp Configuration Variables

Set via `su2:update_config_entries(session_id, updates)`. These are solver
configuration parameters, not aircraft design variables, but they must be set
correctly for each analysis point.

| Parameter | Config Key | Default | Range | Units | Purpose |
|-----------|-----------|---------|-------|-------|---------|
| Freestream Mach | `MACH_NUMBER` | 0.78 | 0.1 - 0.85 | dimensionless | Must match the flight condition being analyzed |
| Angle of attack | `AOA` | 2.0 | -2 - 12 | deg | Swept across values to build the drag polar |
| Reynolds number | `REYNOLDS_NUMBER` | ~30e6 | 1e6 - 100e6 | dimensionless | Based on MAC and flight condition; affects skin friction |
| Sideslip angle | `SIDESLIP_ANGLE` | 0.0 | -5 - 5 | deg | Typically 0 for symmetric cruise analysis |
| Turbulence model | `KIND_TURB_MODEL` | `SA` | `SA`, `SST` | — | SA (Spalart-Allmaras) is faster; SST (Menter k-omega) better for separation |
| Iterations | `ITER` | 1000 | 100 - 10000 | — | More iterations for tighter convergence; mesh-dependent |
| CFL number | `CFL_NUMBER` | 10.0 | 1 - 100 | — | Solver stability parameter; lower for difficult cases |

---

## Variable Coupling Map

Design variables that affect multiple disciplines simultaneously:

| Variable | Aero | Structures | Propulsion | Mission | Fuel Volume |
|----------|------|-----------|------------|---------|-------------|
| Wing.ASPECT_RATIO | Induced drag | Bending mass | — | Range/fuel | — |
| Wing.AREA | Parasite drag, wing loading | Wing mass | — | TOFL, Vref | Tank volume |
| Wing.SPAN | Induced drag | Bending moment | — | — | — |
| Wing.SWEEP | Wave drag, CLmax | Structural path | — | Mach capability | — |
| Wing.TAPER_RATIO | Lift distribution | Root bending | — | — | — |
| Fuselage.LENGTH | Wetted area drag | Fuselage mass | — | Pax capacity | — |
| Engine.SCALE_FACTOR | Nacelle drag | Engine mass | Thrust, SFC | TOFL, climb | — |
| BPR (pycycle) | Nacelle diameter | Engine mass | SFC curve | Fuel burn | — |
| Wing t/c (tigl) | Wave drag | Spar sizing | — | — | Tank volume |
| Material (mass) | — | Wing mass | — | OEM -> fuel | — |

---

## Notes

- aviary-mcp and tigl-mcp both control wing geometry but at different abstraction levels. aviary uses aggregated parameters (AR, area, span); tigl uses section-level CPACS geometry. The orchestrator must keep them consistent. The recommended workflow is: modify geometry in tigl, then update aviary parameters to match.
- The valid ranges listed are recommended optimization bounds, not hard physical limits. Values outside these ranges may still produce valid MCP outputs but are unlikely to yield realistic aircraft designs.
- `Aircraft.Engine.SCALED_SLS_THRUST` is read-only in aviary-mcp. The optimizer should use `SCALE_FACTOR` as the design variable and read the resulting thrust as an output.
- pycycle variable names are model-dependent. Always call `list_variables` after creating a cycle model to discover the actual names.
