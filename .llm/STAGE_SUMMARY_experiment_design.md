# Stage Summary — Experimental Design for the Paper

**Date:** 2026-07-27
**Branch:** `feat/paper-experiment-design` (off `feat/mas-gui-full` @ 38bad7d)
**Status:** MDO pipeline is functional and the SU2 coupling bug is fixed; we are now
moving into *experimental design* for the paper. Do NOT launch a full paid run until
the open decisions below are settled.

---

## 1. What now works (verified)

- **Aero↔geometry loop is closed.** `set_high_level_parameters` morphs the wing/fuselage
  (tigl-mcp), the volume mesh rebuilds, and SU2 solves the *actual* design. Lift responds
  to geometry (baseline CL≈0.093 → morphed CL≈0.18–0.22, real converged solves).
- **Root-cause fix shipped (commit 38bad7d):** the SU2 preset carried `CFL_NUMBER=1e3`
  (200× too high, copied from the 2-D `inv_NACA0012` QuickStart). SU2's own 3-D inviscid
  wing case (`inv_ONERAM6.cfg`) uses JST + 3-level multigrid at CFL=5. At 1e3 the multigrid
  solve was compute-bound and never converged in the 300 s cap → silent fallback to
  reference CL/CD (the old "CL=0.1871"). Fixed to `CFL=20 + CFL_ADAPT` across all 5 combo
  configs; multigrid and the ~660k mesh kept. Verified end-to-end (run6: real 155 s solve,
  exit 0, CL=0.2217, fuel 8562 kg).

## 2. The 1× exploratory sweep (2026-07-27) — results + what it taught us

Ran all 8 combos once (`--repeats 1`), seed 42, model `claude-sonnet-4-6`. **7/8 succeeded,
1 failed.** THIS WAS AN EXPLORATORY RUN, NOT A PAPER RESULT — single sample, and issues
below make the numbers non-comparable.

| Combo | Status | Fuel kg | GTOW kg | eval | dur s |
|---|---|---|---|---|---|
| sequential_iterative_feedback | **FAIL** (zero fuel) | — | — | — | — |
| sequential_graph_routed | success | 7718.2 | 68601 | omission | 1421 |
| sequential_staged_pipeline | success | 8562.5 | 69530 | omission | 553 |
| orchestrated_iterative_feedback | success | 4759.2 | 66226 | omission | 578 |
| orchestrated_graph_routed | success | 8813.3 | 69809 | commission | 741 |
| orchestrated_staged_pipeline | success | 8012.9 | 65000 | omission | 769 |
| networked_iterative_feedback | success | 4652.2 | 64969 | omission | 249 |
| networked_graph_routed | success | 8813.3 | 69809 | commission | 741 |

**Cost:** ~$30 for these 8 runs on Sonnet 4.6 → **~$3.75/run** (my earlier "$1–2/run"
estimate was wrong; correct all future budgeting to ~$3.75/run on Sonnet).

**Findings:**
- **Stochastic flakiness is real.** `sequential_iterative_feedback` FAILED here (aviary
  produced zero fuel) but SUCCEEDED in run6 with the identical config. A single run per
  combo is not a result — we need repeats and a per-combo success rate.
- **3 SU2 `exit_code:1` errors** appeared across the sweep (agents appear to have retried;
  all 7 successes show `converged=True`). Not yet confirmed whether every SU2 solve was a
  real converged run vs a recovered retry — needs per-combo verification.
- **The two `graph_routed` combos are byte-identical** (8813.3 / 69809). Likely legitimate
  (both use the predefined `mdo_f25` graph → same flow), but confirm.
- **Wide fuel spread (4650–8813 kg)** even though starting params were identical (see §3).

## 3. Experimental-design requirements BEFORE a paper run

### 3.1 Fixed / controlled starting conditions — ALL parameters, ALL disciplines
The independent variable is the **coordination structure ONLY**. Therefore EVERY starting
parameter in EVERY discipline must be byte-identical across all 8 combos — not just the wing
design. That includes:
- **Geometry:** design inputs (AR, AREA, SWEEP, TAPER, fuselage dims). Top-level
  `param_sets.json` is fixed (seed 42) BUT agents appear to re-morph mid-run (fuel spread
  4650-8813 kg on the same input) — must pin so agents evaluate the GIVEN design, not their own.
- **SU2 (aero):** full numerics preset (scheme, CFL, multigrid, linear solver, convergence,
  markers, REF_*). AUDIT FINDING 2026-07-27: these are NOT identical today — the SU2 param
  block hashes differ across the 5 configs (sequential=e6118eb0, graph/staged=7e8ba74d,
  orchestrated=beba7193, networked=1c51cae3). CFL=20 is uniform now but other fields diverge.
- **Aviary (mission) — SLSQP gradient optimizer:** the parameters that go INTO SLSQP
  (iteration budget, cruise Mach/altitude/range, design-var bounds, convergence tol, initial
  guesses). AUDIT FINDING 2026-07-27 (GOOD): these are ALREADY identical across all 5 combos —
  every combo calls the same `run_simulation` tool (aviary-mcp/aviary_runner.py) with the same
  agent-supplied config: `optimizer_max_iter=200`, `cruise_mach=0.78`, `cruise_altitude_ft=33000`;
  and the driver internals (`add_driver("SLSQP", max_iter=200)`, `add_design_variables()`,
  `set_initial_guesses()`, scipy-default tol) are shared code, so identical by construction.
  What VARIES is the aircraft design FED INTO SLSQP (wing area, SU2 CL/CD, mass) — because each
  combo's upstream disciplines produce different values (ties back to the re-morph issue above).
  DISCREPANCY TO RESOLVE: the reference benchmark (`aviary-mcp/run_reference_benchmark.py`) runs
  the optimum at Mach 0.785 / 35000 ft / 1500 nmi, but the combos evaluate at 0.78 / 33000 ft —
  align these (and confirm RANGE) so combos and the ground-truth reference use one flight point.
