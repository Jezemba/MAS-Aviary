# Best practices (read with the read_best_practices tool)

Distilled 2026-10-04 from the pre-v2 stage prompts: only points that are still true and useful.
Workarounds for bugs that have since been fixed are left out (for example meshing only Wing1, the
300 s SU2 budget, "the morph does not modify the CPACS"). Entries are dated and appended at the
bottom: the newest entries are last.

## general

- [2026-10-04] Session ids are tracked by the framework and filled into tool calls automatically. Do not track or pass them by hand unless a tool asks.
- [2026-10-04] Look up exact names with the discovery tools before using them: get_design_space (aviary), get_design_inputs (pycycle), get_valid_config_options (su2). A guessed name costs a failed call.
- [2026-10-04] Values coupled between disciplines are passed by the framework: geometry to aviary (wing area, aspect ratio, sweep, taper, fuselage), su2 CL and drag to aviary, wing mass to aviary, mass to pycycle Fn_DES, the mission to su2, mass and pycycle. Do not retype them into parameter dictionaries.
- [2026-10-04] read_design_json returns the current design as the framework captured it from tool results. Prefer it to values copied from earlier stages' text.

## geometry

- [2026-10-04] set_high_level_parameters inputs that reshape the geometry: wing (Wing1, Wing2H, Wing3V) area, aspect_ratio, sweep; fuselage length, diameter. span alone, root_chord, tip_chord, dihedral, twist and taper are not inputs; the tool reports ignored keys in warnings.
- [2026-10-04] Pass only the keys you want to change. Passing a key reshapes even at its current value: sweep sets every section's positioning angle, and this wing's inboard sections are at 0 deg.
- [2026-10-04] Change one or two wing keys per call; area, aspect_ratio and sweep together miss their targets. Read the achieved values from geometry_morph.after.
- [2026-10-04] get_wing_summary's reference_area and aspect_ratio are half-wing figures; full_reference_area and full_aspect_ratio are the full wing.
- [2026-10-04] generate_volume_mesh without component_uid meshes the whole aircraft. The framework sets the mesh sizes. export_component_mesh(format="su2") is surface-only and SU2 cannot solve it.
- [2026-10-04] After reshaping, call export_cpacs; the mass stage reads the exported file.

## aero

- [2026-10-04] Build the SU2 config with configure_from_cpacs once. Do not hand-write it: SU2 rejected 80 to 91 percent of hand-written configs (PHYSICAL_PROBLEM for SOLVER, GREEN-GAUSS, IMPLICIT, MARKER_BODY, MACH). Flight state, numerics and reference area/length are supplied by the framework.
- [2026-10-04] Use update_config_entries only for a deliberate change, and call get_valid_config_options first (for example JST requires MUSCL_FLOW="NO").
- [2026-10-04] Keep CFL at the pinned value and convergence on RMS_DENSITY to -6. CFL near 1e3 never converged; force-based (Cauchy) convergence was a wrong turn.
- [2026-10-04] The whole-aircraft Euler solve takes about 10 minutes.
- [2026-10-04] run_su2_solver returns final_coefficients and drag_assessment. Use drag_assessment.cd_reported: on this mesh the raw CD can fall below the induced drag of its lift, even below zero.
- [2026-10-04] Residuals are log10 values: more negative is better. Drop in orders = row 1 rms[Rho] minus the last row's rms[Rho] (row 0 is the pre-step state). From -10 to -2 is divergence, not an 8-order drop.
- [2026-10-04] sample_surface_solution needs marker_name="aircraft".

## structures

- [2026-10-04] Fixed for the study: wing_mass_method "flops", material "aluminum", design_load_factor 2.5. Pass them exactly; the mass fidelity is held constant across combinations.
- [2026-10-04] To reduce wing mass, change the design (aspect ratio, span, taper), not the method or material.
- [2026-10-04] estimate_mass reports mTOM_kg (the gross mass it started from) and mTOM_sized_kg, which is a result only when sizing_converged is true; it reports the mission it sized at in sizing_conditions.
- [2026-10-04] An OEM above 50,000 kg is a structural concern for this mission.

