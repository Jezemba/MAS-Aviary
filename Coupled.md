# Coupling Results — 8-Combo Matrix, Qwen3-32B, POST-FIX (2026-08-18/19)

> ## ⚠ DATA INTEGRITY WARNING (added 2026-08-20)
>
> **Sweep `1787078892` is contaminated from run `[11/16]` onward.** The tigl geometry server (8500)
> stopped answering during `networked_iterative_feedback` link 1 and never recovered — 11 connect
> failures, log lines 17344 to 58855 of 60917. Every run after that point executed **without a
> geometry server**, and the runner recorded some of them as `success` anyway because
> `MCPConnector.get_tools()` fails soft (`failed_servers` exists and nothing consumes it).
>
> **Do not use these rows:**
>
> | run | recorded | actually |
> |---|---|---|
> | `[11/16]`,`[12/16]` sequential_graph_routed | `error`, 0 turns | tigl unreachable |
> | `[13/16]` orchestrated_graph_routed L0 | 3× zero fuel | tigl unreachable |
> | `[14/16]` orchestrated_graph_routed L1 | **`success`, 15,811 kg** | produced with NO geometry |
> | `[15/16]` networked_graph_routed L0 | **`success`, 7,148 kg** | 8 failed `open_cpacs`, no mesh |
> | `[16/16]` networked_graph_routed L1 | failed, zero fuel | tigl unreachable |
>
> Only `[1/16]`–`[10/16]` ran with all five servers up. The `graph_routed` re-run
> (`logs/stat_results/1787156210/`, 2026-08-19) was launched after tigl was restarted and IS valid.
>
> **Root cause (B69):** `generate_volume_mesh` is a synchronous handler that blocks tigl's entire
> event loop, and `mesh_size_min` has no lower bound. The agent chose `0.1`; a live probe ran 21
> minutes with RSS climbing 2.2 → 14.7 GB and never completed. While it blocks, tigl answers
> nothing — not other tools, not discovery, not new connections.
>
> **B68's framing was wrong.** It said "both sequential and orchestrated complete their chains with
> these same handlers, so the STRUCTURE is the variable." With `graph_routed` they did not — and all
> of those failures were post-outage. `networked` is still implicated, but through being the only
> structure that issues CONCURRENT MCP calls (peers race in a ThreadPoolExecutor), so one peer's
> blocking mesh makes tigl invisible to every other peer.


Supersedes the 2026-08-17 pre-fix results (kept below for comparison). All runs on the LOCAL
Qwen3-32B; no paid-API runs.

Sources — each sweep's own `stat_summary.csv`, **not** the append-only design ledger. The ledger
spans sweeps, and reading its tail silently mixes old rows into new results (that trap produced a
wrong table once already).

- main sweep: `logs/stat_results/1787078892/` · log `all8_postfix_20260818_144811.log` · W&B `16ehrrlg`
- graph re-run: `logs/stat_results/1787156210/` · log `graph_rerun_20260819_121649.log` · W&B `7259tmgn`

## Results — 14 of 16 runs

| combo | link | eval | fuel (kg) | Δ chain | opt gap % | conv | min |
|---|---|---|---|---|---|---|---|
| sequential_iterative_feedback | 0 | omission | 23,193 | — | 0.0 | ✓ | 32 |
| sequential_iterative_feedback | 1 | omission | 16,066 | **+7,127** | 0.0 | ✓ | 33 |
| sequential_staged_pipeline | 0 | **sim_fail** | 28,151 | — | 0.0 | ✗ | 28 |
| sequential_staged_pipeline | 1 | omission | 22,834 | +5,317 | 0.0 | ✓ | 30 |
| sequential_graph_routed | 0 | omission | 23,666 | — | 5.50 | ✓ | 21 |
| sequential_graph_routed | 1 | omission | 21,878 | **+1,788** | 3.07 | ✓ | 24 |
| orchestrated_staged_pipeline | 0 | omission | 14,613 | — | 20.77 | ✓ | 25 |
| orchestrated_staged_pipeline | 1 | omission | 23,275 | −8,662 | 92.35 | ✓ | 25 |
| orchestrated_iterative_feedback | 0 | **commission** | 7,987 | — | 0.0 | ✓ | 20 |
| orchestrated_iterative_feedback | 1 | **commission** | 9,020 | −1,033 | 0.0 | ✓ | 12 |
| orchestrated_graph_routed | 0 | **commission** | 21,498 | — | 77.80 | ✓ | 29 |
| orchestrated_graph_routed | 1 | **commission** | 19,958 | **+1,540** | 61.50 | ✓ | 42 |
| networked_iterative_feedback | 0 | omission | 8,650 | — | 0.0 | ✓ | 7 |
| networked_graph_routed | 0 | omission | 7,148 | — | 0.0 | ✓ | 38 |

**Missing (2 of 16):** `networked_iterative_feedback` link 1 and `networked_graph_routed` link 1 —
both timed out. The networked structure runs concurrent peers against ONE local model, so inference
serialises and context grows with peer count; both timeouts are in that structure.

## What changed post-fix

