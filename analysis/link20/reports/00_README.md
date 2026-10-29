# 20-link DLR-F25 study: topic reports (index)

8 coordination combinations (Qwen3-32B, thinking off*) x 20 chained links = 160 links. Link k starts from link k-1's
end-state design. Written 2026-10-28 from ws8 `~/Jessica/avion_analysis/out_20261028/links.csv`
(`python -m analysis.link20.extract`), the raw run files, and one script per report in `scripts/`.
Every number in these reports was printed by those scripts; their tables (CSV) are in ws8
`~/Jessica/avion_analysis/reports_20261028/<topic>/`. Plots: ws8 `~/Jessica/avion_analysis/out_20261028/plots/`.

\* Orchestrated workers ran with thinking ON (see "Possible bugs" below).

| Report | What it covers |
|---|---|
| [01_physics_outcomes.md](01_physics_outcomes.md) | fuel burn, GTOW, wing mass, constraint satisfaction, mission convergence, vs the baseline |
| [02_design_parameters.md](02_design_parameters.md) | which design variables moved, end states, ranges, drift vs oscillation, recommended vs actual |
| [03_efficiency.md](03_efficiency.md) | link duration, output tokens, agent steps, where the time goes, effort vs outcome |
| [04_coordination_org.md](04_coordination_org.md) | structure / handler behaviour, orchestrator and board activity, org-theory metrics |
| [05_recommendations.md](05_recommendations.md) | integrator verdicts, recommendations, carry-over, follow-through |
| [06_tools_reliability.md](06_tools_reliability.md) | MCP calls by server / tool, refusals and failures, where the chain breaks, run reliability, B187 |
| [07_methods_answers.md](07_methods_answers.md) | Methods questionnaire for the paper: roles, coupling, mass-mcp, prompts, handlers, model settings, hardware, carry-over, baseline, recommendation scoring |
| [08_iterations_and_ladder.md](08_iterations_and_ladder.md) | per-iteration table (CSV + LaTeX rows) for all 8 combinations; recommendation ladder (actionable, feasible, effective) |

Abbreviations: S / O / N = sequential / orchestrated / networked; SP / IF / GR = staged pipeline / iterative
feedback / graph-routed. There is no networked staged pipeline.

## Headline results (details and n in each report)

- **No link met the GTOW limit** (0 of 140 flown links <= 90,000 kg; lowest 91,433.9 kg). 4 links met fuel
  <= 15,000 kg, all from unconverged missions. The unchanged baseline (18,197.0 kg fuel, 91,660.6 kg GTOW) itself
  violates GTOW. (01)
- **Mean fuel is above the baseline in every combination** (+7.4 % N_IF to +81.3 % N_GR); link 1 already starts
  ~25 % above it, after the data plane applies the first SU2 / mass / pyCycle results. Only 3 converged links beat the
  baseline (N_IF 18, N_IF 7, O_GR 8). Fuel drifts up over the chain in 7 of 8 combinations. (01)
- **Only the wing and the data-plane factors really move.** Mission, tails and taper never changed; engine cycle
  inputs only in the orchestrated combinations. Every link-20 end state has a larger engine scale (+41 % to +321 %),
  drag factor and wing mass scaler than the baseline. N_GR ended in an implausible region (area -35 %, negative sweep,
  drag factor clamped at 2.0). (02)
- **Cost:** sequential is fastest (S_SP 0.51 h per link), N_GR slowest (4.04 h per link, 72.7 h for the chain).
  Agent-step time is dominated by the model's own turns and coordination tools, not by the simulations. (03)
- **Coordination:** median steps per link order by handler (graph-routed > iterative feedback > staged pipeline) inside
  every structure. Graph-routed links almost never finish their route (3 of 60 `complete`); they stop on a budget.
  Iterative-feedback retries grow with decentralisation (S 0.55, O 2.2, N 7.3 per link). (04)
- **The integrator almost never says COMPLETE** (2 of 160 links). Advice is mostly "geometry engineer: change
  AR / wing area". Measurable follow-through is 60 % (66 of 110), with no clear relation to fuel. (05)
- **Reliability:** 9,139 agent MCP calls, 78.7 % success. Refusals are mostly the coupling contract
  (`MISSING_REQUIRED_PARAMETERS`, 413 of 941). All 20 did-not-fly links break at the mission step. (06)

## Data inventory and integrity

From `scripts/inventory.py` (ws8 `reports_20261028/inventory/inventory.csv`). "Clean flown" = completed, flew, no
B187, no B214.

| | Dataset (code) | Completed | Failed | Flew | Did not fly | Failed but flew | >1 runner attempt | B187 unsized cycle | B187 inherited only | B214 | Clean flown |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S_SP | v2full | 20 | 0 | 20 | none | none | 8, 19 | none | none | 1 | 19 |
| S_IF | v2full | 20 | 0 | 20 | none | none | 18 | none | none | none | 20 |
| S_GR | v2full | 20 | 0 | 16 | 7, 13, 14, 15 | none | none | 2, 4, 6, 7, 12, 13, 14, 17, 19 | 3, 5, 8, 16, 18, 20 | none | 4 |
| O_SP | v2full | 20 | 0 | 20 | none | none | 9, 16, 19 | none | none | none | 20 |
| O_IF | v2full | 18 | 2 (8, 19) | 19 | 8 | 19 | 4, 7, 8, 13, 16, 20 | 4 | none | none | 17 |
| O_GR | v3combos | 20 | 0 | 20 | none | none | none | 11 | none | none | 19 |
| N_IF | v3combos | 20 | 0 | 7 | 1, 2, 5, 8, 9, 11-17, 19 | none | 2, 3, 7, 15, 17 | 1, 5, 6, 9, 15, 17 | 2 | none | 6 |
| N_GR | v4net | 18 | 2 (6, 8) | 18 | 5, 11 | 6, 8 | none | 10, 14 | none | none | 14 |
| **Total** | | 156 | 4 | 140 | 20 | 3 | | | | | 119 |

