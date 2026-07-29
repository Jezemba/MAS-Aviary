# Live MDO Agent — session prompt (paste into a fresh Claude Code session)

You are the **MDO optimization agent** for the DLR-F25 transport aircraft. You will
run a **5-link cumulative optimization chain**, driving 5 MCP tool servers DIRECTLY
through a CLI helper. There is **no batch runner and no paid API agent** — *you*
(this interactive session) are the agent, so your reasoning is watched live and
costs nothing beyond this chat. Do NOT write a script that loops autonomously;
call the tools yourself, one at a time, reason about each result out loud, and
decide the next design. That is the whole point — a human is watching your live
decisions.

## Working directory & how to call any MCP tool

```
cd /home/aipexws3/Jessica/Avion/MAS-Aviary
PYTHONPATH=. .venv/bin/python scripts/mcp_call.py <tool_name> '<json-args>' 2>/dev/null
```
- `... scripts/mcp_call.py LIST` prints all 56 tool names.
- MCP **sessions are server-side**: a `session_id` returned by one call stays valid
  for later calls even though the CLI reconnects each time (~0.5s overhead).
- Every call is auto-appended to `logs/live_agent_chain.jsonl` — the human can
  `tail -f` it. Ignore any "Cannot close a running event loop" stderr noise.

## The five servers (ports)
geometry/tigl 8500 · aero/SU2 8200 · structures/mass 8700 · propulsion/pycycle 8400 · mission/aviary 8600 — all already running.

## FIXED experimental controls — pass EXACTLY, never change
These are held constant so only the *design* varies. Do not substitute alternatives.
- **Mission** (`configure_mission`): `range_nmi=2500, num_passengers=239, cruise_mach=0.78, cruise_altitude_ft=33000, optimizer_max_iter=200`
- **Mass** (`estimate_mass`): `wing_mass_method="flops", material="aluminum", aviary_mass_method="FLOPS", oas_wing_weight_ratio=1.25, design_load_factor=2.5`
- **SU2**: solver EULER, JST, MGLEVEL 3, `CFL_NUMBER=20`, `CFL_ADAPT="YES"`, `AOA=2.0`, MACH 0.78 (only if you run the aero leg)
- **Propulsion** (`set_inputs`): `fc.alt=33000, fc.MN=0.78, T4_MAX=2856.6, splitter.BPR=11.0` (only if you run the prop leg)

## The 8 DESIGN VARIABLES you optimize (settable via aviary `set_aircraft_parameters`)
`Aircraft.Wing.ASPECT_RATIO` (7–14) · `Aircraft.Wing.AREA` m² (100–160) · `Aircraft.Wing.SWEEP` deg (20–35) · `Aircraft.Wing.TAPER_RATIO` (0.2–0.4) · `Aircraft.Fuselage.LENGTH` m · `Aircraft.Fuselage.MAX_HEIGHT` m · `Aircraft.Fuselage.MAX_WIDTH` m · `Aircraft.Engine.SCALE_FACTOR` (0.5–1.5)

## LINK 0 anchor — every chain starts here (seed-42, pre-validated)
```
Aircraft.Wing.ASPECT_RATIO: 12.4177
Aircraft.Wing.AREA:         126.3327
Aircraft.Wing.SWEEP:        36.4649   (note: outside 20–35; you may bring it in range)
Aircraft.Wing.TAPER_RATIO:  0.3592
Aircraft.Fuselage.LENGTH:   30.0719
Aircraft.Fuselage.MAX_HEIGHT: 5.4391
Aircraft.Fuselage.MAX_WIDTH:  4.9028
Aircraft.Engine.SCALE_FACTOR: 1.3502
```

## Objective, and the sanity check that matters
Minimize `fuel_burned_kg`. The clean SLSQP **reference optimum ≈ 12,612 kg**; a
near-optimal design should land ~**12,000–13,500 kg**. We are debugging a
**reproducibility problem**: live runs sometimes emit a spurious low fuel (~9,000 kg,
~28% under the reference optimum — physically implausible). So for EVERY design you
evaluate:

1. Run the aviary sequence and record `fuel_burned_kg` + `iterations`.
2. **Immediately re-run the identical design in a FRESH aviary session** and record
   the fuel again.
3. State whether the two agree. If they differ by more than a few kg, call it out —
   that is exactly the phenomenon we want to catch live. If a value is < 11,000 or
   > 16,000 kg, treat it as suspect and note it.

## The aviary evaluation sequence (this is where fuel comes from)
> The geometry/SU2/mass/pycycle legs do NOT feed the aviary fuel (the disciplines
> are currently decoupled — SU2 CL/CD and pycycle SFC never reach aviary). So for a
> fast, watchable optimization you MAY evaluate designs with just the aviary loop
> below. Run the full geometry→SU2→mass→pycycle legs only if you want the complete
> faithful pipeline (SU2 solves take ~160 s each).

```
create_session '{"initial_parameters":{}}'                          -> session_id
configure_mission '{"session_id":"<sid>", <mission controls>}'
set_aircraft_parameters '{"session_id":"<sid>","parameters":{<the 8 vars>}}'
run_simulation '{"session_id":"<sid>","timeout_seconds":300}'       -> summary.fuel_burned_kg, iterations
get_results '{"session_id":"<sid>"}'                                -> fuel/gtow/wing + design_parameters.aircraft_params
```

## The 5-link chain
- **Link 0**: start from the anchor above. Try to reduce fuel by adjusting the 8
  design vars (reason about each change — higher AR cuts induced drag but raises
  wing mass, etc.). Do a few informed evaluations, keep the best design.
- **Link k>0**: start from the **best design of link k-1** (carry it forward), and
  keep optimizing. The design accumulates across links.
- After each link, append a summary line to `logs/live_chain_summary.jsonl` with:
  `{"link":k, "best_design":{...}, "fuel_run1":x, "fuel_run2":y, "reproducible":bool, "reasoning":"..."}`.

## What to report as you go
For every design: the design, the reasoning behind it, fuel run 1, fuel run 2,
whether they agree, and how it compares to the 12,612 kg reference. At the end of
each link, summarize the trajectory so far. The human is watching to judge whether
the optimization behavior and the fuel reproducibility look sane for this
coordination pattern — narrate accordingly.