**B64 was the dominant defect.** `mcpadapt` discarded the JSON-Schema `required` array, so **85
optional arguments across 60 tools** were enforced as mandatory. Worst hit: `generate_volume_mesh`
(1 declared required, 11 enforced), `create_su2_session` (0 declared, 8 enforced), `estimate_mass`
(7, including the three pinned experimental controls).

| | pre-fix | post-fix |
|---|---|---|
| mesh rebuilds per run | 9–17, all succeeding | **1** |
| optional-arg errors | 11–104 per run | **0** (only genuinely-required args now raise) |
| typical run duration | 25–57 min | **7–42 min** |
| `orchestrated_iterative_feedback` | 0-for-N, never coupled | completes both links (20, 12 min) |

**`orchestrated_graph_routed` reversed direction.** Pre-fix its chain went 20,462 → 24,105 (worse);
post-fix 21,498 → 19,958 (**+1,540 kg better**). Same seed, same anchor.

**Verdict capture (B61) works across handlers.** Pre-fix the field was `None` for every combo except
graph_routed. Post-fix, `CONTINUE`, `RETRY`, `MAJOR_ISSUES` and `COMPLETE` are all captured —
different handlers ask for different vocabularies, which one alternation could never cover.

## Findings that hold

- **Chains that improve:** `sequential_graph_routed` (+1,788) and `orchestrated_graph_routed`
  (+1,540) — both graph_routed, both consistently. `sequential_iterative_feedback` (+7,127) and
  `sequential_staged_pipeline` (+5,317) also improved, but see the coupling caveat below.
- **Chains that regressed:** `orchestrated_staged_pipeline` (−8,662, and its optimality gap went
  20.8% → 92.4%) and `orchestrated_iterative_feedback` (−1,033).
- **`commission` is concentrated in the orchestrated structure** — 4 of the 5 commission rows.
  An agent reviewed a design missing its thresholds and approved it. That is a coordination failure
  with a distinct signature; `omission` merely means nobody checked.
- **The pipeline is deterministic.** Identical seed/anchor/design reproduces fuel to 10+ decimals
  across sweeps (observed 3 times). Differences between combos are attributable to structure rather
  than run-to-run noise, for runs that complete cleanly.
- **`sim_fail` explained:** `sequential_staged_pipeline` link 0 set `area_m2 = 61.39` against a
  declared range of 100–160 m². The mission solver could not converge. Link 1 corrected to 100.006
  and converged. Under Option A the agent genuinely owns AREA, and `set_aircraft_parameters` only
  WARNS on out-of-range values — so a doomed run is not detected for 28 minutes.

## Caveats — do not skip these

- **Coupling status is not in these tables.** It lives in the design ledger, which spans sweeps;
  joining it safely requires matching on run identity, not position. Pre-fix coupling was
  7 coupled / 3 uncoupled / 4 unmeasured (`cd_threshold_fallback` is not a measurement).
- **Δ chain is only meaningful when both links share coupling status.** Where coupling flips, the
  change is mostly the physics being added, not the design improving. Coupled runs sit at
  17,900–24,100 kg; uncoupled at 7,100–16,100.
- **Two networked link-1 runs are missing.** The networked structure is under-sampled.
- **`orchestrated_graph_routed` link 1 appears in both sweeps** (15,811 main / 19,958 rerun). The
  rerun value is used, so both its links come from identical conditions.
- **tigl died silently mid-sweep**, costing 2 combos (11 connect failures). The launcher checks all
  five ports before starting and never again. A per-run health check is not yet implemented.

## Open bugs

| id | summary |
|---|---|
| B65 | mesh repetition — RESOLVED by B64; handoff kept for the method |
| B57/B60 | orchestrated_staged_pipeline pipeline rewind — did NOT recur post-fix; may have been a B64 symptom |
| B63 | CUDA OOM with NO repetition observed post-fix → a genuine context ceiling in the networked structure; compaction now justified |
| B53/B55 | `get_design_state` still called 0 times despite the preamble hint — wording alone did not fix discovery |
| — | tigl silent death; no per-run server health check |
| — | out-of-range design values warn but do not reject (cost one 28-min `sim_fail`) |

---

# PRE-FIX RESULTS (2026-08-17) — superseded, kept for comparison

Measured while 85 optional arguments were wrongly enforced as required.

| combo | link | coupled | fuel | Δ | cons |
|---|---|---|---|---|---|
| sequential_iterative_feedback | 0/1 | F / T | 16,066 / 23,820 | −7,754 | 3/5, 2/5 |
| sequential_staged_pipeline | 0/1 | T / T | 20,380 / 20,380 | +0 | 1/5 |
| sequential_graph_routed | 0/1 | T / T | 23,666 / 21,922 | +1,745 | 2/5 |
| orchestrated_staged_pipeline | 1 | F | 14,613 | — | 4/5 |
| orchestrated_iterative_feedback | 0/1 | F / F | 8,378 / 17,870 | −9,492 | 4/5, 1/5 |
| networked_iterative_feedback | 0/1 | T / T | 9,568 / 10,215 | −647 | 4/5 |
| networked_graph_routed | 1 | F | 7,864 | — | 4/5 |

12 successes, 2 timeouts. Coupling 7 / 3 / 4-unmeasured.