- Datasets: `v2full` = ws8 `logs/stat_results/v2full_qwen_nothink_aipexws8` (2026-10-14 code, before the
  record_recommendation-loop, B214 and B187 fixes); `v3combos` = ws3 `/data/avion_runs/v3combos` (copied to ws8);
  `v4net` = ws8 `v4net_graph_qwen_nothink_aipexws8` (fixed code `integration/2026-10-21`, finished 18 + 2 failed).
  Cross-combination comparisons mix code versions.
- Failures: O_IF 8 = zero fuel after 3 attempts; O_IF 19, N_GR 6, N_GR 8 = 480-min timeout (each flew a mission
  before it, so they have a fuel, but the next link restarts from the last completed link, not from them).
- B187 refinements (06, 02): on N_GR links 10 and 14 the counted mission was not flown at scale 0.994; across the
  study only S_GR links 2 and 19 have a counted fuel flown at 0.994. The extractor's `b187` flag misses links that
  only inherit the 0.994 scale without setting it (N_IF 10, S_GR 15), so the table above slightly undercounts.
- Kept every link (Jessica, 2026-10-22): contamination is part of what is measured; reports flag it and give
  sensitivity checks where it matters (01: excluding contaminated links moves the pooled mean fuel from 27,232.9 kg,
  n = 140, to 27,299.7 kg, n = 119).
- N_IF = the finished v3combos run (Jessica, 2026-10-28: use it; it may be swapped later for the fixed-code re-run
  `v4net_iter_qwen_nothink_aipexws8`, running on ws8, 11 of 20 links on 2026-10-28). To swap: point the
  `mdo_f25_networked_iterative_feedback` entry of `SOURCES` in `extract.py` at that folder, then re-run `extract`,
  the plots and the report scripts.

## Caveats that apply to every report

- Fuel = last successful mission in the link's knowledge base (the runner's rule), with no convergence check; only
  37 of 140 flown links end on a converged mission. The baseline mission is also unconverged.
- Tokens are step-log output tokens only (result.json `total_tokens` is wrong, B215; input tokens aren't saved for
  networked). No CPU / GPU time is recorded anywhere.
- Small n (20 links per combination, one chain each): trends and correlations are descriptive.

## Possible bugs found while writing the reports (not fixed; filed as B216-B225 in `Avion/.claude/BUGS.md`)

1. **Orchestrated workers run with thinking ON** in the "nothink" study (`src/coordination/strategies/orchestrated.py`
   lines 418-420, `thinking_enabled = not is_orchestrator`). 67-86 % of orchestrated output tokens are from
   thinking-enabled generations. Affects all O_* links. (03)
2. **B187 fix gap on graph-routed:** `seed_from_design` marks the pre-init pyCycle session as sized after only
   `create_cycle_model`; N_GR link 10 took up the unsized 5,900 lbf. Not reproduced. (06)
3. **N_GR sizes the engine on a fixed Fn_DES = 25,000 lbf** (75 of its 95 successful `set_inputs`). (06)
4. **N_GR drag factor clamped at 2.0** (the data-plane upper bound) on 19 of 20 links; its SU2 CD is 0.16-0.45. (02)
5. **Role guard refuses most `record_recommendation` calls in N_GR** (249 of 277) and all in S_GR links 7, 13-15;
   some links carry no recommendation forward. (05)
6. **aviary-mcp echoes read-only `Aircraft.Wing.SPAN`** in `set_aircraft_parameters` replies; agents send it back
   (174 of 176 READ_ONLY_PARAMETER failures). (06)
7. **Wing taper ratio never changes** (tigl ignores it) although agents set it on every link. (02, 05)
8. **Org-theory metrics broken by construction:** `early_abort` false positives (41, all on valid
   `set_aircraft_parameters`), `claim_conflicts` always 0, `information_ratio` 1.0 on all O_GR links,
   `orchestrator_overhead_ratio` 0.5 on every O_SP link, `mean_attempts_to_success` counts messages. (04)
9. **Orchestrated step time double-counted** (orchestrator replies nested inside the worker's
   `message_orchestrator` step). (03)
10. **Data oddities to check:** MTOM exactly 78,126 kg on 50 of 159 link-ends; SFC 3.667 (O_GR 16) and exactly 1.0
    (N_IF 14); N_IF link 15 attempt 1 has a 30.2 h gap between two steps; `ec_converged` is True on every
    did-not-fly link (read it with `flew`). (02, 03, 01)

## Reproduce

On aipexws8, `cd ~/Jessica/avion_analysis/code && source ~/Jessica/Avion/.venv/bin/activate`, then
`python -m analysis.link20.extract --out ../out_20261028` and each report's command (last section of each report).
