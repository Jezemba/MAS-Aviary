# Discipline Tradeoffs

> Cross-disciplinary coupling knowledge for the MDO skill. Each section describes
> a pair of interacting disciplines, the physical mechanism, the direction of the
> coupling, and how the current MCP stack can (or cannot) capture it.

---

## 1. Aerodynamics <--> Structures

### Coupling: Wing Area / Aspect Ratio vs. Wing Mass

**Physical mechanism:** Increasing wing area or aspect ratio improves aerodynamic
efficiency (higher L/D, lower induced drag) but produces a longer, more slender
wing box that must resist higher bending moments. The structural mass grows
roughly with span squared for a given load, creating a direct tradeoff against
the aero benefit.

**Direction:**
- Higher AR --> lower induced drag (aero benefit) --> higher wing bending mass (structural penalty)
- Higher wing area --> lower wing loading --> lower cruise CL (aero) but heavier wing structure

**MCP evaluation:**
- Aero side: su2-mcp runs RANS at the cruise condition to capture the CL/CD improvement from AR changes.
- Structures side: mass-mcp `estimate_mass(wing_mass_method="oas")` uses OpenAeroStruct VLM+FEM to compute the wing structural mass under aerodynamic loads.
- The orchestrator must iterate between these two to find the AR that minimizes MTOM, not just drag.

**Key insight:** The F25 baseline AR of 15.6 is aggressive. Small increases in AR yield diminishing aero returns while structural mass grows steeply. The optimum is sensitive to material choice (composite vs. aluminum) and load factor.

---

## 2. Aerodynamics <--> Propulsion

### Coupling: Engine Scale vs. Drag and Weight

**Physical mechanism:** Larger engines produce more thrust and can improve SFC at
the design point, but the nacelle diameter grows, increasing parasite drag and
interference drag at the wing-pylon junction. Engine weight also increases,
raising MTOM and therefore required lift and induced drag.

**Direction:**
- Larger engine (higher scale_factor) --> lower SFC at design point (propulsion benefit) --> higher nacelle drag (aero penalty) + higher engine mass (weight penalty)
- Smaller engine --> worse SFC, risk of insufficient thrust at takeoff (C2, C5 constraints)

**MCP evaluation:**
- Propulsion side: pycycle-mcp sizes the engine cycle; `Aircraft.Engine.SCALE_FACTOR` in aviary-mcp scales thrust and weight.
- Aero side: su2-mcp can capture nacelle drag if the mesh includes the nacelle geometry (exported via tigl-mcp). However, typical MDO iterations may skip nacelle CFD for speed and rely on aviary's built-in drag bookkeeping.
- The orchestrator should use pycycle to determine SFC and thrust, then let aviary's internal engine model apply the drag increment.

**Key insight:** The engine scale factor interacts with both TOFL (C2) and 2nd segment climb (C5). Under-sizing the engine to save drag and weight risks violating takeoff constraints.

---

## 3. Structures <--> Propulsion

### Coupling: Heavier Airframe --> More Thrust --> More Weight (Positive Feedback)

**Physical mechanism:** A heavier airframe requires more thrust to meet takeoff
and climb constraints. More thrust means a larger (heavier) engine, which further
increases MTOM, which in turn demands even more structural strength. This is a
positive feedback loop (sometimes called the "snowball effect") that can cause
MTOM to diverge if not properly converged.

**Direction:**
- Higher OEM --> higher MTOM --> higher required thrust --> larger engine --> higher engine mass --> even higher MTOM
- This loop converges only when the marginal weight increment per thrust increment becomes small enough

**MCP evaluation:**
- Structures: mass-mcp provides OEM.
- Propulsion: pycycle-mcp sizes the engine; aviary-mcp applies `Aircraft.Engine.SCALE_FACTOR`.
- The orchestrator must run multiple inner-loop iterations (mass -> engine sizing -> mass) until MTOM stabilizes (typically 3-5 iterations, convergence criterion: delta MTOM < 0.1%).

**Key insight:** This loop is the reason MDO cannot be solved in a single pass. The orchestrator must detect divergence (MTOM increasing monotonically) and intervene by reducing design variable aggressiveness.

---

## 4. Propulsion <--> Mission

### Coupling: BPR Affects SFC Curve; Aligning SFC Bucket with Cruise Thrust

**Physical mechanism:** Bypass ratio (BPR) shifts the engine's specific fuel
consumption curve. Higher BPR generally lowers cruise SFC but reduces specific
thrust, requiring a larger fan diameter for the same net thrust. The SFC "bucket"
(minimum SFC region) must align with the cruise thrust requirement for minimum
fuel burn over the mission.

**Direction:**
- Higher BPR --> lower cruise SFC (fuel benefit) --> larger fan diameter --> more nacelle drag and weight
- If the SFC bucket does not align with cruise thrust, the engine operates off-design with higher SFC

**MCP evaluation:**
- pycycle-mcp: `sweep_inputs` across altitude/Mach conditions to map the SFC curve vs. thrust. Vary BPR, OPR, and fan pressure ratio to shift the bucket.
- aviary-mcp: `run_simulation` uses the SFC/thrust data to compute mission fuel burn.
- The orchestrator should run pycycle sweeps at the cruise condition (Mach 0.78, 33000 ft) and verify that minimum SFC occurs near the required cruise thrust.