- **Mass (structures) & Propulsion (pycycle):** all model settings / initial conditions.

**ACTION (this is the core experimental-design task):** define ONE canonical baseline
parameter set for every discipline, factor it into a single shared source, and make all 8
combo configs reference EXACTLY it — so the ONLY thing that differs between combos is the
orchestration structure. Then audit that agents don't override any of it at runtime.

### 3.2 Model choice (SOTA for the paper)
- **Currently `anthropic/claude-sonnet-4-6`** (`config/mdo_f25_run_claude.yaml:4`).
- For the paper we likely want **`claude-opus-5`** (SOTA). Cost implication: Opus is ~5×
  Sonnet → **~$18/run** vs ~$3.75/run. A single 8-combo sweep on Opus ≈ **$144** — already
  over the $100 budget. This forces trade-offs (below).

### 3.3 Repeats & statistics
- Runs are stochastic (proven by the flaky failure). Need N repeats per combo to report
  mean ± spread and a **success rate**. N=5 is a reasonable minimum.

### 3.4 Budget arithmetic ($100 remaining)
- Sonnet 4.6: 8 combos × 5 repeats = 40 runs × ~$3.75 ≈ **$150** (over).
- Sonnet 4.6: 8 × 3 = 24 runs ≈ **$90** (fits).
- Opus 5: 8 × 1 = 8 runs ≈ **$144** (over). Opus 5 × any repeats is out of budget.
- **Trade-off to decide:** SOTA model (Opus 5, few/no repeats, over budget) vs statistical
  power (Sonnet 4.6, 3 repeats, in budget) vs a hybrid (Opus 5 on a subset of combos).

## 3.5 CROSS-DISCIPLINE parameter divergence audit (2026-07-27) — it is NOT just SU2

Audited the initial params passed to ALL 5 MCPs across the 5 combo configs. Divergences
everywhere except the mission optimizer:

| MCP | Parameter | Divergence |
|---|---|---|
| Geometry (tigl) | `far_field_distance` | 10.0 vs 50 (graph has both) |
| Geometry | `component_uid` | "Wing" (orch/net) vs "Wing1" (others) — the retry bug |
| Geometry | mesh sizing | specified in some, absent in others |
| SU2 (aero) | full numerics | orch/net specify ~10 keys, others ~30; REF hardcoded 61.39 in graph/staged |
| Mass | `material` | **"aluminum" vs "composite"** — different wing mass |
| Mass | `load_factor`, `wing_mass_method` | present in some, absent/other in others |
| Propulsion (pycycle) | design point (MN, OPR, T4, thrust, fan_pr) | pinned in some configs only |
| Mission (aviary/SLSQP) | mach/alt/range/max_iter | UNIFORM (0.78 / 33000 / 2500 / 200) — the only clean one |

Every one of these changes the output independent of the coordination structure => confound.

## 3.6 Shared-source refactor — architecture (chosen approach)

Constraint: the config loader (`src/config/loader.py`) is plain `yaml.safe_load` + env
overrides. Discipline params live as literal TEXT inside agent `role: |` prose blocks
(`stage_defaults` + `templates`). YAML anchors cannot interpolate into a `|` block scalar, so
a true single source needs a small TEMPLATING LAYER:

1. **`config/mdo_f25_canonical_baseline.yaml`** — one file holding the canonical baseline for
   EVERY discipline (geometry design inputs + mesh + component_uid; full SU2 numerics + REF
   derivation rule; mass material/load_factor/method; pycycle design point; mission already
   uniform). Single source of truth.
2. **Placeholder substitution in the loader/runner** — role prompts reference placeholders
   (e.g. `{{SU2_CONFIG}}`, `{{MESH_PARAMS}}`, `{{MASS_PARAMS}}`, `{{PROP_DESIGN_POINT}}`); the
   loader substitutes the canonical values at load time. Prose that differs = COORDINATION
   instructions only; physics inputs come from the shared file.
3. **A test** (`tests/test_canonical_params_identical.py`) asserting all 8 combos resolve to
   byte-identical discipline params — so drift can never silently return.

Effort: moderate (new file + ~loader change + refactor all 5 combo configs to placeholders +
test). This is the core experimental-design deliverable; do it before any paid paper run.

## 4. Open decisions (settle before launching the paper run)

1. **Model:** Opus 5 (SOTA, expensive) vs Sonnet 4.6 (cheaper, more repeats)? Or Opus 5
   on a representative subset + Sonnet for the rest?
2. **Fixed design input:** confirm/enforce that all combos evaluate the SAME design and don't
   re-morph — otherwise combos aren't comparable.
3. **Repeats:** how many per combo (success rate + variance)?
4. **Metrics:** define the paper's dependent variables (verification correctness, tool-omission
   rate, convergence, fuel/GTOW accuracy vs a ground-truth reference, cost, wall-time).
5. **Ground truth:** what is the reference the eval compares against, and is it the right target?
   (Current eval flags fuel "22% from ref 7000.6" — is 7000.6 the correct benchmark?)
6. **Budget cap:** hard $ ceiling for the paper experiment.

## 5. Cost reference (corrected)

| Model | ~$/run | 8×1 | 8×3 | 8×5 |
|---|---|---|---|---|
| Sonnet 4.6 | ~$3.75 | ~$30 | ~$90 | ~$150 |
| Opus 5 (est. ~5×) | ~$18 | ~$144 | ~$432 | ~$720 |

(Opus multiplier is an estimate; validate with one Opus run before committing.)
