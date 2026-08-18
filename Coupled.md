# Coupling Results — 7-Combo Sweep, Qwen3-32B (2026-08-17)

Sweep: `logs/all7_20260817_095456.log` · W&B run `rrnlyp8p` · 14 runs (7 combos x 2 chain
links) · 13 hours · 150-min cap per run.

Code under test: Option A (agent owns AREA/SCALE_FACTOR), B50 (timeout not retried), B51
(solver-owned disclosure), B54 (dotted names rejected), B56 (any verdict terminates),
threshold-only budget warning, reasoning capture.

## Results

| combo | link | coupled | fuel (kg) | Δ vs start | constraints | verdict |
|---|---|---|---|---|---|---|
| sequential_iterative_feedback | 0 | False | 16,066 | — | 3/5 | — |
| sequential_iterative_feedback | 1 | **True** | 23,820 | −7,754 | 2/5 | — |
| sequential_staged_pipeline | 0 | **True** | 20,380 | — | 1/5 | — |
| sequential_staged_pipeline | 1 | **True** | 20,380 | **+0** | 1/5 | — |
| sequential_graph_routed | 0 | **True** | 23,666 | — | 2/5 | MAJOR_ISSUES |
| sequential_graph_routed | 1 | **True** | 21,922 | **+1,745** | 2/5 | MAJOR_ISSUES |
| orchestrated_staged_pipeline | 0 | — | TIMEOUT | — | — | — |
| orchestrated_staged_pipeline | 1 | False | 14,613 | — | 4/5 | — |
| orchestrated_iterative_feedback | 0 | False | 8,378 | — | 4/5 | — |
| orchestrated_iterative_feedback | 1 | False | 17,870 | −9,492 | 1/5 | — |
| networked_iterative_feedback | 0 | **True** | 9,568 | — | 4/5 | — |
| networked_iterative_feedback | 1 | **True** | 10,215 | −647 | 4/5 | — |
| networked_graph_routed | 0 | — | TIMEOUT | — | — | — |
| networked_graph_routed | 1 | False | 7,864 | — | 4/5 | — |

12 successes, 2 timeouts.

## Coupling rate: 7 coupled / 3 uncoupled / 4 UNMEASURED

The headline "7 of 14" understates it. The uncoupled rows are two different things:

| detection | rows | meaning |
|---|---|---|
| `data_plane`, `MISSING_no_su2_aero` | 3 | reliably measured as uncoupled |
| `cd_threshold_fallback`, `status=None` | 4 | **not measured** — the mis-calibrated heuristic the ledger itself flags as unreliable |

Among rows we can trust: **7 coupled / 10 measured = 70%**.

## Why the uncoupled runs were uncoupled

Traced end-to-end for `orchestrated_iterative_feedback` (both links, `MISSING_no_su2_aero`):

```
geometry_engineer ran, HAD generate_volume_mesh in its toolset, called it 0 times
  -> set_mesh            -> "success": false
     -> run_su2_solver   -> "mesh file named mesh.su2 was not found"  (x9 across the runs)
        -> no CL/CD, read_history_csv never called
           -> MISSING_no_su2_aero -> UNCOUPLED
```

The agents were not confused about the aero sequence. Their own reasoning states it
correctly: *"create an SU2 session, generate a volume mesh, configure the session from
the CPACS file, and run the SU2 solver"* and *"run the solver, and then read the history
CSV for aerodynamic data."*

**The geometry worker never got that far.** Its task was "open a CPACS file and set
high-level parameters", and it spent its turns on two obstacles:

1. **Session-id mismatch** (B53/B55) — from its reasoning: *"The session ID provided is
   d73ba2fc-… but the observation from open_cpacs shows a different session ID:
   a0596bed-…. Hmm, maybe the initial SESSION_ID was a typo"*.
2. **Wrong component UID** — tried `wing_1`, got *"component wasn't found. The available
   UIDs are Fuselage1, Wing1, Wing2H, Wing3V"*. It recovered: this error names the
   correct values, which is the remedy class that works.

It finished without generating a mesh, and every downstream aero step failed as a
consequence. **Root cause is upstream of aerodynamics, not in it.**

## Verified findings

- **`orchestrated_staged_pipeline` never reached `mdo_integrator`** — 0 runs of that
  stage in BOTH links. Pass 1 stopped at stage 5, restarted at stage 1, pass 2 stopped at
  stage 6. No verdict, no `RECOMMENDED_CHANGE` for the next link. (B57 / B60)
- **`networked_graph_routed` hit GPU memory limits** — `CUDA out of memory` x12,
  "Tried to allocate 1.38 GiB. GPU 1 has 31.37 GiB of which 721.75 MiB is free". The
  networked structure runs many concurrent peers against one model, so KV cache grows
  with peer count. Its link-0 timeout is a resource failure, not coordination.
- **`orchestrated_staged_pipeline` link-0 timeout was NOT coordination** — 3 workers, no
  revisiting, 19 min of step time in a 150-min window. `run_su2_solver` blocked 131 min
  on a 600 s cap. (B59)
- **`sequential_staged_pipeline` ran a textbook forward pass both times** — all 7 stages
  once each, reaching `mdo_integrator`. The clean control against the orchestrated variant.

## Comparisons that hold, and ones that don't

- **`sequential_graph_routed` is the only chain that genuinely improved**: 23,666 →
  21,922 kg, both links coupled, so like-for-like. **+1,745 kg**.
- **`networked_iterative_feedback` is the strongest overall**: both links coupled, 4/5
  constraints on both, lowest coupled fuel (9,568 / 10,215).
- **`sequential_staged_pipeline` link 1 repeated link 0 exactly** — identical design to 14
  decimal places, `Δ +0`. The chain carried the design forward and the second link
  explored nothing. Absolute fuel would hide this; the relative metric catches it.
- **Δ is only meaningful where both links share coupling status.** Three chains qualify.
  Where coupling flips (e.g. `sequential_iterative_feedback` 16,066 uncoupled → 23,820
  coupled) the change is mostly *the physics being added*, not the design degrading.
  Coupled runs sit at 17,900–24,100 kg; uncoupled at 7,800–17,900.
- **`integrator_verdict` is present for `graph_routed` only** (B61) — missing for 5 of 7
  combos, including runs that demonstrably reached the integrator. The quality signal B56
  made primary is absent for most of the matrix.

## Open bugs from this sweep

| id | summary | priority |
|---|---|---|
| B61 | verdict captured only on the graph_routed path | first — verifiable against these logs, no new runs |
| B59 | `run_su2_solver` blocks past `max_runtime_seconds` (PIPE held by forked ranks) | high — cost 150 min here, real money on API models |
| B60 | a new pipeline iteration must be earned by finishing, not taken mid-pass | high — why orchestrated_staged never produced a verdict |
| B57 | pipeline rewind guard — **three failed fix attempts**, instrument before a fourth | see attempt log in BUGS.md |
| B58 | interception truncates `list_variables`, so the agent guesses names | medium |
| B53/B55 | one session id broadcast to five servers; `get_design_state` unused by agents | medium — visible in the geometry reasoning above |
