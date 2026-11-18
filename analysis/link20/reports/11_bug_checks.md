# 11. Bug checks on the 20-iteration re-run

Answers to the writer's "are these bugs?" prompt for the re-run in `10_rerun_results.md` (ws8 `/data/Jessica/l20_final/`).
"Iteration" k = folder `repeat_<k-1>`. All checks were read-only on the run data. Code was read at the commit each
combination ran (MAS-Aviary aeb67dd for S_GR, O_SP, O_IF, O_GR; b985d28 for S_SP, S_IF, N_IF, N_GR). Scripts and their
outputs are in ws8 `/data/Jessica/l20_bugchecks/case{1,3,4,5,6}/`. "KB n" = knowledge-base sequence number; "row N" =
tool_calls row.

Re-flights: 8 missions on a private aviary-mcp (ws8 port 13623, the study's aviary code, stopped afterwards), for
Case 5 only. Cases 1 and 2 needed none, because the run itself shows the mission is deterministic: identical inputs give
identical fuel to 1e-12 kg (O_SP 6 and 19; O_SP 2, 5, 8, 9, 11, 13, 16, 17, 18, 20 all 23,006.371383 kg).

## Headline

1. **The three iterations that meet both limits (O_SP 6, O_SP 19, O_IF 4) are all a framework bug** (Cases 1-2). SU2
   solved them at a fixed 2 deg angle of attack instead of trimming to cruise lift. The resulting CD (0.0053 / 0.0044) is
   below the induced drag of that lift, so L/D came out about 19 instead of about 13 and the engine was sized too small.
   Without these and the nine known-flawed iterations, **no iteration meets the fuel limit**, and the study minimum
   becomes N_GR 12 at 15,255.1 kg (not checked for this bug).
2. **The nine known-flawed iterations are confirmed**, and none was missed. Re-flights show some reported changes come
   entirely from the bug, e.g. N_IF 17 (16,577.5 kg flown, about 29,809 kg with its own values). One more partial case
   was found (N_GR 19, engine scale 0.6 % stale).
3. **The coupled aero/drag/engine values are not computed the same way in every iteration** (Case 3). The causes are a
   4.0 m default chord, lift targets typed by agents, and a framework step that creates SU2 sessions before the carried
   mass is loaded. As a result the same design can give different fuel, by up to 9.5 t in O_GR.
4. **O_SP, O_IF and N_IF flew a 27.33 deg wing from iteration 1 on** (Case 4). The cause is a wrong default in the
   prompt design table, so these three did not start from the same design as the baseline and the other five.
5. **Case 6:** three recording/config bugs that change coordination metrics only (no fuel numbers).


## Case 1: O_SP iterations 6 and 19 (14,812.1 kg) against the same design at ~23,006 kg

### Verdict: **bug** (framework coupling, not agent design and not mission non-determinism)

In iterations 6 and 19 the geometry agent closed the TiGL session before anyone had measured the wing. Because of that,
the data plane could not compute a cruise-lift target for SU2. SU2 then solved at the fixed default AoA of 2 deg, with no
aspect ratio and the CPACS-declared reference area. The result was CL 0.4610 and CD 0.00532 (Euler). That CD is below the
induced drag of its own lift. Every coupled input of the mission comes from that solve: the drag factor, the design CL,
Fn_DES and therefore the engine scale. Mass, geometry, the engine cycle, the deck and the mission settings are identical to
iteration 5. The 14,812.1 kg is the fuel of an aircraft flown on lift and drag that SU2 was never asked to trim to.

The mission is deterministic. Identical inputs always gave identical fuel to the last digit: 14,812.082769733986 kg in
iterations 6 and 19, and 23,006.371383044272 kg in iterations 2, 5, 8, 9, 11, 13, 16, 17, 18 and 20. No re-fly was needed,
so none was done, and no aviary-mcp server was started.

### 1. Complete input to the counted `run_simulation`, field by field

Sources: `knowledge_base.jsonl` (KB) of each `repeat_<k-1>/mdo_f25_orchestrated_staged_pipeline`. The counted attempt is
attempt 1, except iteration 19, where it is attempt 2. The aviary parameters are the `design_parameters.aircraft_params`
that `get_results` returned right after the counted run. The engine fields come from the `run_simulation` output.

| Field | it 5 (`repeat_004`) | **it 6 (`repeat_005`)** | it 7 (`repeat_006`) | **it 19 (`repeat_018`, attempt 2)** |
|---|---|---|---|---|
| counted run_simulation (KB seq) | 30 | 25 | 32 | 26 |
| Aircraft.Wing.AREA / ASPECT_RATIO / SWEEP / TAPER_RATIO | 122.788 / 9.3693 / 27.3267 / 0.2462 | same | same | same |
| Aircraft.Fuselage.LENGTH / MAX_HEIGHT / MAX_WIDTH | 37.68 / 4.15 / 3.9607 | same | same | same |
| Aircraft.Wing.SPAN (derived) | 33.918101 | same | same | same |
| Aircraft.Wing.MASS (mass-mcp mWing) | 8717.790735927743 | same | same | same |
| mass-mcp mTOM_kg (used, sizing_converged=false) | 78126.0 | same | same | same |
| **Mission.Design.LIFT_COEFFICIENT** | **0.558735238** | **0.4609687869** | 0.558735238 | **0.4609687869** |
| **Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR** | **1.2861381636** | **0.8451864731** | 1.2822109891 | **0.8451864731** |
| **Aircraft.Engine.SCALE_FACTOR** | **1.448599730** | **0.978358293** | 1.444176486 | **0.978358293** |
| pyCycle set_inputs Fn_DES (lbf) | 8546.738409 (agent typed "17300") | 5772.313926 (agent typed "15000") | 8520.641265 | 5772.313926 |
| cycle: BPR / fan PR / LPC PR / HPC PR / T4_MAX | 5.105 / 1.685 / 1.935 / 9.369 / 2857 | same | same | same |
| engine deck (design_key, cached) | deck_7815057d6edc9732.deck, cached | same | same | same |
| deck reference_fn_des_lbf / SLS thrust | 5900 / 29,088.17 lbf | same | same | same |
| scaled SLS thrust / engine mass (run output) | 42,137.1 lbf / 5,173.0 kg | 28,458.7 lbf / 3,294.0 kg | 42,008.5 / 5,154.9 | 28,458.7 / 3,294.0 |
| mission_config | 2500 nmi, 239 pax, M0.78, 33,000 ft, optimizer_max_iter 200, takeoff_mass_kg 78126 | same | same | same |
| aviary session (new each iteration) | 7c6404f2… | a2b83db1… | c60ac26a… | 85ab7e75… |
| SLSQP iterations / runtime | 49 / 9.1 s | 11 / 4.3 s | 46 / 8.7 s | 11 / 4.65 s |
| **fuel / GTOW (kg)** | 23,006.37 / 100,202.36 | **14,812.08 / 88,021.20** | 22,914.07 / 100,071.69 | **14,812.08 / 88,021.20** |
| get_results cruise CL / CD avg | 0.6025 / 0.04453 | 0.5644 / 0.02937 | 0.6024 / 0.04438 | 0.5644 / 0.02937 |

The deck file's own hash was **not checked**: the decks live on ws6 (`/data/aipexws6_jessica/slot0/decks`), which is not
copied to ws8. The `design_key` (7815057d6edc9732) and the cycle inputs are identical in all four iterations, and
`generate_engine_deck` reported `cached: true` each time.

**Only three mission inputs differ: CL, drag factor and engine scale.** All three come from one upstream difference, the
SU2 result.

### 2. Who set the differing values, and why the SU2 result differs on the same geometry

#### The SU2 solve (the root difference)

| | it 5, KB seq 10/12 | it 6, KB seq 6/8 | it 19 (att. 2), KB seq 6/8 |
|---|---|---|---|
| wing measured before SU2? | yes: geometry agent `get_high_level_parameters` Wing1, seq 3 | **no** | **no** |
| TiGL session at SU2 creation | closed at seq 9, but the wing was already captured | **closed at seq 4 by the geometry agent, never measured** | **closed at seq 4, never measured** |
| aero agent's own `get_high_level_parameters` | not needed | seq 5 **failed**: "Unknown session_id '02fb01f5…'" | seq 5 **failed**: "Unknown session_id 'b3b97949…'" |
| `target_cl` typed by agent → executed | `null` → **0.5591901447** (framework) | `null` → **absent** | `null` → **absent** |
| `aspect_ratio` executed | 9.369319 (framework) | **absent** | **absent** |
| `ref_area` executed (SU2 REF_AREA) | 122.78803 (framework, "computed_geometry") | **122.4 ("cpacs_declared")** | **122.4** |
| SU2 mode | fixed CL (FIXED_CL_MODE, target 0.55919) | **fixed AOA 2.0** (override in every session) | **fixed AOA 2.0** |
| SU2 rows / runtime | 171 / 187 s | 68 / 78 s | 68 / 78 s |
| CL / CD | 0.558735238 / 0.0249581227 | **0.4609687869 / 0.005323192215** | **0.4609687869 / 0.005323192215** |
| drag_assessment.source | `su2` (CD ≥ induced floor 0.01372) | **`su2_unchecked`**: "No aspect_ratio was given…floor was not applied" | **`su2_unchecked`** |

The "as typed / as executed" columns are in `step_log_attempt1.md`: Step 16 of `repeat_004` and Step 11 of `repeat_005`.
The agent typed `target_cl: null` in both iterations. In iteration 5 the framework filled in the target, AR and reference
area. In iteration 6 it filled in nothing.

The CD of iteration 6 is not physical as a cruise drag. The induced drag of CL 0.461 at AR 9.369 (Oswald factor 0.7732,
the value su2-mcp itself used in iteration 5) is 0.00934 (`recompute_coupling.out`). SU2 reported 0.00532. The B138 floor
would have corrected it, but it needs `aspect_ratio`, and the framework did not inject one.

**Code path (MAS-Aviary aeb67dd, `src/tools/data_plane.py`):**
- Lines 2591-2594: on `create_su2_session(cpacs_file_path=…)` the data plane calls `_inject_target_cl`.
- Lines 971-1023, `_inject_target_cl`: the mass falls back to the CPACS `designMasses/mTOM` (78,126 kg; aero runs before
  structures). The wing area must come from `data_store["geometry_wing"]` (line 996), or else from
  `_auto_measure_wing()` (line 997), or else from `design_params_applied` (line 1000). Iteration 6 had none of the three,
  so lines 1001-1004 log at INFO level "no target CL" and return. The solve keeps its configured AOA of 2 deg.
- Lines 1026-1052, `_auto_measure_wing` (B154): it calls `get_high_level_parameters` with `_design_state.sessions["tigl"]`
  (line 1037). `close_cpacs` does not remove that entry: only `close_cycle_model` (line 290-301) and a `NO_SESSION` error
  (line 1837-1856) drop a session. So the fallback can only call a dead session. The agent's identical call at seq 5 shows
  what the server answers ("Unknown session_id"). The console log
  (`/data/Jessica/l20_final/logs/l20_orch_staged_iter_20261110_175100.log`) has neither the B154 warning nor the B111 INFO
  line, because INFO is not printed. So the log cannot tell whether the auto-measure was attempted. Either way it could
  not succeed.
- Lines 2761-2763: `aspect_ratio` is injected only from `geometry_wing`, so it is absent too.
- Lines 2776-2793: `ref_area` is injected only from `geom.reference_area` or `geometry_wing`. Both were absent, so su2-mcp
  used the CPACS-declared 122.4 m^2.
- su2-mcp ff53ebd (the study's commit) `tools/config_tools.py:176-178` sets `FIXED_CL_MODE` only `if target_cl is not None`.
  `drag_model.py:46-55` reports `su2_unchecked` when there is no aspect ratio.
- MAS-Aviary aeb67dd `src/tools/aero_guard.py`, `nonphysical_reason`: the only guard before `run_simulation`. It refuses
  only CD ≤ 0 or CL outside [0.05, 1.0]. It does not check that the solve was trimmed (CL against the target) or that CD is
  at least the induced drag. Iterations 6 and 19 passed it.

#### The coupled values derived from that solve (all framework, not agent)

Recomputed with the aeb67dd transforms in `coupling_aeb67dd.py` (`recompute_coupling.py` → `recompute_coupling.out`). Every
recomputed value reproduces the recorded one to all printed digits.

- **Fn_DES** (`data_plane.py:3025-3084`, `_inject_phase_k_b_fn_des`; `coupling.py:204-208`
  `fn_des_from_drag`): Fn_DES = MTOM·g/(L/D)/2/4.448·1.25 with L/D = CL/(CD_SU2 + friction).
  - Iteration 5: CD_total 0.02496 + 0.019385 = 0.04434, L/D 12.600, Fn_DES 8546.74 lbf.
  - Iteration 6: CD_total 0.00532 + 0.019385 = 0.02471, L/D 18.656, Fn_DES 5772.31 lbf.
  - The framework overrode the agent's typed Fn_DES ("17300" in iteration 5, "15000" in iteration 6): `set_inputs` at KB
    seq 21 (iteration 5) and seq 16 (iteration 6), `framework_applied.overridden.Fn_DES`.
- **Engine scale** (`data_plane.py:3553-3599`, `_inject_engine_scale`; `coupling.py:211-217`): scale = pyCycle Fn /
  deck `reference_fn_des_lbf` (5900, line 3583-3585).
  - Iteration 6: 5772.31/5900 = **0.978358**. Iteration 5: 8546.74/5900 = 1.448600.
  - In iteration 6 the scale is injected twice. KB seq 20/21 gives 0.97242: the bench reference of 5936 lbf, used before
    the deck was attached. KB seq 24 (`framework_applied.overridden.SCALE_FACTOR`) gives 0.97836, after `set_engine_deck`
    at seq 23.
- **Drag factor** (`data_plane.py:3412-3529`, `_inject_phase_h_aero`; `coupling.py:155-175`): factor = CD_total / (0.022 +
  CL²/(π·AR_eff·0.85)).
  - Iteration 5: 0.04434/0.03448 = 1.2861. Iteration 6: 0.02471/0.02923 = 0.8452.
- **Design CL**: injected straight from SU2 (line 3505-3509): 0.4610 against 0.5587.

#### Is 0.978 consistent with its inputs, or stale or mismatched? (question 4)

It is consistent. The thrust and the reference both belong to iteration 6: the thrust is pyCycle's converged Fn of 5772.31
lbf (run_cycle, seq 17), and the reference is the 5900 lbf of the attached deck 7815057d6edc9732. There is no mixing of
designs or decks. The value is wrong only because the L/D it was sized on is wrong: 18.66 from the untrimmed solve, against
12.60 from the trimmed one. The cycle contributes nothing to Fn_DES, which depends only on MTOM, CL and CD.

### 3. Determinism (question 3)

The inputs differ, so the re-fly in question 3 was not required, and none was done. The run itself shows that the mission
is deterministic. Iterations 6 and 19 have identical inputs and identical fuel to 1e-12. The ten iterations flying the
iteration-5 input set (2, 5, 8, 9, 11, 13, 16, 17, 18, 20) all give 23,006.371383044272 kg. Every iteration used a new
aviary session (different session ids), so nothing was warm-started across iterations.

### 4. Iteration 19 against iteration 6, and why the chain returns to ~23,006 kg (question 5)

Iteration 19 is an exact repeat of iteration 6:
- the same carried geometry, measured identically;
- the same agent sequence (geometry: open, mesh, close; aero: failed `get_high_level_parameters`, then `create_su2_session`
  with nothing injected);
- bit-identical SU2 CL/CD, Fn_DES, deck, scale, CL, drag factor and fuel.

Its attempt 1 had a trimmed session: KB attempt 1, seq 4 measured the wing and seq 5 created the session with
`target_cl` 0.55919. But the aero agent never ran SU2, so `run_simulation` was refused with MISSING_REQUIRED_PARAMETERS
(attempt 1, seq 22 and seq 24). The retry (attempt 2) took the untrimmed path.

The chain returns to ~23,006 kg because nothing from iteration 6 persists that matters. Every iteration re-solves SU2 and
re-derives Fn_DES, the scale, CL and the drag factor. The carried `end_state` engine scale (0.978, the iteration 7
`link_start`) is overwritten by injection. In iteration 7 the geometry agent measured the wing with `get_wing_summary`
(seq 4-6) before closing TiGL, so the target CL was injected again.

### Numbers this changes

Replacements below use the fuel of the identical trimmed input set (iteration 5: 23,006.37 kg, GTOW 100,202.36 kg)
(`impact.py` → `impact.out`).

- **Table 2, O_SP row:**
  - Mean 22,167.0 → 22,986.5 if iterations 6 and 19 are replaced, or 22,984.2 (n = 18) if they are dropped.
  - SD 2,515.8 → 46.5 (replaced) or 48.7 (dropped).
  - Minimum 14,812.1 (iteration 6) → 22,914.1.
  - Median is unchanged at 23,006.4.
  - O_SP then shows essentially no variation: its 20 iterations span 22,914.1 to 23,086.0 kg.
- **GTOW table:** the O_SP minimum 88,021.2 kg (iteration 6) → 100,071.7 kg. The O_SP mean 98,951.6 → 100,169.7 kg.
  - "Lowest GTOW of any flown iteration: O_SP iteration 6" (report lines 298 and 304) is wrong.
- **The three iterations that meet both limits** (report lines 361-366 and 376-379): O_SP 6 and O_SP 19 are this bug, and
  O_IF 4 is the same bug (see `case_2.md`).
  - After excluding these 3 and the 9 known-flawed iterations, **no iteration meets the fuel limit (≤ 15,000 kg) and none
    meets both limits**.
  - The lowest remaining fuel and GTOW is N_GR 12 (15,255.1 kg / 92,345.6 kg). N_GR 12 itself was not checked here.
- **The O_SP trend line (report lines 318-325):** the mean difference and the slope include iterations 6 and 19. **Not
  recomputed.**
- **Appendix E rows** for O_SP 6 and 19 should be marked flawed.

### Fix

- Do not let the target CL depend on an agent having measured the wing. In `_inject_target_cl` /
  `_auto_measure_wing`, read the area from the CPACS file being solved (`resolved["cpacs_file_path"]`, which the data plane
  already parses for mTOM) or from the carried `end_state` area, rather than calling a TiGL session that may be closed.
- Drop `sessions["tigl"]` on `close_cpacs`.
- Make `aero_guard` refuse `run_simulation` in three cases:
  - the SU2 session had no target CL;
  - the drag source is `su2_unchecked`;
  - CD is below the induced drag of its CL.
- Re-fly O_SP 6 and 19 with a trimmed solve. The expected result is 23,006.4 kg, the same inputs as iteration 5.

### Possible bugs / surprises found along the way

1. **The drag factor depends on whether the agent typed the aspect ratio** (`data_plane.py:3475`). `ar =
   parameters.get("Aircraft.Wing.ASPECT_RATIO")` is read from the agent's `set_aircraft_parameters` call, not from the
   geometry. When AR is absent, `coupling.py:169` uses AR_eff 11.0. In iteration 6 this gives 0.8452. With the measured
   AR 9.369 it would be 0.8103.
2. **The friction drag depends on which TiGL tool the agent used.** `get_wing_summary` stores `geom.mac_length` and the
   wetted area (`data_plane.py:1802-1807`); `get_high_level_parameters` does not. The friction CD actually used is
   0.019250 after `get_wing_summary` (iterations 1, 4, 7, 10, 12, 14, 15) and the nominal 0.019385 otherwise. That
   changes Fn_DES (8520.64 against 8546.74 lbf), the scale (1.44418 against 1.44860) and the drag factor (1.28221 against
   1.28614).
   - This is why the same O_SP design flies 22,914.1 kg in iterations 1, 7, 10, 12 and 15 and 23,006.4 kg in iterations
     2, 5, 8, 9, 11, 13, 16, 17, 18 and 20.
   - The remaining spread is the agent-rounded cycle (BPR 5.1 / fan 1.68 …): deck 687a53ed… in iterations 4 and 14
     (22,998.2 kg), deck 10242538… in iteration 3 (23,086.0 kg).
   - Relevant to Case 3.
3. Across all 8 combinations, the untrimmed signature that actually flew (`create_su2_session` without `target_cl` and
   `drag source su2_unchecked`) appears only in **O_SP 6, O_SP 19 and O_IF 4** (`scan_*.tsv`). The two N_IF rows the scan
   flagged (iterations 2 and 8) are false positives: their final solve went through `configure_from_cpacs` with
   `target_cl` injected.

Helper scripts and outputs are on ws8 in `/data/Jessica/l20_bugchecks/case1/`:
- `kb_timeline.py`, `kb_aero.py`, `kb_table.py` (per-iteration input table; `osp_table.tsv`, `oif_table.tsv`,
  `scan_*.tsv`);
- `recompute_coupling.py` with `coupling_aeb67dd.py` (`recompute_coupling.out`);
- `impact.py` (`impact.out`);
- `data_plane_aeb67dd.py` (`git show aeb67dd:src/tools/data_plane.py`).

## Case 2: O_IF iteration 4 (13,765.6 kg, the study minimum); engine scale 1.659 → 1.106 → 1.610

### Verdict: **bug** (the same root cause as Case 1)

The engine scale did not respond to the 0.3 % cycle edit. In aeb67dd, Fn_DES (and so the scale) does not depend on the
cycle inputs at all. It depends only on MTOM, the SU2 CL and the SU2 CD.

Iteration 4 is the only O_IF iteration flown on an untrimmed SU2 solve. The geometry agent closed the TiGL session before
anyone measured the wing (KB seq 4). The aero agent's own `get_high_level_parameters` then failed on the dead session
(seq 5: "Unknown session_id '6e2a4863…'"). `create_su2_session` (seq 6) therefore got no `target_cl`, no `aspect_ratio` and
no `ref_area`, and SU2 solved at fixed AOA 2.0 with REF_AREA 122.4. The result was CL 0.4627 and CD 0.004417
(`su2_unchecked`). That CD is below the induced drag of its own lift: 0.00882 at AR 10.0, using the Oswald factor 0.7732.

The 45 % scale jump is the L/D going from 12.96 (iteration 3) to 19.44 (iteration 4) and back to 13.23 (iteration 5).
The cycle edit has nothing to do with it. Neither the deck reference nor the sizing formula changed: the reference was
5900 lbf in all three iterations.

### 1. Complete input to the counted `run_simulation` (iterations 3, 4, 5)

Source: `repeat_00{2,3,4}/mdo_f25_orchestrated_iterative_feedback/knowledge_base.jsonl`. Each is attempt 1, with one
successful run_simulation. The aviary parameters are from `get_results.design_parameters` right after the run.

| Field | it 3 (`repeat_002`) | **it 4 (`repeat_003`)** | it 5 (`repeat_004`) |
|---|---|---|---|
| counted run_simulation (KB seq) | 34 | 28 | 35 |
| Wing AREA / AR / SWEEP / TAPER | 122.7878 / 9.9999 / 27.3353 / 0.2462 | same (no morph) | 122.788 / **10.5** / 27.3411 / 0.2462 (agent morph AR 10.5, seq 5) |
| Fuselage L / H / W | 37.68 / 4.15 / 3.9607 | same | same |
| Aircraft.Wing.MASS (mass-mcp) | 8917.324 | 8917.324 | 8930.832 |
| mass-mcp mTOM used (sizing_converged=true → mTOM_sized) | 92,054.72 | 92,054.72 | 91,117.56 |
| configure_mission takeoff_mass_kg | 92,054.7 | 92,054.7 | 91,117.6 |
| SU2 session: target_cl / aspect_ratio / ref_area | 0.55919 / 9.9999 / 122.788 (framework) | **none / none / none (CPACS 122.4)** | 0.55919 / 10.5 / 122.788 (framework) |
| SU2 CL / CD / source | 0.5589197456 / 0.02372737807 / su2 | **0.4627477946 / 0.004416876067 / su2_unchecked** | 0.5589220315 / 0.0228465198 / su2 |
| **Mission.Design.LIFT_COEFFICIENT** | 0.5589197456 | **0.4627477946** | 0.5589220315 |
| **SUBSONIC_DRAG_COEFF_FACTOR** | 1.2793523603 | **0.8126293057** | 1.2751426588 |
| pyCycle Fn_DES (framework) | 9787.758 lbf | **6526.763 lbf** | 9496.562 lbf |
| cycle (BPR / fan / LPC / HPC / T4) | carried (set_inputs sent only fc.alt, fc.MN, Fn_DES); deck key 7815057d6edc9732 | 5.105 / 1.685 / 1.935 / 9.369 / 2857, deck 7815057d6edc9732 | **5.1 / 1.68 / 1.93 / 9.37 / 2857**, deck **10242538c29c86b9** |
| deck reference_fn_des / SLS thrust | 5900 / 29,088.17 lbf | 5900 / 29,088.17 lbf | 5900 / 29,099.18 lbf |
| design TSFC of the deck | 0.643235 | 0.643235 | 0.644849 |
| **Aircraft.Engine.SCALE_FACTOR** | 1.658942 | **1.106231** | 1.609587 |
| engine mass (run output) | 6,045.9 kg | 3,793.8 kg | 5,842.0 kg |
| mission_config | 2500 nmi / 239 pax / M0.78 / 33,000 ft / max_iter 200 | same | same |
| SLSQP iterations / runtime | 33 / 7.4 s | 6 / 3.7 s | 226 / 31.4 s (converged True, exit 0) |
| **fuel / GTOW (kg)** | 22,368.96 / 101,582.69 | **13,765.61 / 88,226.41** | 21,811.65 / 100,613.63 |

Iterations 3 and 4 have identical geometry, mass, cycle and deck. Iteration 4 differs only in the three SU2-derived values
(CL, drag factor, engine scale). Iteration 5 differs from 3 in the AR morph (10.0 → 10.5), the slightly rounded cycle
(a new deck) and a new mass.

### 2. Who set each differing value

- **SU2 CL/CD (iteration 4, KB seq 8):** the untrimmed solve. The code path is the one described in `case_1.md` §2:
  - MAS-Aviary aeb67dd `src/tools/data_plane.py`: `_inject_target_cl` (lines 971-1023) and `_auto_measure_wing` (lines
    1026-1052), which reuses `sessions["tigl"]` that `close_cpacs` never clears.
  - The AR injection (lines 2761-2763) and the ref_area injection (lines 2776-2793) both need a measured wing.
  - su2-mcp ff53ebd `config_tools.py:176-178` sets FIXED_CL_MODE only with a target.
  - `aero_guard.py` does not refuse untrimmed or below-induced-drag aero.
- **Fn_DES:** framework, `_inject_phase_k_b_fn_des` (`data_plane.py:3025-3084`, `coupling.py:204-208`), set_inputs at KB
  seq 13 of iteration 4 (`framework_applied.overridden.Fn_DES`). It is MTOM·g/(CL/(CD+friction))/2/4.448·1.25, using the
  mass and the SU2 CL/CD. None of the cycle inputs enter it.
- **Engine scale:** framework, `_inject_engine_scale` (`data_plane.py:3553-3599`; `coupling.py:211-217`): pyCycle Fn /
  deck reference 5900.
  - Iteration 4, seq 20/24: 1.09952 (bench reference 5936, before the deck was attached).
  - Iteration 4, seq 27: 1.106231, after `set_engine_deck` at seq 26.
- **CL and drag factor:** framework, `_inject_phase_h_aero` (`data_plane.py:3412-3529`). The mission agent re-sent the same
  numbers (seq 20/24/27).

Recomputed with the aeb67dd transforms (`/data/Jessica/l20_bugchecks/case1/recompute_coupling.out`). Each value matches
the recorded one exactly.

| | MTOM (kg) | CL | CD_SU2 | friction used | L/D total | Fn_DES (lbf) | scale = Fn/5900 |
|---|---|---|---|---|---|---|---|
| it 3 | 92,054.72 | 0.55892 | 0.023727 | 0.019385 | 12.964 | 9787.758 | 1.658942 |
| it 4 | 92,054.72 | 0.46275 | 0.004417 | 0.019385 | **19.442** | **6526.763** | **1.106231** |
| it 5 | 91,117.56 | 0.55892 | 0.022847 | 0.019414 | 13.226 | 9496.562 | 1.609587 |

The iteration 5 friction is slightly different because its morph reported the MAC (3.96 m), which changes the Reynolds
number.

### 4. Is a 45 % jump from a 0.3 % cycle edit plausible?

No, and it is not what happened. The cycle edit (iteration 4 → 5: BPR 5.105 → 5.1, fan 1.685 → 1.68, LPC 1.935 → 1.93,
HPC 9.369 → 9.37) changed only:
- the deck (7815057d… → 10242538…);
- the design TSFC (0.643235 → 0.644849, +0.25 %);
- the SLS thrust at the 5900 lbf reference (29,088 → 29,099 lbf, +0.04 %).

Fn_DES and the deck reference (5900 lbf in all three iterations) did not change because of the cycle. The scale moved
because Fn_DES moved, and Fn_DES moved only because SU2's L/D did. Iteration 4 is the outlier: it went from 1.659 to
1.106 and back to 1.610. Iteration 3 → 5 (1.659 → 1.610, −3 %) is a normal change, driven by the AR morph to 10.5 and the
new mass.

### Numbers this changes

`impact.py` → `/data/Jessica/l20_bugchecks/case1/impact.out`. The replacement value is iteration 3, which has identical
inputs apart from the aero (22,368.96 kg, GTOW 101,582.69 kg). That is the expected value of a trimmed re-fly of iteration
4. It was **not re-flown**.

- **The study minimum 13,765.6 kg (O_IF 4) is a bug.** Excluding O_SP 6/19, O_IF 4 and the 9 known-flawed iterations, the
  lowest flown fuel is N_GR 12 at 15,255.1 kg. N_GR 12 was not checked by me. The "All" row minimum in Table 2 changes
  accordingly.
- **Table 2, O_IF row:**
  - Mean 20,654.0 → 21,106.8 if iteration 4 is replaced, or 21,036.7 (n = 18) if it is dropped.
  - SD 2,062.1 → 1,250.2 (replaced) or 1,247.4 (dropped).
  - Minimum 13,765.6 → 17,680.6 (iteration 12).
  - Median unchanged at 20,987.3.
- **O_IF GTOW:** the minimum 88,226.4 → 98,130.2 kg (iteration 12). The mean 99,111.0 → 99,814.0 kg.
- **Both-limits list** (report lines 361-366, 376): O_IF 4 must go. With O_SP 6 and 19 (Case 1), **0 valid iterations meet
  the fuel limit or both limits**.
- **The O_IF row of the iteration-1-to-best table** (report line 279: "13,765.6 (4), −2,624.9") and the O_IF trend
  statistics (lines 319-325) include iteration 4. **Not recomputed.**
- **Report line 376 and the snippet A-2 reading** ("engine scale 1.106; next iteration 1.61") should say that the scale
  change is the untrimmed aero of iteration 4, not a cycle effect.

### Fix

The same fix as Case 1:
- take the cruise-lift wing area from the CPACS being solved, not from a possibly closed TiGL session;
- clear `sessions["tigl"]` on `close_cpacs`;
- refuse `run_simulation` on untrimmed aero, on `su2_unchecked` drag, or on a CD below the induced drag;
- re-fly O_IF 4 with a trimmed solve. The expected result is about 22,369 kg.

### Possible bugs / surprises

1. **The O_IF target CL and the mass it flies are inconsistent.**
   - In O_IF, aero runs before structures. The SU2 target CL is therefore built from the CPACS mTOM of 78,126 kg (target
     0.55919), as in every iteration in the table.
   - But mass-mcp's sizing converged (`sizing_converged` true), so the framework uses `mTOM_sized` (92,054.7 kg in
     iterations 3-4) for Fn_DES and for `configure_mission takeoff_mass_kg` (`data_plane.py:640-642`).
   - The cruise CL for 92,054.7 kg on 122.79 m^2 is 0.6589 (`recompute_coupling.out`). So O_IF's trimmed solves are trimmed
     to the lift of a 78 t aircraft while the mission and engine use 92 t.
   - O_SP is consistent: its sizing never converged, so it uses 78,126 kg throughout.
   - This affects every O_IF iteration, and any combination whose sizing converges.
2. **The drag factor uses the aspect ratio the agent typed** (`data_plane.py:3475`), or AR_eff 11.0 when the agent typed
   none. In iteration 4 the factor is 0.8126 with AR_eff 11. With the measured AR 10.0 it would be 0.7929.
3. O_IF iteration 5's run_simulation reports 226 SLSQP iterations with `optimizer_max_iter` 200, but `converged` True and
   exit code 0. I did not check whether "iterations" counts function evaluations. It is probably harmless, but **not
   checked**.

## Case 3: same design gives a different result, or a different design gives the same result

Scripts and outputs (ws8): `/data/Jessica/l20_bugchecks/case3/`.
- `case3_scan.py` produces `case3_table.csv` (one row per combination and iteration), `case3_pairs.txt` (3.1) and `case3_consecutive.txt` (3.2).
- `case3_ogr_su2.py 13 20` produces `ogr_su2_it13-20.txt` (3.3).
- `case3_target_cl.py` produces `case3_target_cl.txt`, which shows who set SU2's lift target.
- `case3_fn_des.py` produces `case3_fn_des_pairs.txt` (engine thrust and scale for selected pairs).
- `df_check.py` recomputes the drag factor with MAS-Aviary's own `coupling.aero_cd_to_aviary_drag_factor` (b985d28 checkout, same code as aeb67dd).

All runs are read-only over `/data/Jessica/l20_final`. Code line numbers are from MAS-Aviary aeb67dd. `src/tools/data_plane.py` is byte-identical in b985d28.

### Verdict summary

| Sub-case | Verdict | Mechanism |
|---|---|---|
| 3.1 same design, different fuel | **bug** (three framework causes, plus agent-chosen SU2 lift targets) | (A) drag factor uses MAC = 4.0 m whenever no MAC was captured in that iteration; (B) SU2 lift target depends on call order and on whether the agent typed one; (C) engine scale divides by the bench reference 5,936 lbf instead of the deck's 5,900 lbf in some iterations |
| 3.2 consecutive identical fuel | **not a bug** (no flown change in any of them) | The carried design was re-flown. SU2 re-solves are deterministic (identical CL/CD to 10 digits). Every morph that succeeded was either a no-op or came after the counted flight. |
| 3.3 O_GR 17-20 CL alternation | **bug, but not B271** | New SU2 solves on the current mesh, trimmed to two different lift targets. The pre-init SU2 session is configured before the carried mass is seeded (graph_preinit.py:165 then :169), so the target falls back to the CPACS-declared 78,126 kg. A later `configure_from_cpacs` uses mass-mcp's sized 89,455.7 kg. |

### 3.1 Pairs with the same design inputs and |Δfuel| > 1 kg

**Design key:** what aviary flew, taken from `design_flown.json` `mission_applied`:
- Wing AREA, ASPECT_RATIO, SWEEP and TAPER, rounded to 3 dp.
- Fuselage length, rounded to 2 dp (from tigl when aviary was not re-sent it).
- The pyCycle cycle inputs T4_MAX, BPR, fan/lpc/hpc PR from `design_space.engine`.

127 of 154 flown iterations have a complete key. The other 27 lack engine inputs or wing keys in `design_flown.json`; these include S_GR 1, O_SP 6 and 19, O_IF 4, and N_GR 1-10.

The scan found **17 groups and 186 pairs**. All of them are listed with CL, CD, drag factor (DF), wing mass and engine scale in `case3_pairs.txt`. The "trim mass" column is CL·q·S/g at M0.78 / 33,000 ft, i.e. the mass the SU2 lift target corresponds to.

| Group (same design) | Iterations and fuel (kg) | What differs | Cause |
|---|---|---|---|
| O_SP / O_IF / N_IF baseline at 27.33° sweep (group 10, 105 pairs) | O_SP 1, 7, 10, 12, 15 and O_IF 1: **22,914.07**; O_SP 2, 5, 8, 9, 11, 13, 16, 17, 18, 20: **23,006.37**; N_IF 1, 4, 5: 22,916.0 / 22,914.07 / 23,002.96 | CL 0.558735, CD 0.0249581 and wing mass 8,717.79 are identical. DF is 1.2822110 vs 1.2861382. Scale is 1.444176 vs 1.448600 (Fn_DES 8,520.64 vs 8,546.74 lbf, ratio 1.0030628 = DF ratio). | **A**: every 22,914 iteration called `get_wing_summary` or morphed Wing1 (MAC 4.1903 m captured). Every 23,006 iteration did not, so MAC = 4.0. Recomputed: MAC 4.0 gives DF 1.28613816 and MAC 4.1903 gives 1.28221101. N_IF 5 vs O_SP 2 (23,002.96 vs 23,006.37, same Fn 8,546.74): **C**, scale ratio 1.0061017 = 5936/5900. |
| O_GR 1 vs 2-4 (group 1) | 21,698.59 vs 21,788.49 | DF 1.2213977 vs 1.2253688 | **A**: recomputed exactly (MAC 4.1925 vs 4.0). |
| S_SP 1 / S_IF 1 vs O_GR 1-4 (group 1, cross-combination) | 21,876.19 vs 21,698.59 / 21,788.49 | CL 0.55979 vs 0.55879, CD 0.023088 vs 0.022876 | **B**: the S_SP/S_IF agent typed `target_cl` 0.56; O_GR used the framework's W/(qS) of 0.559005. **A** on top of that. |
| S_SP 8 vs 9-11 vs 12 (group 2) | 20,971.57 / 20,999.66 / 20,910.28 | 8 vs 9: DF 1.2103757 vs 1.2116368. 12: CL 0.558751 / CD 0.021276 vs 0.559751 / 0.021487. | **A** (it8 morphed, so MAC was captured; 9-11 used 4.0, recomputed 1.21163677). **B**: in it12 the agent typed target_cl 0.559 instead of 0.56, giving a new SU2 answer for the same wing. |
| S_IF 4 vs 5-6 (group 3) | 21,009.66 vs 20,923.58 | DF 1.1964014 vs 1.1926348 | **A** |
| S_IF 18 vs 19-20 (group 5) | **19,806.93 vs 19,403.28** (404 kg) | DF 1.1665404 vs 1.1478720; scale 1.33136 vs 1.31006 | **A**: recomputed exactly. MAC 3.2954 m (morph) gives 1.16654040; default 4.0 gives 1.14787195. The default understates friction drag on this small-chord wing. |
| S_GR 3 vs 4 (group 6) | 22,106.24 vs 22,662.44 | DF 1.0805655 vs 1.0497679 | **A** (4.0 recomputes to 1.04977) |
| S_GR 3-4 vs 5-7 (group 6) | 22,106 / 22,662 vs 51,679-51,836 | CL 0.5576 vs 0.9131, CD 0.0139 vs 0.1266 | **B**: different SU2 lift targets for the same 75 m² wing (trim masses 47,581 vs 77,918 kg). |
| S_GR 5 vs 6-7 (group 6) | 51,678.84 vs 51,836.11 | Only the scale differs: 3.384058 vs 3.404707, same Fn 20,087.77 lbf, same deck `deck_7815057d6edc9732` | **C**: 20,087.77/5936 = 3.384058 and 20,087.77/5900 = 3.404707 |
| S_GR 9 vs 10, 11, 13 (group 7) | 47,589.08 vs 47,697.45 | Scale 2.990800 vs 3.009049, same Fn 17,753.39 | **C** (ratio 1.0061017) |
| S_GR 8 / 12 vs 9-13 (group 7) | 48,541.58 / 50,753.42 vs 47,589-47,697 | CL 0.9094 / 0.9890 vs 0.8545 | **B**: different lift targets |
| S_GR 15 vs 16-18 (group 8) | 49,517.52 vs 49,592.32 | Scale 2.664926 vs 2.681186, same Fn 15,819.00 | **C** |
| S_GR 14 vs 15-18 (group 8) | 49,247.96 vs 49,517-49,592 | CL 0.85089 vs 0.85431 | **B** |
| S_GR 19 vs 20 (group 9) | 35,734.56 vs 35,624.54 | Scale 2.326805 (= 13,811.91/5936) vs 2.323551 (= 13,708.95/5900) | **C**, and Fn differs |
| S_GR 2 vs S_IF 12 (group 4, 95 m², AR 12, cross-combination) | 32,169.47 vs 21,800.15 | CL 0.6486 vs 0.5588, CD 0.0373 vs 0.0168, DF 1.632 vs 1.160 | **B** (S_IF agent target 0.56) and **A** |
| O_IF 2 vs 3 (group 11) | 22,340.66 vs 22,368.96 | DF 1.2781083 vs 1.2793524 | **A** (it2 morphed, it3 did not) |
| O_GR 5 vs 6 (group 12) | 23,615.17 vs 20,758.12 | CL 0.5799 vs 0.5498 | O_GR 5 is B271 (known-flawed) |
| O_GR 8 vs 9-12 (group 13) | 38,977.81 vs 27,158.97 | CL 0.7294 vs 0.6239 | O_GR 8 is B271 (known-flawed) |
| O_GR 11 vs 9, 10, 12 (group 13) | 27,318.14 vs 27,158.97 | DF 1.4868717 vs 1.4807325; Fn_DES 10,621.92 vs 10,578.06 | **A** (it11 called `get_wing_summary` three times) |
| O_GR 13, 17, 19 vs 14-16, 18, 20 (group 14) | 32,944.88 / 32,659.68 vs 23,192.02 | CL 0.7262 / 0.7156 vs 0.6239 | **B**, see 3.3 |
| N_IF 7 vs 8 (group 15) | 26,847.62 vs 27,024.53 | DF 1.5238609 vs 1.5312988 | **A** (MAC from different capture calls) |
| N_IF 13 vs 14 (group 16) | 20,664.98 vs 20,687.98 | Scale **1.0000** vs 1.5597 (GTOW 94,386 vs 99,130) | N_IF 13 flew Fn = 5,900.0 lbf, the deck-generation reference (`run_cycle` #43 and #56). This is already B187-flagged in the report (contamination.csv). |
| N_GR 16 vs 17 (group 17) | 37,182.13 vs 37,099.67 | DF 1.9453961 vs 1.9422084 | **A**-type (different MAC from different morph calls) |

#### Root causes

**(A) Drag factor depends on whether a MAC was captured in that iteration.**
- `data_plane._friction_inputs`, `data_plane.py:3539`: `mac = coupling.get_var(_design_state, "geom.mac_length") or 4.0`. It feeds Reynolds into `aero_cd_to_aviary_drag_factor` (`data_plane.py:3477`).
- `geom.mac_length` is written only by `get_wing_summary` or a Wing1 morph (`_capture_geometry_ref`, `data_plane.py:1791-1831`).
- `geom.mac_length` is not carried between iterations: `graph_preinit.seed_from_design` seeds no `geom.*`, and the O_SP repeats show the default in every iteration without such a call.
- So an unchanged design gets a different DF, and through Fn_DES a different engine scale, depending on which read-only tool an agent happened to call.
- Size of the effect: 0.3 % DF and ~92 kg on the O_SP baseline; 1.6 % DF and 404 kg on S_IF's 105 m², AR-13 wing.
- **Fix:** carry `geom.mac_length` and the wetted areas in `design_flown.json` and seed them, or measure them in the run_simulation pre-hook. Never default to 4.0 m.

**(B) The SU2 lift target is not a framework value.**
- `data_plane._inject_target_cl` returns early when the caller passed `target_cl` (`data_plane.py:984`, "the caller pinned one; do not override").
- From raw model arguments in `step_log_attempt*.jsonl` (`case3_target_cl.txt`), agents typed `target_cl` in:
  - S_SP 19/22 calls (all 0.56)
  - S_IF 22/22 (all 0.56)
  - S_GR 18/27
  - O_GR 7/13
  - N_IF 11/90
  - N_GR 78/152
  - O_SP and O_IF 0 and 1
- The 0.56 comes from the prompt table row "Lift coefficient … framework: cruise lift W/(q·S) … ~0.56 … set by framework" (`sequential.md:56`). The table says the framework sets it; the code lets the agent win.
- Consequence: S_SP/S_IF solve every wing at CL ≈ 0.559 whatever its area. S_IF iterations 18-20 (105 m²) imply a trim mass of 66,829 kg, while W/(qS) at 78,126 kg would be ≈ 0.654.
- When the framework does set the target, the mass it uses depends on call order:
  - the CPACS-declared mTOM 78,126 kg (`data_plane.py:994`) when no mass-mcp result exists yet;
  - otherwise the sized MTOM (~89-91 t).
- Neither is the mission GTOW (95-117 t). Implied trim mass per combination is in `case3_trim_mass_summary.txt`:
  - O_SP 18/18 at 78,063 kg
  - S_IF 13/20 below 70 t
  - N_GR 11/20 at 88-92 t
- **Fix:** make `target_cl` framework-only, like the CL/DF injection (B143), with one defined mass basis.

**(C) Engine scale reference.**
- `_inject_engine_scale` (`data_plane.py:3584-3587`) divides pyCycle Fn by the deck's `reference_fn_des_lbf` (5,900) when `engine_deck_current(sid)` is true, otherwise by `BENCH_ENGINE_CRUISE_FN_LBF` 5,936 (coupling.py:194).
- In S_GR 5, 9, 15, 19 and N_IF 5 the flown scale equals Fn/5936 exactly. The paired iterations used Fn/5900. In both cases run_simulation reports the same external deck `deck_7815057d6edc9732` flown.
- The B187 re-apply (`_reapply_current_engine`, `data_plane.py:2114-2140`) did not correct it, because it only checks [cycle key, deck attached].
- Why `engine_deck_current` was false at injection time is not proven: the console logs have no B170 INFO lines.
- Effect: 0.61 % engine scale, 75-157 kg of fuel.
- This overlaps Case 1/2 (engine scale) and is worth handing to that owner.

### 3.2 Consecutive iterations with identical fuel (to 0.1 kg)

Full listing is in `case3_consecutive.txt`. In every pair the later iteration re-flew the carried design. All coupled values (CL, DF, wing mass, scale) are bit-identical.

| Pair | Fuel (kg) | Set/morph calls that succeeded in the later iteration | SU2 |
|---|---|---|---|
| S_SP 9→10→11 | 20,999.662 | No morph. One `set_inputs` (#16) re-sent the unchanged cycle (2857/5.105/1.685/1.935/9.369). | New solve each time (#11, 284-285 s), CL 0.5597506506 identical |
| S_IF 5→6 | 20,923.575 | No morph. `set_inputs` #18 unchanged cycle. | New solve, identical |
| S_IF 19→20 | 19,403.282 | No morph. `set_inputs` #15 unchanged. | New solve, identical |
| S_GR 6→7, 10→11, 16→17→18 | 51,836.109 / 47,697.452 / 49,592.323 | No morph. Pre-init `set_inputs` #8 (carried cycle); it17 #42 only Fn_DES/flight condition. | New solves identical. it17 also had a second solve #40 (CL 0.85091) that was not flown. |
| O_SP 8→9, 16→17→18 (the repeated 23,006.371) | 23,006.371 | No morph. `set_inputs` unchanged or Fn_DES only. | New solve (#12, 187 s) identical |
| O_IF 10→11 | 20,869.127 | Morph #3 Wing1 {AR 11.5, taper 0.28} **succeeded, but before = after** (AR already 11.4999); no geometry change | Identical |
| O_IF 15→16→17 | 20,987.260 | it16: morph #36 {AR 12.0001}, rebuilt, no change. it17: attempt-1 solve CL 0.6376 never flown; attempt 2 flew the carried CL 0.5577. | Identical |
| O_GR 2→3→4 | 21,788.494 | No morph. Pre-init `set_inputs` only. | Two new solves each, identical |
| O_GR 9→10 | 27,158.967 | No morph. Seven `set_inputs` (Fn_DES / lpt_PR; #53/#59 unchanged cycle). | Identical |
| O_GR 14→15→16 | 23,192.018 | No morph. Pre-init `set_inputs` (carried 6.5/1.8 cycle). | it15 new solve identical; it16 **no** solve (flew the seeded aero) |
| N_GR 2→3 | 19,712.795 | Morph #17 Wing1 area 110 changed the geometry, but **after** the counted run_simulation #14; later flights refused (#46 GEOMETRY_NOT_APPLIED, #48 MISSING_REQUIRED_PARAMETERS) | Solve #34 (110 m²) not flown. Iterations 2-3 are the B268 known-invalid ones. |

These identical fuels are real repeats of an unchanged design, not stale results. SU2, pyCycle and aviary are bitwise deterministic for identical inputs. The identical values show the *absence* of the (A)/(B)/(C) perturbations between those two iterations. Example: O_SP 16-18 all had no MAC capture.

### 3.3 O_GR iterations 17-20: CL 0.6238746855 vs 0.7156362038

Source: `ogr_su2_it13-20.txt`, `l20_orch_graph/repeat_016..019`.

**These are new SU2 solves, not reused results.** Each one has its own session and its own runtime:
- CL 0.6238746855 / CD 0.03199741782:
  - it17 #9 (269 s)
  - it18 #9 (277 s) and #29 (311 s)
  - it19 #9 (321 s)
  - it20 #9 (282 s)
  - also it14 and it15
- CL 0.7156362038 / CD 0.05747241378:
  - it17 #28 (261 s)
  - it19 #32 (261 s)

**The geometry and mesh are current, so this is not B271.**
- Every iteration from 14 to 20 opens the same carried `repeat_012/.../geometry_end.xml`.
- No morph happens in 14-20.
- The framework pre-init regenerates the mesh (#5 `generate_volume_mesh`) and creates the SU2 session on it (#6) every iteration.
- B271 is "SU2 solved the old mesh after a reshape without set_mesh" (O_GR 5, 7, 8). Here nothing is reshaped.

**The difference is the lift target the solve is trimmed to:**
- Pre-init `create_su2_session` (#6, agent "unknown"): `target_cl` **0.6242631287** = W/(qS) with **78,126 kg** (the CPACS fixture's declared mTOM) on 109.9886 m². The agent's solve #9 on this config gives CL 0.62387.
- In it17 #27 and it19 #31 the aerodynamics agent called `configure_from_cpacs` without typing a target (the agents' raw arguments contain no 0.7148). The data plane injected `target_cl` **0.7147926582** = W/(qS) with **89,455.7 kg**, mass-mcp's `mTOM_sized_kg` from `estimate_mass` #10. The re-solve gives CL 0.71564 and CD 0.05747, which was flown: 32,659.68 kg, scale 2.2310, DF 1.9872.
- Root cause:
  - `graph_preinit.prepare` calls `_open_sessions` (which runs `create_su2_session`, `graph_preinit.py:133`) at line 165, **before** `seed_from_design(previous)` at line 169 puts the carried `mass.mtom_kg` 89,455.7 into the registry (line 100-101).
  - So `_inject_target_cl` (`data_plane.py:988-995`) finds no mass and falls back to `_cpacs_design_mtom` = 78,126 kg.
  - Any re-configure after `estimate_mass` uses 89,455.7 kg.
  - Same design, two lift targets (0.624 vs 0.715), two drags (CD 0.0320 vs 0.0575), fuel 23,192 vs 32,660 kg.
  - The alternation follows whether the agent re-configured SU2 after structures: yes in 13, 17 and 19; no in 14-16, 18 and 20.
  - Neither mass is the flown GTOW (103,029 / 117,265 kg).
- **Fix:**
  - seed the carried results before opening the SU2 session in `graph_preinit.prepare`, or re-inject `target_cl` at `run_su2_solver`;
  - fix one mass basis for the trim;
  - include `target_cl` in the SU2 fingerprint. Currently `ALREADY_DONE` refuses a re-solve of the same geometry only within one config, as it18 #28 shows.

**Numbers it changes:**
- O_GR iterations 13 (32,944.9), 17 and 19 (32,659.7) vs 23,192.0.
- O_GR mean, std and spread, and any Results 4.1 statement that O_GR "oscillates" late in the chain. That oscillation is a framework artifact, not agent exploration. Agents changed nothing in 14-20; the cycle edits BPR 6.5 / fan 1.8 are from iteration 13.
- The same pre-init order applies to every graph-routed combination (S_GR, O_GR, N_GR).

### Which reported numbers Case 3 changes

- **O_SP:** the 22,914 / 23,006 split (O_SP iterations 1, 7, 10, 12, 15 vs 2, 5, 8, 9, 11, 13, 16, 17, 18, 20) is cause A, worth 92.3 kg (0.4 %). The "23,006 kg elsewhere" baseline in Case 1 is the MAC-default value of the design.
- **O_GR:** iterations 13, 17 and 19 (cause B / 3.3): +9,468-9,753 kg each.
- **S_IF:** 18 vs 19-20 (404 kg, cause A), and S_IF/S_SP's whole aero, which is trimmed to the agent's CL 0.56 (cause B). Smaller wings get drag at too low a CL, so S_IF's low-fuel designs (19,403 kg at 105 m², AR 13) are likely optimistic. Not quantified: that needs re-solves.
- **S_GR:** 5, 9, 15 and 19 (cause C, 75-157 kg each); S_GR 3 vs 4 (cause A, 556 kg).
- **Ranking of combinations:** combination means and the "best" values mix trim bases (agent 0.56, 78,126 kg, ~89-91 t sized MTOM). That is a cross-combination comparability issue for Table 2.

## Case 4: wing sweep 21.65 → 27.33 deg at iteration 1 in O_SP, O_IF and N_IF

Scripts and outputs (ws8): `/data/Jessica/l20_bugchecks/case4/`.
- `case4_sweep.py` lists every tigl `set_high_level_parameters` call carrying `sweep`, and the SWEEP sent to each counted run_simulation. Outputs: `case4_sweep_it1-2.txt`, `case4_sweep_all.txt`.
- `case4_positionings.py` produces `case4_positionings.txt` (CPACS positioning angles and mass-mcp's sweep input).

Code read:
- MAS-Aviary aeb67dd / b985d28, via `git show` on ws3. `data_plane.py` and `prompt_assembly.py` are identical in both.
- tigl-mcp 59f866b and mass-mcp 1f25297 (ws8 `/data/avion_study/code/...`). O_SP/O_IF ran on ws6, whose tigl-mcp checkout I did not read. Its recorded outputs (below) match the code read here.

### Verdict

**Bug, but not the one suspected.** Aviary flew the **measured** quarter-chord sweep, 27.3267°, of a wing that TiGL really sheared to that sweep. No code path writes a positioning angle into `Aircraft.Wing.SWEEP`.

The bug is upstream, in the prompts:
- The design table's "Default" column gives 27.33 / 32.21 / 40.40 for the tigl `sweep` key. These are the D150 CPACS **positioning sweepAngles**: 27.3288 / 32.2131 / 40.3982.
- The tigl `sweep` key is a **quarter-chord target**. The baseline measures 21.65 / 28.00 / 37.20 on that definition.
- The framework copies that column into the iteration-1 DESIGN_STATE as "the starting design".
- Agents that "apply the starting design" therefore morph the aircraft before the first evaluation: wing +5.68°, horizontal tail +4.21°, vertical tail +3.19°.

The tail changes have the same cause.

### 1. The call, and what TiGL measured

All three are **agent** calls, not framework calls, in iteration 1 (`repeat_000`):

| Combo | KB seq | Agent | Component, request | Measured c/4 before → after | LE sweep after |
|---|---|---|---|---|---|
| O_SP | 3 | geometry_engineer | Wing1 {area 122.78, AR 9.37, **sweep 27.33**} | 21.6502 → **27.3267** | 30.2908 |
| O_SP | 4 | geometry_engineer | Wing2H {area 31.1, AR 4.98, sweep 32.21} | 27.9999 → **32.2100** | 36.1574 |
| O_SP | 5 | geometry_engineer | Wing3V {area 21.5, AR 1.61, sweep 40.4} | 37.2000 → **40.3945** | 45.3138 |
| O_SP | 6 | geometry_engineer | Fuselage1 {length 37.68, diameter 4.15} | 37.6843 → 37.6800 m | — |
| O_IF | 2, 3, 4 (fuselage 5) | geometry_engineer | Same requests | Same values | Same |
| N_IF | 12, 13, 14 (fuselage 15) | agent_1 | Same requests | Same values | Same |

- Each response has `geometry_morph.rebuilt: true`, `targets: {sweep_quarter_chord_deg: 27.33}` and `targets_met: true` (O_SP KB seq 3).
- After the morph, Wing1's CPACS positionings are [0, 6.8559, 32.4788, 32.4788] deg, against the baseline's [0, 0, 27.3288, 27.3288] (`case4_positionings.txt`, `geometry_end.xml`). The morph sheared every section; it did not write 27.33 into a positioning.
- The area/AR/fuselage parts of the same calls are the table defaults rounded (122.83 → 122.79 m², 9.363 → 9.369, 37.684 → 37.680 m).

**Why these three combinations:**
- `prompt_assembly.starting_design_state` builds the iteration-1 text from the table's Default column (`_baseline_lines`, `src/coordination/prompt_assembly.py:402`, used at :468): "DESIGN_STATE (the starting design for this iteration): … Wing1: area 122.78, aspect_ratio 9.37, sweep 27.33 / Wing2H: … sweep 32.21 / Wing3V: … sweep 40.40".
- The test `tests/test_v2_prompts_networked_orchestrated.py:103` asserts exactly this text.
- This block reaches the orchestrated and networked step logs (O_SP 2×, O_IF 2×, O_GR 27×, N_IF 13× in `repeat_000/step_log_attempt1.md`), but not the sequential ones or N_GR.
- O_SP, O_IF and N_IF's geometry agents applied it as a set call.
- O_GR got the same text, but its geometry engineer re-set the measured 21.65015 (KB seq 27, no change).
- Later sweep moves are separate agent decisions: O_IF it10 #42 → 27.004; O_GR it7 #72 → 20.006; N_GR from it6 → 23-26°.
- Sweep then stays at 27.33 because the next iteration starts from the carried, measured geometry.

### 2. Does aviary get the c/4 sweep or the positioning angle?

**It gets the measured quarter-chord sweep.** Code path:

1. tigl-mcp `set_high_level_parameters` hands the wing `sweep` to `apply_wing_morph(target_sweep_deg=…)` (`tools/parameters.py:213-219`).
   - The morph shears the positionings (`morph.py:_shear_positionings`, lines 107-130) until the measured sweep meets the target (`_SWEEP_TOL_DEG` 0.01, `morph.py:42`; loop from `morph.py:239`).
   - It reports `after.sweep_deg = planform["sweep_quarter_chord_deg"]` (`morph.py:100`). That is the straight root-c/4 → tip-c/4 line from TiGL chord points (`planform.py:14-16, 81`).
   - `get_high_level_parameters` says the same in its own note: "sweep is the quarter-chord sweep, the same quantity set_high_level_parameters takes and reports" (`parameters.py:98`).
2. MAS-Aviary `data_plane._capture_geometry_wing` (`data_plane.py:1079`) takes `after` from the Wing1 morph and calls `_add_measured_planform` (`:1104`, defined `:1069-1076`). That sets `Aircraft.Wing.SWEEP = after["sweep_quarter_chord_deg"/"sweep_deg"]` = 27.32669.
3. `_apply_geometry_authority` (`data_plane.py:1287-1312`) overwrites every caller's wing keys in `set_aircraft_parameters` with that record. O_SP KB seq 23/27 report `geometry_authority.wing_from_geometry.Aircraft.Wing.SWEEP` 27.3267, and `design_flown.json` `mission_applied` has SWEEP 27.3267 next to `design_space.geometry.Wing1.sweep_set` 27.33 / `sweep_quarter_chord_deg` 27.32669.

So 27.33 is both the request (a positioning-angle number from the table) and the achieved c/4 sweep. The match is because the morph targets exactly what it is asked for, not because a positioning angle was copied into aviary.

Supporting evidence that the definitions differ and the repo knows it:
- `scripts/optimizer_baselines/design_space.py:36`: "not the table's 27.33 / 32.21 / 40.40, which are leading-edge values and would morph the aircraft before the first evaluation".
- `scripts/stat_batch_runner.py:124`: "the tigl positioning angle is 27.33".
- `tigl-mcp tools/mesh_deformation.py:74-84` calls the max positioning sweepAngle the "sweep setting".
- The comment at `data_plane.py:1097-1100` (B133: "the REQUESTED sweep is a tigl positioning angle") is stale. Since tigl-mcp B252 the request is a c/4 target.

**Tail sweeps:** same cause, same calls (KB seq 4-5 O_SP, 3-4 O_IF, 13-14 N_IF): Wing2H 28.00 → 32.21 and Wing3V 37.20 → 40.39 (measured c/4). They never reach aviary: `mission_applied` has no tail keys. mass-mcp takes non-wing geometry from aviary's bench CSV (`backends/aviary_backend.py:43`). So the tail change affects only the SU2 mesh.

### Root cause and fix

- **Root cause:** the Default column of the design-space table is CPACS positioning angles, while the column header says the value goes to tigl `set_high_level_parameters … key sweep`, which is a quarter-chord target. The rows are:
  - `sequential.md:28`
  - `orchestrated.md:34/410/786`, plus tails at `:37/:40`
  - `networked.md:33/413`
  - `config/mdo_f25_sequential_agents.yaml:35`
- `prompt_assembly._baseline_lines` (`:402`) turns that column into the iteration-1 DESIGN_STATE.
- **Fix:** set the defaults to the measured c/4 values, 21.65 / 28.00 / 37.20 (as `optimizer_baselines/design_space.py` already does). Optionally, have `_baseline_lines` build the starting design from `get_high_level_parameters` on the baseline CPACS instead of the prompt table.

### Which reported numbers it changes

- O_SP, O_IF and N_IF fly a different aircraft from the other five combinations from iteration 1 on: c/4 sweep 27.33° (O_IF 27.00-27.35°), tails +4.2/+3.2°. Every fuel, GTOW and wing mass in these three combinations is for that aircraft.
- They should not be compared with the 21.65° baseline (16,390.5 kg) or the other combinations as "same starting design".
- Indicative only (confounded, not a controlled re-fly):
  - O_SP/O_IF iteration 1 flew 22,914.07 kg with SU2 CD 0.024958 and DF 1.2822.
  - S_SP/S_IF iteration 1 flew 21,876.19 kg with CD 0.023088 and DF 1.2299, on essentially the same area and AR. The lift target also differs (Case 3 cause B).
- The report's sweep-move counts and "Possible bugs 3" (10_rerun_results.md lines ~473-493, 583-586) should say the move is a real agent-made geometry change triggered by a mislabeled prompt default, not a mapping error in aviary.

### Surprise found along the way: mass-mcp sizes the wing with a positioning-angle mean

mass-mcp's FLOPS wing mass does **not** use the quarter-chord sweep:
- `CPACSParser.get_wing_sweep` (`mass_mcp/cpacs/parser.py:146-159`) returns the mean of Wing1's **non-zero positioning sweepAngles**, while its docstring says "Quarter-chord sweep".
- That value goes to aviary's `aircraft:wing:sweep` (`backends/aviary_backend.py:67, 90`).

Recorded `validate_cpacs_inputs` outputs in `tool_calls_attempt1.jsonl` show `wing_sweep_25_deg`:
- 27.3288 for the unmorphed D150 (O_GR iteration 1). The true c/4 is 21.65.
- **23.9378** for the O_SP/O_IF iteration-1 wing (mean of 6.8559, 32.4788, 32.4788). The true c/4 is 27.33.

So the more-swept wing is sized at a *lower* sweep (23.94 vs 27.33). That is why O_SP's wing mass (8,717.79 kg) is below the unswept baseline's (8,735.33 kg).

This affects every wing mass in the study:
- The baseline is sized 5.7° too swept.
- Any sheared wing is sized with a sweep that moves the wrong way.
- Every morph that changes sweep shifts the inboard positioning from 0 to non-zero, which also changes how many angles enter the mean.

Fix in mass-mcp: measure the c/4 sweep from chord points (as tigl-mcp `planform.py` does), or have MAS-Aviary pass the measured sweep through.

## Case 5: the nine known-flawed iterations

**Verdict: bug, confirmed in all nine** (B270 x1, B271 x4, B272 x4). A scan of all 155 iterations finds **no missed
iteration** with any of the three signatures. It does find one more iteration, **N_GR 19**, where a refused
`set_aircraft_parameters` (the same mechanism as N_GR 6) left a stale engine scale: 2.8830 was flown and 2.9005 was
intended, 0.6 %. It also finds that **N_GR 6 flew a stale engine scale as well as the stale wing mass** (1.8787 flown,
1.5644 intended), which the B272 entry does not mention. Six re-flights and two control flights give corrected fuel for
N_GR 6, N_IF 11, 17 and 20. O_IF 13's corrected fuel is known without a re-flight. The four B271 iterations need a new
SU2 solve, so their corrected fuel was not estimated.

Notation: "row N" is the 0-based row of `tool_calls_attempt<k>.jsonl` (the `#N` used in BUGS.md and check_coupling).
"KB n" is the `seq` in `knowledge_base.jsonl`, matched to the row by tool name and time (to within 1 s, refused KB
entries skipped; `kbseq.py`). Iteration k = `repeat_<k-1>`. All files are under ws8
`/data/Jessica/l20_final/runs/<dataset>/repeat_<k-1>/<combo>/`. The counted mission is the `run_simulation` whose fuel
equals `links.csv fuel_kg`. Flown inputs are taken from the `get_results.design_parameters` that follows it (aviary-mcp
a5ef294 reports the inputs it flew with) or, where there is none (N_IF 17), from `carry_over.aviary_session_start` plus
the successful sets.

### 1. The nine iterations, value by value

| Iteration | Stale value that reached the mission | Correct value (from the same iteration's tools) | Where (row / KB) | Fuel flown | Fuel with the correct value |
|---|---|---|---|---|---|
| **O_IF 13** (B270) | engine deck `7815057d6edc9732` = baseline cycle BPR 5.105 / fan 1.685 / LPC 1.935 / HPC 9.369 (design TSFC 0.6432) | carried engine BPR 10 / fan 1.5 / LPC 1.8 / HPC 12 (`carry_over.previous_design.design_space.engine`), deck `53003f1683e9bdc9` (TSFC 0.5297) | get_design_inputs row 13 / KB 15; set_inputs row 14 / KB 16 sends only fc.alt, fc.MN, Fn_DES 9038.6; generate_engine_deck row 27 / KB 29 (`design_bpr 5.105`, cached); mission row 30 / KB 32 | 20,767.4 | **17,680.6** (not re-flown: O_IF 12's counted mission flew identical aviary inputs (wing 122.7878 / AR 11.4999 / sweep 27.3503, CL 0.5584281122, drag factor 1.2690105025, wing mass 8,981.58, scale 1.53196774129432, takeoff mass 89,737.4) with the carried-engine deck and burned 17,680.6) |
| **O_GR 5** (B271) | CL 0.5798872781 / CD 0.02747833 from solve row 51 / KB 54, on the **start mesh** (row 3 / KB 5, AR 9.363, area 122.829) | wing reshaped to AR 9.5009 (row 45 / KB 48); a mesh for it was made at row 48 / KB 51 but never loaded (no set_mesh; configure_from_cpacs row 50 / KB 53 changes only the references) | mission row 76 / KB 79 flew AR 9.5009 | 23,615.2 | not estimated (needs an SU2 solve on the AR 9.5 mesh) |
| **O_GR 7** (B271) | CL 0.5574667052 / CD 0.01646004 from solve row 74 / KB 79, on the mesh of the **115 m² / AR 10 / sweep 21.66** wing (made row 40 / KB 42, loaded row 42 / KB 45) | wing reshaped to 110 m² / AR 10.5 / sweep 20.0 (row 68 / KB 72); its mesh (row 71 / KB 75) was never loaded | mission row 95 / KB 101 flew 109.9999 / 10.5 / 20.0058 | 19,321.3 | not estimated |
| **O_GR 8** (B271) | CL 0.7294147421 / CD 0.06381026 from solve row 81 / KB 86, on the mesh of the **AR 10.5** wing (made row 45 / KB 47, loaded row 47 / KB 50) | AR 10.7 (row 76 / KB 80); its mesh (row 78 / KB 82) was never loaded | mission row 95 / KB 100 flew AR 10.701 | 38,977.8 | not estimated |
| **N_GR 11** (B271) | CL 0.4898504599 / CD 0.00960988 from solve row 129 / KB 127, on the mesh of the **155 m² / AR 13** wing (made row 31 / KB 33, loaded row 57 / KB 57) | 160 m² / AR 13 / sweep 25 (rows 109 / KB 108 and 118 / KB 116); its mesh (row 120 / KB 118) was never loaded | mission row 144 / KB 143 flew 160.0004 m² | 14,199.4 | not estimated |
| **N_GR 6** (B272) | wing mass 9,008.73 kg, the iteration-5 wing (carried); **also** engine scale 1.87868, iteration 5's value | mass-mcp 9,235.49 kg for the 130 m² / AR 11.5 wing (row 21 / KB 24); scale 1.56435 in the same refused call | set_aircraft_parameters row 25 / KB 29 refused as a whole (READ_ONLY_PARAMETER, Wing.SPAN); mission row 29 / KB 33 | 21,330.6 | **21,380.8** with the mass only; **20,963.2** with the mass and the refused scale |
| **N_IF 11** (B272 ext.) | **no SU2 aero at all**: Mission.Design.LIFT_COEFFICIENT and SUBSONIC_DRAG_COEFF_FACTOR absent, so aviary used its own drag polar | SU2 CL 0.5093219237, CD 0.01338087 (row 22 / KB 29) → drag factor 1.03101 (the value the framework recorded in design.json) | the only set_aircraft_parameters (row 21 / KB 26) ran **before** the solve; missions row 32 / KB 42 and row 36 / KB 48 (counted). Attempt 2 | 16,602.1 | **17,420.8** |
| **N_IF 17** (B272 ext.) | no SU2 aero (as N_IF 11) **and** wing mass 9,031.60 kg | SU2 CL 0.6344644999, CD 0.04234329 (rows 21 / KB 27, 26 / KB 32) → factor 1.69792; mass 9,006.76 kg (row 25 / KB 31, reshaped AR 10.5 wing) | the 9,031.60 estimate (row 5 / KB 7) read a copy of the **start file** (AR 11.0) 4 s after the reshape to AR 10.5 (row 4 / KB 6) and before any export, so it equals the carried iteration-16 mass. The set carrying the new values (row 27 / KB 33) was refused (Wing.SPAN). Missions row 37 / KB 48, row 39 / KB 50 (counted) | 16,577.5 | **29,808.9** |
| **N_IF 20** (B272 ext.) | no SU2 aero **and** wing mass 8,668.64 kg | SU2 CL 0.6678451169, CD 0.05153474 (row 20 / KB 26) → factor 1.87252; mass 8,836.15 kg (row 25 / KB 37) | the 8,668.64 estimate (row 4 / KB 6) read the **start file** (115 m²) 4 s after the reshape to 120 m² (row 3 / KB 5; export only at row 7 / KB 9) = the carried mass. The only set (row 14 / KB 17) ran before the solve and the new estimate. Mission row 32 / KB 46 | 18,958.6 | **34,929.8** |

**Physical check of the stale meshes (B271).** SU2's first-iteration force coefficient times its reference area
(CFz₀ x REF_AREA) depends only on the mesh, since every solve starts from the same uniform flow. In each B271 case the
counted solve has the same value as an earlier solve on the old mesh, to 9-10 digits:

| Iteration | Counted solve | Earlier solve on the old mesh |
|---|---|---|
| O_GR 5 | 44.0121757297 (row 51) | 44.0121757220 (rows 7, 28) |
| O_GR 7 | 41.6649517041 (row 74) | 41.6649517085 (row 44) |
| O_GR 8 | 40.1327788978 (row 81) | 40.1327789003 (rows 7, 48) |
| N_GR 11 | 53.0916583543 (row 129) | 53.0916583527 (row 47) |

A new mesh gives a different value (for example O_GR 7 44.0655 → 41.6650 when the 115 m² mesh was loaded). The meshes
were also matched by payload length: the base64 length of each loaded mesh equals 4·⌈mesh_size_bytes/3⌉ of the
generate_volume_mesh named above.

**Fuel with the correct values.** Each corrected number re-flies the counted mission's exact aviary inputs with only the
listed values replaced. It was flown on a private aviary-mcp (ws8 port 13623, `~/Jessica/Avion/aviary-mcp` a5ef294, the
code the study used; `setsid nice -n 15`, 2 threads). The server was stopped afterwards with `kill -TERM -- -3742433`,
and port 13623 is free. Two control flights of the as-flown inputs reproduce the recorded fuel exactly:

| Control flight | Re-flown | Recorded |
|---|---|---|
| N_GR 6 | 21,330.63990873948 | 21,330.63990873952 |
| N_IF 11 | 16,602.078316622905 | 16,602.07831662289 |
| N_IF 17 (inputs rebuilt from the session start plus sets) | 16,577.50974228237 | 16,577.509742282487 |

Caveat: the corrections keep everything else as flown, including the engine scale. In a fully coupled iteration the
SU2 L/D and MTOM also set Fn_DES and therefore the scale. So the N_IF numbers are "this design with its own aero and
mass", not the fuel a fixed framework would have produced. The N_IF 17 and 20 drag factors (1.70, 1.87) are near
aviary's 2.0 clamp, so these designs are poor once their real drag is applied.

### 2. Scan of all 155 iterations

Scanner: `scan_signatures.py`, which checks the counted mission of every iteration. The 5 failed iterations (O_IF 20;
N_IF 2, 3, 10, 15) have no result.json and no counted fuel.

- **A. Carried engine replaced (B270):** the deck the mission flew, rebuilt from the pyCycle session's inputs at the
  `generate_engine_deck` that returned it (defaults T4 2857 / BPR 5.105 / fan 1.685 / LPC 1.935 / HPC 9.369 from
  `get_design_inputs`), compared with the carried engine overlaid with every `set_inputs` value of the iteration.
  **Only O_IF 13.** The 23 check_coupling "link -> link engine" FAILs are agents re-typing or rounding values (5.1 vs
  5.105) or sending none while the carried engine was the baseline (O_IF 3, 7, 15, 16; O_SP 2, 9, 16). In all of them
  the deck matches what was intended.
- **B. SU2 on an earlier mesh / CL-CD not of the flown shape (B271, B272-aero):** the solve whose CL equals the flown
  CL, the mesh in its session (from create_su2_session/set_mesh, matched to the generating call by payload length),
  and the Wing1 shape that mesh was made from, compared with the flown area and AR. Iterations with no solve and
  carried aero are checked against the carried wing. **Only O_GR 5, 7, 8 and N_GR 11** (stale mesh) and **N_IF 11,
  17, 20** (a solve ran but no CL/drag factor was flown). S_GR 4 and 20, O_GR 16, and N_GR 2, 3, 7, 10, 17 fly
  carried aero on an unchanged, carried wing, which is correct.
- **C. Flown wing mass ≠ mass-mcp's current wing mass (B272-mass):** flown Aircraft.Wing.MASS compared with the last
  successful estimate before the mission, and the geometry that estimate read compared with the flown wing. tigl's
  `close_cpacs` auto-export counts as an export; without that, S_GR 8, O_IF 11 and N_GR 9, 18, 19 are false alarms.
  **Only N_GR 6, N_IF 17, N_IF 20.**
- The full checker `check_coupling.py` (MAS-Aviary 645101b, copied as `check_coupling_645101b.py`) over all 7 run
  folders gives the same set. Its only non-engine FAILs are O_GR 5/7/8 and N_GR 11 "geometry -> SU2 mesh"; N_IF
  11/17/20 "SU2 -> aviary"; and N_IF 17/20 and N_GR 6 "mass -> aviary" (`check_coupling_all.txt`).

**Extra signature (D: flown engine scale ≠ the last scale the iteration tried to set):**
- **N_GR 6**, as above: 1.87868 flown, which is iteration 5's scale, against 1.56435.
- **N_GR 19**: the set at row 42 / KB 44 was refused for Wing.SPAN. It carried scale 2.90054, and the mission (row 45 /
  KB 47, 35,303.4 kg) flew the earlier 2.88295. Its CL, drag factor and wing mass had already reached aviary, so only
  the scale is stale (0.6 %; not re-flown). Same mechanism as B272: a wholly refused set call, after which the agent
  flew anyway. The B272 fix (645101b) re-applies coupled results (SU2, mass, engine) captured after aviary's last
  successful set, per its BUGS entry. Whether that covers a scale the framework computes from Fn_DES was not checked.

### 3. Reported numbers this changes (10_rerun_results.md)

The report already marks all nine. With the estimates:

- **O_IF 12 → 13** (line 521), +3,086.8 kg: all of it is B270. With the carried engine, iteration 13 flies iteration
  12's inputs, so the change is 0.
- **N_IF best fuel, 16,577.5 (iteration 17)** (line 281) is about 29,809 with its own aero and mass, so it is not the
  best. **N_IF 11** goes from 16,602.1 to about 17,421, above the 16,390.5 baseline (lines 383-384). Neither stays
  below the baseline.
- **N_IF last iteration, 18,958.6 (iteration 20)** (line 281) is about 34,930 with its own aero and mass.
- **N_GR 6:** 21,330.6 becomes 21,380.8 (mass) or 20,963.2 (mass and scale). The pair delta in line 1477
  (−5,575.8) becomes −5,525.6 or −5,943.2, and line 1478 (N_GR 7, −402.8) becomes −453.0 or −35.4. The direction
  does not change.
- **Pair deltas** (lines 1474-1476): N_IF 17 −4,661.7 becomes about +8,570; N_IF 18 (k = 17) +17,837.0 becomes about
  +4,606; N_IF 20 −19,673.3 becomes about −3,702.
- **O_GR best 19,321.3 (iteration 7), N_GR best 14,199.4 (iteration 11), and N_GR 11 among the iterations meeting a
  limit** (lines 280, 282, 365, 377): no corrected value; these need an SU2 solve on the right mesh.

### 4. Possible bugs / surprises

1. **links.csv coupled columns are not always what flew.** Its `Mission.Design.LIFT_COEFFICIENT` and drag-factor
   columns differ from the counted flight's inputs in 12 iterations: S_GR 17; O_IF 13, 16; N_GR 1, 2, 3, 6, 7, 10,
   14, 15; and N_IF 12, where wing_mass_kg is empty but a zero-fuel mission ran. For example, O_IF 13 csv has CL
   0.6411 / factor 1.8156 but flew 0.5584 / 1.2690, and N_GR 2 csv has 0.7235 / 2.0 but flew 0.5597 / 1.1954. They
   look like the iteration's last (unflown) aero, not the flown one. List in `links_vs_flown.txt`; the extractor was
   not traced.
2. **design.json / design_flown.json say aero was injected when it was not.** For N_IF 11, 17 and 20, both files
   record `aero.injected_into_aviary` = {CL, drag factor}, for example N_IF 11 0.5093 / 1.0310. aviary's
   `get_results` shows neither parameter was set for the flight. Not traced to code.
3. **A mass estimate seconds after a reshape reads the old file.** In N_IF 17 (rows 4→5) and N_IF 20 (rows 3→4),
   agent_2's estimate_mass ran 4 s after agent_1's reshape, on a copy of the start CPACS (no export yet). It returned
   the old wing's mass, and that mass is what flew. This is the B262 race in the networked boards. The B272 fix may
   cover it by re-applying the current mass before flying, but only if a later estimate exists.
4. **N_IF 12** ran a `run_simulation` that "succeeded" with 8.2e-06 kg fuel (row 34). links.csv already counts it as
   not flown.

### Files (ws8 `/data/Jessica/l20_bugchecks/case5/`)

- `scan_signatures.py` (+ `.json`, `.txt`): the A/B/C/D scanner. Its `kb_seq` fields use a greedy name alignment and
  can be off by refused KB entries; the KB numbers above come from `kbseq.py` (time-matched).
- `summary.txt`: one line per iteration.
- `check_coupling_645101b.py` + `check_coupling_all.txt`: the read-only checker run over all seven folders.
- `trace_geo_mass.py`, `peek.py`, `kbseq.py`, `show.py`, `summarize.py`: inspection helpers.
- `links_vs_flown.py` + `.txt`.
- `prep_reflights.py`, `reflights.json`, `reflights_nif17_control.json`, `reflight_client.py`: re-flight inputs and
  client. Run the client with `/home/aipexws8/Jessica/avion_envs/aviary/bin/python`.
- `reflight_results*.json`, `aviary_13623.log`, `aviary_13623/` (server outputs).
- `decks/deck_7815057d6edc9732.*`: a copy of the baseline deck from `/data/avion_study/slot5/decks` (md5 of `.deck`
  identical), with the path inside `.json` / `.aircraft.csv` rewritten as `start_slot.sh` does.

## Case 6: quick checks

Code was read with `git show <commit>:<path>` in `/home/aipexws3/Jessica/Avion/MAS-Aviary`. Line numbers refer to
that commit. Run data comes from ws8 `/data/Jessica/l20_final/runs/` (read-only). One script reproduces every number
below: ws8 `/data/Jessica/l20_bugchecks/case6/case6_checks.py`, with its output saved next to it as
`case6_checks.out` (plain python3, no dependencies). Iteration k is folder `repeat_<k-1>`.

### Q1. N_GR records no graph stop reason in 17 of 20 iterations

**Verdict: recording bug.** The 17 iterations were ended by the coordinator's generic "stuck" rule, which never
writes a stop reason. The board graph's own stop rules never fired in those iterations.

Evidence:
- **The main loop checks the stuck rule first.** `src/coordination/coordinator.py:316` (b985d28) runs
  `while not self.termination.should_stop(self.history)` before it calls `strategy.is_complete()` (l.319) or
  `next_step()` (l.325). When `should_stop` is true, the loop simply exits and nothing is recorded.
- **What `should_stop` checks.** `src/coordination/termination.py:22-29` tests for the keyword, max_turns,
  3 consecutive errors, or `_stuck_detected` (l.58-66). `_stuck_detected` is true when the last two messages come from
  the same agent with identical content. `check_reason()` (l.31-41) can name the reason, but nothing in `src/` calls
  it (git grep).
- **Every other exit path records a reason.** In the networked strategy, `_end()` in `is_complete`
  (`src/coordination/strategies/networked.py:517-523`) and every `terminate` branch in `_board_graph_next_step`
  (l.1357-1372) set `bg.stop_reason`, and `BoardGraph.step()` sets it too (`src/coordination/board_graph.py:194, 206,
  209`). Only the coordinator's own pre-check bypasses all of them.
- **Data: 17 of 17 match.** In each of the 17 iterations with `design.json graph_route.stop_reason = None` (iterations
  2, 4-15, 17-20), the last two `result.json` messages come from the same peer (agent_1) with identical content.
  - Example, iteration 2: the messages for parallel cycles 8 and 9 are the same `final_answer`, "I have successfully
    increased Wing1's aspect ratio from 10.0 to 11.0 ...".
  - The three iterations that do record a reason are not stuck:
    - iteration 1: "budget of 25 role runs at complex spent"
    - iteration 3: "30 board cycles dispatched"
    - iteration 16: "every TODO is done and nothing was left to act on"
- **Why the rule fires so often in N_GR.** When only one TODO is available, only agent_1 is dispatched (see Q2). So
  consecutive history entries come from one peer, and a repeated `final_answer` trips the rule.
- **The rule cuts iterations short.** The stopped iterations end after 5-13 role runs (`graph_route.role_runs`),
  against a COMPLEX budget of 25.
- The same code is at aeb67dd; `git diff aeb67dd b985d28` shows no change to termination.py or coordinator.py.

Effect and fix:
- **Numbers it changes:**
  - `graph_stop_reasons.csv` for N_GR: the 17 blanks should read "stuck: identical output".
  - Report 10 l.824, l.2068 and surprise 1 (l.2148), which calls this "possibly a recording difference".
  - It is not a recording difference: the stuck rule ended these iterations early.
  - It explains part of N_GR's low step counts (surprise 4, median 156.5).
  - No fuel or GTOW value changes.
- **Fix:** after the loop in `Coordinator.run`, write `self.termination.check_reason(history)` into the message
  metadata and into `bg.stop_reason` when it is empty. Better still, skip the stuck rule when the board graph owns
  termination.

### Q2. N_GR uses only 2 of its 3 peers; N_IF's spawned peers never take a step

**Verdict: not a bug in either case. Both follow from the dispatch rules as written.** Each one is a design
consequence the paper should state.

#### N_GR (board graph): at most 2 peers can ever act at once

**The dispatch rule.** `networked.py:1373`:
`peers = list(self._agent_order)[:max(1, min(len(available), len(self._agent_order)))]`.
- `available` is `bb.read_available_todos()` (l.1367). That means TODOs that are pending or failed and whose
  `depends_on` are all DONE (`src/coordination/blackboard.py:318-349`).
- The peers are always the first N peers in the fixed order agent_1, agent_2, agent_3.
- Claimed TODOs are released before each dispatch (l.1356).

**Board dependencies.** `board_graph.py:32-33`, restricted to the TODOs on the board at each level (`_deps`,
l.62-64):
- **SIMPLE:** MISSION -> SIMULATION -> REVIEW is a chain, so at most 1 TODO is available.
- **MODERATE:** AERO and MASS have no on-board dependencies. PROP needs MASS, MISSION needs AERO and MASS, SIMULATION
  needs MISSION, REVIEW needs SIMULATION and PROP.
  - The largest sets that can be available together are {AERO, MASS}, {AERO, PROP}, {PROP, MISSION} and
    {PROP, SIMULATION}, all of size 2.
  - A set of 3 would need, for example, AERO pending while MISSION is done. That cannot happen:
    - a DONE TODO cannot be claimed or failed (blackboard.py:366-369, 423);
    - a reopen always reopens the whole suffix of `NODES` from the target (`graph_router.downstream_of` =
      `NODES[NODES.index(node):]`; `board_graph._reopen_from` l.91-95, `_set_level` l.87).
- **COMPLEX:** GEOMETRY comes first, so only 1 TODO is available until it is done. After that the board behaves like
  MODERATE.

**Result.** At most 2 TODOs are ever available, so agent_3 can never be dispatched. The data agree:
- `agents_spawned` (the number of distinct peers that sent messages, `org_theory_metrics.py:247`) is 2 in 19
  iterations and 1 in iteration 6.
- agent_3 takes no step in any iteration.

**Most peers that can act at once on the N_GR board: 2.** The third peer is dead weight under this rule.

#### N_IF (iterative feedback, concurrent board, no graph): every peer acts in every cycle; spawned peers wait for the next cycle

**The rule is not `min(available, peers)`.** `networked.py:430-459` dispatches all peers in `self._agent_order` every
cycle, up to `_max_concurrent_runs` cycles. That is 2 per `config/aviary_mdo_f25_networked.yaml:54`, and N_IF has no
graph override (the l.308 and l.316 overrides apply only in graph mode). Every `result.json` shows at most 2
`parallel_cycle` values.

**How a spawned peer joins.** `spawn_peer` (`src/tools/networked_tools.py:218-290`) adds the new agent to the shared
agents dict. `next_step` picks it up only at the start of the next cycle (`networked.py:405-407`).

**When the 7 spawn calls happened.** All 7 succeeded (agent_4 or agent_5, `"success": true`). Each one came in the
last cycle of its runner attempt. Times are from the step logs; a cycle starts when all 3 peers start together:

| Iteration (attempt) | Cycle starts | Spawn time(s) | Cycle of the spawn |
|---|---|---|---|
| 8 (att. 2) | 1794613734, 1794620591 | 1794623126 | cycle 2 (last) |
| 11 (att. 1) | 1794674671 only | 1794675589, 1794676918 | cycle 1; the attempt never reached cycle 2 |
| 13 (att. 1) | 1794691726 only | 1794696510 | cycle 1; the attempt never reached cycle 2 |
| 15 (att. 2) | 1794712947, 1794716900 | 1794718178, 1794719339 | cycle 2 (last) |
| 19 (att. 1) | 1794761218, 1794766384 | 1794766919 | cycle 2 (last) |

**Result.** No cycle was dispatched after any spawn, so no spawned peer ever stepped. Why the attempts for iterations
11 and 13 stopped after one cycle was not checked.

**Most peers that can act at once on the N_IF board:** all peers present at the start of the cycle. That is 3 from
the start, and up to `max_agents` = 7 only if peers are spawned during cycle 1 and cycle 2 then runs. In this data it
was 3 every time.

**Numbers it changes:** none. Report §0.8 is correct as measured. The wording should be "spawned peers could only act
in a later cycle, and none followed", not "refused by the cap": every spawn returned success with a total of 4 or 5
agents, below 7.

### Q3. Networked `convergence_classification`: why the eval-success input is false even in iterations that flew

**Verdict: not a bug. "Eval success" is not "flew".** It means all five metrics are within ±10 % of the DLR-F25
reference. One small bug: an eval that was skipped is counted as a failure.

**Where the input comes from.**
- `src/runners/batch_runner.py:1219`: `ot_config["_eval_success"] = classification.result == "success"`.
- `classification` comes from `classify_aviary_eval` (`src/logging/eval_classifier.py:105-255`). That function returns
  `"success"` only when fuel, GTOW, wing mass, reserve fuel and ZFW are all within `max_deviation_pct` = 10 % of the
  reference: fuel 12,612.80, GTOW 73,705.42, wing 7,975.44 kg (l.72-81, l.176-200).
- Otherwise it returns `omission` or `commission` (l.233), or `sim_fail` or `eval_skipped`.

**Data.**
- No iteration of any combination has `eval_result = success`. links.csv counts: omission 104, commission 50,
  eval_skipped 1, blank 5.
- In every networked iteration that flew, `fuel_pass` and `gtow_pass` are False. Networked fuel ran from 14,199 kg
  (N_GR iteration 11) to 38,913 kg; the pass band is ≤ 13,874 kg.
- So `_eval_success` = False everywhere, and `_convergence_classification` (`org_theory_metrics.py:124-138`) can only
  return `joint_myopia` (score ≥ 0.6) or `incoherent_team`.

**Small bug.** The N_IF iteration whose eval was skipped (iteration 12, result `eval_skipped`) also gets
`_eval_success` = False and is labelled `incoherent_team`. It should get `None`, which gives `low_convergence`
(l.129-131).

**Numbers it changes:**
- Report l.954 and surprise 2 should say "eval-success = within 10 % of the F25 reference on all five metrics, met by
  no iteration of any combination". That is not the flight result and not the 15,000 / 90,000 kg limits.
- N_IF's label count becomes incoherent_team 14, low_convergence 1, joint_myopia 1 if the skipped eval is treated as
  unknown.
- The labels carry only the 0.6 threshold, as the report already says.

### Q4. What the graph-routed stop reason "complete" means in S_GR

**Verdict: misleading label (a naming bug, not a crash).** "complete" is the default stop reason and does not mean the
integrator said COMPLETE.

**The code (aeb67dd).**
- `src/coordination/conditional_graph_handler.py:208` sets `stop_reason = "complete"`. It is overwritten only for
  budget (l.212) and missing-agent (l.216) exits. So "complete" just means the loop reached `node == gr.COMPLETE`.
- `graph_router.route_after_review` (`src/coordination/graph_router.py:207-234`) routes to COMPLETE in three cases:
  - (a) a recorded verdict COMPLETE (l.216-217);
  - (b) a text verdict COMPLETE or TASK_COMPLETE (l.221-223);
  - (c) **"the integrator gave no recommendation to act on"** (l.225-227). This happens when the visit recorded no
    recommendation and `parse_recommendation` finds no RECOMMENDATION_TO / RECOMMENDED_CHANGE that
    `recommendation_target` can map.

**Data.** All 9 S_GR "complete" iterations (4, 6, 7, 8, 10, 11, 12, 18, 20) end on case (c). In each one:
- The final `mdo_integrator` visit called no `record_recommendation`. Every recommendation signal in
  `design.json coordination_signals` predates the start of the final visit.
- The integrator's final text gives the verdict CONTINUE, for example `"VERDICT": "CONTINUE"` in JSON, or
  "The verdict is CONTINUE" in iteration 20.
- That text has no RECOMMENDATION_TO / RECOMMENDED_CHANGE field. The JSON outputs carry only `MDO_REASONING`.

**O_GR has the same issue** (same handler). Its 9 "complete" iterations split into 7 case (c) and 2 genuine
"recorded the verdict COMPLETE".

**Numbers it changes:**
- The stop-reason categories in `graph_stop_reasons.csv` and the plots (`plots.py:491-523`): S_GR "complete" 9 should
  become "ended: CONTINUE verdict with no actionable recommendation" 9, and O_GR splits 7 / 2.
- Report l.2068 ("O_GR 9 [complete]") overstates O_GR's completions: only 2 are integrator COMPLETE.
- No fuel numbers change.

**Fix:** give case (c) its own stop reason (e.g. `"no actionable recommendation (verdict CONTINUE)"`) and its own rule
name. Either route it like a missing recommendation (re-ask the integrator) instead of ending the iteration, or
document that it ends the iteration.

### Q5. The S_SP aerodynamics stage fails its completion criterion in 20 of 20 iterations

**Verdict: bug (config). The criterion checks the wrong tool.** Impact is limited to metrics, because the criteria are
observational and never block a stage.

**The criterion text** (`config/mdo_f25_staged_pipeline_sequential.yaml:35-40`, b985d28; S_SP loads this file via
`src/runners/batch_runner.py:133-134`):
```yaml
    - name: "aerodynamics_analyst"
      completion_criteria:
        type: "tool_attempted"
        check: "tool_called"
        tool_name: "run_simulation"
        description: "aerodynamics_analyst ran a CFD simulation via su2-mcp"
```
- `run_simulation` is aviary's mission tool. The description says SU2.
- The orchestrated file `config/mdo_f25_staged_pipeline.yaml:79-84` (used by O_SP) has `tool_name: "run_su2_solver"`.
  That is why O_SP is met in 20 of 20.
- Criteria never block advancement (`src/coordination/staged_pipeline_handler.py:5, 110, 452`).

**Data.**
- S_SP's aerodynamics_analyst called `run_su2_solver` in 20 of 20 iterations and `run_simulation` in 0.
- Over all 20 iterations: create_su2_session 22, run_su2_solver 21, read_history_csv 16.
- `per_stage_completion` shows `met: False, reason: completion_criteria` for aero in all 20.

**Numbers it changes** (S_SP, recomputed with aero counted as met):
- Stages not meeting criterion per iteration: 1.6 ± 0.598 → **0.6 ± 0.598**.
- Iterations with at least one stage not met: 20/20 → **11/20**.
- Worker completion rate: 0.771 → **0.914**.

**Fix:** set `tool_name: "run_su2_solver"` at l.39.

### End of the file

| Case | Verdict | Numbers in the paper it changes | Fix |
|---|---|---|---|
| 6.1 N_GR missing stop reason | **bug** (recording) | N_GR stop-reason table: the 17 blanks are "stuck: identical output". Report l.824/2068/2148 wording. Explains part of N_GR's short iterations (5-13 role runs vs budget 25). Not Table 2 fuel/GTOW, not the 3 iterations that meet both limits. | Record `TerminationChecker.check_reason()` after the coordinator loop, or exempt the board graph from the stuck rule (coordinator.py:316, termination.py:58) |
| 6.2 Peer dispatch | **not a bug** (design consequence) | Appendix/§0.8 wording: N_GR at most 2 peers can act at once; N_IF all present peers act each cycle, spawned peers only from the next cycle, and no spawn was followed by a cycle. No numbers change. | Document it; or dispatch spawned peers immediately, or raise `max_concurrent_runs` |
| 6.3 Eval-success always false | **not a bug** (definition), plus a small bug: eval_skipped counted as failure | Results/Appendix wording for convergence_classification; N_IF iteration 12 label (incoherent_team → low_convergence) | `batch_runner.py:1219`: map `eval_skipped` to `None` |
| 6.4 S_GR "complete" | **bug** (misleading label) | Stop-reason table/plots: S_GR 9 "complete" are all "CONTINUE with no actionable recommendation"; O_GR 9 = 7 of those + 2 real COMPLETE. Report l.2068. | New stop reason/rule for `graph_router.py:226-227`; set it in `conditional_graph_handler.py` |
| 6.5 S_SP aero criterion | **bug** (config) | Table 2 / Appendix E S_SP rows: stages not met 1.6 → 0.6 / it, ≥ 1 in 20/20 → 11/20, worker completion 0.771 → 0.914 | `mdo_f25_staged_pipeline_sequential.yaml:39` → `run_su2_solver` |

### Possible bugs / surprises

1. **The stuck rule ends N_GR iterations early, not just silently.** 17 of 20 N_GR iterations stopped after
   agent_1 repeated an identical `final_answer` in two consecutive one-peer cycles, at 5-13 role runs. N_GR's
   effort and step metrics therefore partly measure this rule. The old run had 4 such blanks.
2. **O_GR "complete" is mostly not COMPLETE**: 7 of its 9 are the no-recommendation fallback.
3. **The integrator's per-visit record obligation was not enforced on the last visit.** The B180 reminders were
   reset per visit (`conditional_graph_handler.py:222-227`), yet the final review visit in all 9 S_GR "complete"
   iterations recorded nothing. Why the reminder gate let `final_answer` through was not checked.
4. **N_GR iteration 3 used all 30 board cycles for only 8 role runs** (`graph_route.role_runs` = 8). Most cycles
   completed no TODO. Not investigated.
5. **Two N_IF runner attempts ended after one cycle** (iteration 11 attempt 1, iteration 13 attempt 1). Their spawned
   peers were lost with them. Why those attempts stopped was not checked.

---

## Summary table

| Case | Verdict | Numbers in the paper it changes | Fix |
|---|---|---|---|
| 1. O_SP 6 and 19 at 14,812.1 kg | **bug** (untrimmed SU2 after TiGL closed unmeasured; CD below induced drag) | Two of the three iterations that meet both limits are removed. Table 2 O_SP: mean 22,167.0 → 22,986.5 kg (SD 2,515.8 → 46.5, min 14,812.1 → 22,914.1) if replaced by the trimmed value. O_SP GTOW min 88,021.2 → 100,071.7. The "lowest GTOW = O_SP 6" statement. Trend and best-iteration rows for O_SP not yet recomputed. | `_inject_target_cl` takes the wing area from the CPACS being solved, not from a possibly-closed TiGL session. `close_cpacs` clears `sessions["tigl"]`. `aero_guard` refuses a mission whose SU2 had no target CL, whose drag is `su2_unchecked`, or whose CD is below the induced drag. Re-fly O_SP 6, 19. |
| 2. O_IF 4 at 13,765.6 kg | **bug** (same as Case 1). The 1.106 → 1.61 scale jump is entirely L/D; Fn_DES does not depend on the cycle, and the deck reference stayed 5,900 lbf. | The study minimum and the third both-limits iteration are removed. Table 2 O_IF: mean 20,654.0 → 21,106.8 kg, min 13,765.6 → 17,680.6. O_IF 4's estimate is about 22,369 kg (from iteration 3's identical inputs, not re-flown). | As Case 1; re-fly O_IF 4. |
| 3.1 Same design, different fuel | **bug** (3 causes) | O_SP's 22,914 vs 23,006 split (92 kg, default chord). S_IF 18 vs 19-20 (404 kg). S_GR 3 vs 4 (556 kg). S_GR 5, 9, 15, 19 and N_IF 5 (0.6 % engine-scale error, 75-157 kg). S_SP/S_IF solved at the agents' typed CL 0.56. Table 2 compares combinations whose lift targets were set three different ways. | Carry the measured MAC between iterations and stop defaulting to 4.0 m. Always inject the framework target CL, or refuse an agent-typed one. Engine scale uses the attached deck's reference. |
| 3.2 Consecutive identical fuel | **not a bug** | none (these iterations re-flew the carried design unchanged) | — |
| 3.3 O_GR 17-20 CL alternation | **bug, not B271** (graph pre-init creates the SU2 session before the carried mass is loaded) | O_GR 13, 17, 19 are inflated by about 9.5 t each (32.7 t vs 23.2 t for the same design), so the late "oscillation" is the framework, not the agents. The same pre-init applies to S_GR and N_GR. | `graph_preinit.py`: seed the carried mass before creating the SU2 session. |
| 4. Sweep 21.65 → 27.33 deg | **bug in the prompt design table** (aviary flies the real measured quarter-chord sweep) | O_SP, O_IF and N_IF all flew a different wing from the baseline and the other five, so they are not the "same start". Every fuel/GTOW/wing-mass of these three is affected. | Set the table defaults to 21.65 / 28.00 / 37.20. |
| 5. Nine known-flawed iterations | **bug**, all 9 confirmed; none missed | O_IF 13: 20,767.4 → 17,680.6 kg (the +3,086.8 kg change is entirely the bug). N_IF 17: 16,577.5 → about 29,809. N_IF 20: 18,958.6 → about 34,930. N_IF 11: 16,602.1 → about 17,421. N_GR 6: 21,330.6 → 20,963.2. N_IF best fuel and below-baseline counts change. O_GR 5/7/8 and N_GR 11 need a new SU2 solve to estimate. | Already fixed (B270-B272). Re-fly if the paper reports them. |
| 6.1 N_GR no stop reason (17/20) | **bug** (the generic "stuck" rule exits before the graph records a reason, and ends those iterations early at 5-13 of 25 role runs) | `graph_stop_reasons` for N_GR; report wording. N_GR's low step counts are partly this. | Record a "stuck" stop reason. |
| 6.2 Peer dispatch | **not a bug** (N_GR: at most 2 TODOs available at once, so at most 2 peers. N_IF: spawned peers join only in the next cycle, and all 7 spawns came in the last cycle.) | Wording of section 0.8 | — |
| 6.3 Eval-success always false | **not a bug** (definition: fuel, GTOW, wing mass, reserve fuel and ZFW all within ±10 % of DLR-F25; no iteration meets it) | Wording. One label: N_IF 12 should be `low_convergence`, not `incoherent_team`. | Treat eval_skipped as missing, not as failed. |
| 6.4 S_GR "complete" | **misleading label** ("complete" is the default stop reason, also reached when the integrator gave no actionable recommendation) | S_GR: 9 → 0 real COMPLETE verdicts. O_GR: 9 → 2. Summary bullet 4 ("graph-routed completes in 18 of 60") becomes 2 of 60. | Use a distinct stop reason, e.g. "no_actionable_recommendation". |
| 6.5 S_SP aero criterion | **config bug** (S_SP's yaml checks `run_simulation`; the aero agent calls `run_su2_solver`) | S_SP: stages not met per iteration 1.6 → 0.6; iterations with a stage not met 20/20 → 11/20; worker completion 0.771 → 0.914 | `mdo_f25_staged_pipeline_sequential.yaml:39` → `run_su2_solver` |

## Possible bugs / surprises

1. **mass-mcp sizes every wing with the wrong sweep.** `src/mass_mcp/cpacs/parser.py` `get_wing_sweep` (study checkout
   1f25297) averages the non-zero positioning angles and calls the result quarter-chord sweep: 27.33 deg for the baseline
   wing (true 21.65), 23.94 deg for the 27.33 deg wing. The more-swept wing is therefore sized lighter, which is why O_SP's
   wing mass (8,717.8 kg) sits below the baseline's (8,735.3 kg). This touches every wing mass in the study (Case 4 agent;
   the code was checked by me).
2. **O_IF trims SU2 to the wrong mass.** Aero runs before structures, so the target CL comes from the CPACS mTOM
   (78,126 kg, CL 0.559). Fn_DES and the mission's takeoff mass use the sized 92,054.7 kg, whose cruise CL is 0.659.
3. **The drag factor uses the AR the agent typed** (`data_plane.py:3475`), or 11.0 if none was typed, not the measured
   AR (O_SP 6: 0.845 vs 0.810).
4. **links.csv's CL and drag-factor columns are not the flown values in 12 iterations** (S_GR 17, O_IF 13 and 16, N_GR 1,
   2, 3, 6, 7, 10, 14, 15). These columns look like each iteration's last, unflown aero. Example: O_IF 13's csv has
   0.6411 / 1.816, but it flew 0.5584 / 1.269. The extractor needs a fix before the design-parameter plots and Table 2
   aero columns are reused.
5. **N_GR 19 flew a stale engine scale** (2.8830 instead of 2.9005) after a refused set call: the same mechanism as N_GR 6,
   not re-flown. Whether the B272 fix covers the engine scale was not checked.
6. **N_IF 11, 17 and 20's design.json / design_flown.json say aero was "injected_into_aviary"** when it never reached the
   mission.
7. **N_IF 12 has a "successful" run_simulation with 8.2e-06 kg fuel** (links.csv already treats it as not flown).
8. **O_IF 5 reports 226 optimizer iterations against a limit of 200** but says converged with exit code 0. What
   "iterations" counts was not checked.
9. Not investigated: why B180's per-visit reminder did not make the S_GR integrator record a recommendation on its last
   visit; N_GR 3 using all 30 board cycles for 8 role runs; N_IF 11 and 13 first attempts stopping after one cycle.
10. Process note: the engine-deck file hashes were not checked (the decks are on ws6; deck keys were compared instead).

---

## Addendum (2026-11-18): measured fuel effect of the sweep start and the agent-typed lift target

Nine missions flown through the full coupled chain: the optimizer's evaluator (MAS-Aviary bcf115d, unedited copy), on a
private server slot on ws8 (stopped afterwards). All 9 converged. Records: ws8
`/data/Jessica/l20_bugchecks/reflights/results/F01-F09.json`. Full write-up: `reflights/reflights.md`.

| Experiment | Flown | Fuel (kg) | GTOW (kg) | Δ fuel |
|---|---|---|---|---|
| E1 sweep start | anchor, sweeps 21.65 / 28.00 / 37.20 (F03) | 21,698.9 | 98,248.1 | |
| | anchor, sweeps 27.33 / 32.21 / 40.4 (F04) | 22,918.8 | 100,077.4 | **+1,219.9 (+5.6 %)** |
| E3 determinism | F04 repeated with new sessions (F05) | 22,918.797727179524 | identical | 0 (bit-identical) |
| E2 lift target, anchor | framework target 0.5592 (F01) | 21,697.8 | 98,246.6 | |
| | agent-typed 0.56 | 21,765.6 | | +67.8 |
| E2 lift target, S_IF it. 19 wing (105 m², AR 13) | framework target 0.6539 (F06) | 30,146.0 | 110,426.1 | |
| | agent-typed 0.56 (F07) | 19,799.9 | 95,598.3 | **−10,346 (the 0.56 hides 10.3 t)** |
| E2 lift target, S_SP it. 3 wing (122.8 m²) | framework target 0.5590 | 19,679.4 | | |
| | agent-typed 0.56 | 19,770.1 | | +90.6 |

- The 27.33 deg start costs about 1.2 t on the anchor, through drag (CD 0.0229 → 0.0250) and engine size (scale 1.376 →
  1.444); the wing mass barely moves. F04 reproduces O_SP/O_IF iteration 1 (22,914.1 kg) within 4.7 kg.
- The agent-typed 0.56 is harmless near the baseline area, but on small wings it under-trims. S_IF's low fuel on its
  105 m² wing (19,403-19,807 kg) would be about 30,146 kg under the framework's own rule. So S_IF's (and possibly S_SP's)
  small-wing results are optimistic by up to ~10 t. The 0.56 flights reproduce the run's S_IF 18 and S_SP 3 within 7 kg.
- The framework target used the CPACS mTOM 78,126 kg every time, which is not the flown GTOW. A sized-mass target would
  penalize the small wing further.

### The baseline is not coupled (affects every "vs baseline" number)

The unmodified anchor flown through the full coupled chain (F01) burns **21,697.8 kg** (GTOW 98,246.6 kg). The run's
baseline of **16,390.5 kg** was flown aviary-only: engine scale 1.0 on the bench engine, aviary's own lift and drag,
nothing from SU2/mass/pyCycle (`baseline_run1.json` anchor_params). The old study's 18,197.0 kg baseline was flown the
same way. Every agent iteration flies the coupled chain. So "every combination's mean fuel is above the baseline" in
report 10 compares a coupled design against an uncoupled one. Against the coupled anchor (21,697.8 kg), three
combinations' mean fuel is below the start: S_SP 21,024.2, S_IF 21,144.9 and O_IF 20,654.0 (report 10 section 2.2).
But S_SP and S_IF are flattered by the agent-typed 0.56 (E2), and O_IF's mean includes O_IF 4 (Case 2) and O_IF 13
(B270). The medians of S_SP, S_IF, O_IF and N_GR (20,985-20,989 kg) are also below it. F01 matches the optimizer's own evaluation of the same design to 0.006 kg.
**Which baseline the paper uses is Jessica's decision**; the 15,000 kg target was set against the uncoupled one.
