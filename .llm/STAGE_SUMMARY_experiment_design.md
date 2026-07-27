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
- **Aviary (mission):** the baseline **linear solver** (and nonlinear solver, atol/rtol,
  maxiter, initial guesses). AUDIT FINDING: the aviary_mdo_f25_* configs pin NO explicit
  solver — they fall through to defaults that may differ by how each combo invokes aviary.
  These MUST be set explicitly and identically.
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