**Key insight:** For the F25 at Mach 0.78 and 33000 ft, the cruise thrust is approximately 4000-5000 lbf per engine (depending on MTOM). The pycycle design should target minimum SFC in this thrust range.

---

## 5. Wing Geometry <--> Fuel Volume

### Coupling: Thinner Wings Reduce Wave Drag but Decrease Fuel Tank Volume

**Physical mechanism:** Reducing wing thickness-to-chord ratio (t/c) delays
transonic wave drag rise, allowing higher cruise Mach or lower drag at the same
Mach. However, the internal volume available for integral fuel tanks decreases
with t/c, potentially requiring a larger wing area (and weight) to store the
needed fuel.

**Direction:**
- Lower t/c --> less wave drag (aero benefit) --> less fuel tank volume (design penalty)
- Higher t/c --> more fuel volume but earlier wave drag rise, possibly forcing lower cruise Mach

**MCP evaluation:**
- Aero side: su2-mcp captures the wave drag effect through RANS at transonic conditions.
- Fuel volume: NOT directly checkable (constraint C4). tigl-mcp can export wing cross-sections via `intersect_with_plane`, and the orchestrator could estimate volume, but no MCP automates the fuel-volume-ratio calculation.
- The orchestrator should treat this as a soft constraint and flag designs where t/c drops below ~10% at the root as high risk for fuel volume violation.

**Key insight:** The F25 uses a relatively thick wing (enabled by the high AR and moderate Mach 0.78) to maximize fuel volume. Pushing t/c down for drag reduction without checking fuel volume is a common pitfall.

---

## 6. High Aspect Ratio <--> Aeroelasticity

### Coupling: AR = 15.6 Creates Flutter and Divergence Risk

**Physical mechanism:** High aspect ratio wings are inherently more flexible. The
F25 AR of 15.6 is well above conventional transport aircraft (typically 9-12).
At this AR, the wing tip deflection under load can be several meters, and the
interaction between aerodynamic forces and structural deformation can trigger
flutter (dynamic instability) or static divergence.

**Direction:**
- Higher AR --> more flexible wing --> lower flutter speed --> potential flight envelope restriction
- Wing sweep partially mitigates flutter (moves the flutter boundary higher) but the F25 sweep of 25 deg is moderate

**MCP evaluation:**
- NOT checkable with current MCPs (constraint C10). Flutter analysis requires a coupled aeroelastic eigenvalue solver (e.g., NASTRAN + doublet lattice method). Neither su2-mcp nor mass-mcp provides this capability.
- mass-mcp with OAS captures static aeroelastic deflection (bend-twist coupling) but does NOT compute flutter speeds.

**Key insight:** This is the most dangerous unchecked constraint for the F25. Any optimization that increases AR beyond 15.6 or reduces wing stiffness (thinner skins, lighter materials) should be flagged with a high-priority aeroelasticity warning. The orchestrator should impose a conservative AR upper bound (e.g., 16.0) until aeroelastic analysis is available.

---

## 7. Span <--> Airport Compatibility

### Coupling: 45 m Span Exceeds 36 m Gate Limit Without Folding Wingtips

**Physical mechanism:** ICAO Aerodrome Reference Code C (the most common category
for single-aisle operations) limits wingspan to 36 m. The F25 baseline span of
45 m exceeds this by 9 m, placing it in Code D (up to 52 m) or requiring folding
wingtips for Code C gate compatibility (similar to the Boeing 777X).

**Direction:**
- Higher span --> better aero performance (lower induced drag) --> restricted airport access
- Folding wingtips add ~200-400 kg mass and mechanical complexity (not modeled in current MCPs)

**MCP evaluation:**
- Span is set directly via `Aircraft.Wing.SPAN` in aviary-mcp or indirectly through AR and area in tigl-mcp.
- Airport compatibility is NOT modeled by any MCP. This is a binary design decision that the orchestrator must enforce as a hard bound.
- If Code C compatibility is required: cap span at 36 m. If Code D is acceptable: cap at 52 m. If folding tips are assumed: cap the fixed portion at 36 m and add a mass penalty (~300 kg) manually.

**Key insight:** The span constraint fundamentally limits the achievable AR for a given wing area. At 130.1 m2 area with 36 m span, the maximum AR is only 10.0 — far below the F25 baseline of 15.6. The F25 design explicitly assumes Code D or folding-tip operations.

---

## Coupling Summary Matrix

| | Aero | Structures | Propulsion | Mission | Fuel Volume | Aeroelasticity | Airport |
|---|---|---|---|---|---|---|---|
| **Aero** | -- | AR/area vs mass | Engine size vs drag | -- | t/c vs wave drag | -- | -- |
| **Structures** | Wing mass vs L/D | -- | Snowball loop | -- | -- | Stiffness vs flutter | -- |
| **Propulsion** | Nacelle drag | Engine weight | -- | SFC bucket alignment | -- | -- | -- |
| **Mission** | Drag --> fuel | OEM --> fuel | SFC --> fuel | -- | Range vs volume | -- | -- |
| **Fuel Volume** | t/c tradeoff | -- | -- | Required volume | -- | -- | -- |
| **Aeroelasticity** | Load redistribution | Flexibility | -- | Flutter envelope | -- | -- | -- |
| **Airport** | -- | -- | -- | -- | -- | -- | Span cap |

Cells marked "--" indicate weak or negligible coupling for the F25 design space.