## propulsion

- [2026-10-04] Input names: splitter.BPR, fan.PR, lpc.PR, hpc.PR, T4_MAX, Fn_DES, fc.alt, fc.MN. There is no top-level BPR or OPR input (OPR = fan.PR x lpc.PR x hpc.PR). get_design_inputs lists them with current values.
- [2026-10-04] list_variables is very large (about 30K tokens) and omits the promoted names (Fn_DES, T4_MAX); prefer get_design_inputs.
- [2026-10-04] Output names: perf.TSFC and perf.Fn (not SFC or Fn). Ask run_cycle for those names in outputs_of_interest.
- [2026-10-04] Fn_DES, fc.alt and fc.MN are set by the framework (from the mass estimate and the mission).
- [2026-10-04] run_cycle reports converged; an unconverged cycle is not a result. Measured window: splitter.BPR up to about 10, fan.PR 1.4 to 1.7, T4_MAX 2857 degR and above.
- [2026-10-04] pycycle computes no engine mass.

## mission

- [2026-10-04] Call get_design_space first for the exact parameter names.
- [2026-10-04] Do not call create_session: the aviary session already exists with the mission, and the framework refuses a new one.
- [2026-10-04] Aircraft.Wing.ASPECT_RATIO above about 12 makes the bench aircraft's tables fail (AVIARY_SETUP_ERROR); reliable range 9 to 11.5.
- [2026-10-04] Do not set Aircraft.Wing.SPAN: it is derived from area and aspect ratio and read-only.
- [2026-10-04] set_aircraft_parameters validates inline (valid flag). If valid is false, report it; repeating the same values does not fix them.
- [2026-10-04] An UNCOUPLED_MISSION or missing-coupled-input refusal means upstream data is missing; do not fly the mission on default drag.
- [2026-10-04] If the climb does not converge on a heavy design, a low Aircraft.Engine.SCALE_FACTOR is a likely cause.

## simulation

- [2026-10-04] Call run_simulation once. FUEL_BURNED_KG comes from run_simulation and get_results; the fuel in a set_aircraft_parameters response is a validation evaluation and is always 0.0.
- [2026-10-04] AVIARY_SETUP_ERROR is permanent for the parameter set: report it, do not retry, and skip get_results and get_trajectory (they return NO_RESULTS).
- [2026-10-04] exit_code 0 = success, 9 = iteration limit. Do not present unconverged numbers as reliable.

## integrator

- [2026-10-04] check_constraints takes the exact names from get_results, e.g. fuel_burned_kg and gtow_kg.
- [2026-10-04] DLR-F25 reference: MTOM 85,700 kg, OEM 46,300 kg, design-range fuel 12,100 kg, span 45.0 m, AR 15.6, wing area 130.1 m2, cruise CL 0.593, cruise L/D 19.5.
- [2026-10-04] Constraint ids: C1 range >= 2500 nmi; C2 TOFL <= 2200 m; C3 Vref <= 136 KCAS; C4 wing fuel volume ratio >= 1.1 (no tool checks it); C5 2nd-segment climb >= 2.4 %; C7 rear spar height >= 0.15 m; C8 no flow separation; C9 buckling margin >= 1.0; C10 buffet and flutter (no tool checks it).
- [2026-10-04] Typical conflicts: aero vs structures (more area or aspect ratio lowers drag but adds wing mass); propulsion vs weight (a smaller engine saves mass but risks the climb); the snowball (higher OEM, higher MTOM, more thrust, heavier engine).
- [2026-10-04] RECOMMENDED_CHANGE should name an input the next iteration can set (a tigl or pycycle input), not a value the framework derives (AREA, SWEEP, TAPER, fuselage height and width follow the geometry).
