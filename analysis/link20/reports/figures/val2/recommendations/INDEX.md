# Validation 2: recommendation figures (final_val2, 8 combinations x 2 iterations)

Scripts: `analysis/link20/reports/scripts/val2_rec_extract.py` (dumps every run) and
`analysis/link20/reports/scripts/val2_recommendations.py` (table + figures). Hand-traced judgements:
`analysis/link20/reports/data/val2_rec_curated.csv`. Run on aipexws8 (nice 15) from the data in
`/data/Jessica/final_val2/{runs, analysis_check/copies/ws5, analysis_check/copies/ws6, ws3_copy}`. Outputs are in ws8
`/data/Jessica/final_val2/figures/recommendations/` (the per-run dumps are in `extract/` there only) and this folder.
Every figure has PNG (300 dpi), PDF and the CSV it plots.

## Population and definitions

- **Recommendation.** A recommendation is any `record_recommendation` call (77 calls), plus 3 networked posts that carry one: 1 blackboard key `geometry_engineer_recommendation` and 2 "Recommendation:" lines in an evaluation TODO result.
- **Counting unit.** The unit is the **distinct recommendation**: same run, author, channel and text. That gives 60 recommendations from 78 calls.
  - The 2 COMPLETE verdicts (S_GR it2, O_GR it1) carry no change and are excluded.
  - Repeats of the same text by the same author are folded into one row: N_GR it1 agent_1 made 20 refused calls of 6 texts, and N_GR it2 repeated one text 5 times. `n_calls` keeps the number of calls.
- **Recorded vs refused.** The framework recorded 21 of the recommendations. 36 were refused by the role guard ("Only the mdo_integrator records the recommendation"). Any agent may call the tool, but only the integrator's call (in networked, the peer holding that role) is stored.
- **Type.**
  - **Specific:** names a design parameter, a direction and a value.
  - **Directional:** names a parameter and a direction.
  - **General:** names no design parameter, or a parameter without a direction.
  - The fine categories are hand-assigned, with `rec_params.parse` shown alongside as `auto_type`. "OEM 48831 -> 48000" counts as general, because OEM is an output and not a design parameter.
- **Carried.** The last recorded recommendation of iteration 1 is put into every iteration-2 prompt ("RECOMMENDATION from the previous iteration").
- **Reached target.** The target agent's input (system + user messages only, never its own turns or tool observations) contains the recommendation. Levels:
  - **verbatim:** in the framework block or the orchestrator's task.
  - **paraphrased:** through the author's own `respond_to_recommendation` text, which later agents see in the coordination log, or through the hand-off.
  - **via re-issue:** a refused text that the integrator then recorded word for word.
  - **run ended first:** recorded, but no agent ran afterwards (the last recommendation of iteration 2, or of a link that stopped).
  - **not reached.**
  - In networked runs, any peer counts as the target, because peers take any role.
  - Every "not reached" was checked by hand with a keyword search over the target's later prompts (see `reach_evidence`).
- **Orchestrator relay** (orchestrated runs only). Compares the first `assign_task` to the target after the recommendation with the recommendation's text. Categories:
  - **unchanged:** verbatim text plus tool instructions.
  - **reworded:** same parameter, direction and value.
  - **reworded and expanded:** same target and intent, plus added instructions.
  - **bypassed:** no new task; the iterative loop re-ran the earlier task.
  - **not relayed:** the run ended first.
  - "Why" quotes the orchestrator's visible text. Thinking was off, so in 3 of 4 relays it wrote only the tool call and no reason exists.
- **Outcome.**
  - **followed:** the design change was made (a morph with the asked direction or value) and flown.
  - **followed (not flown):** the morph was made, but the iteration ended before a mission.
  - **not followed:** the target had no lever, re-ran the same analysis, or claimed a change it did not make.
  - **not acted:** run ended, coordination stall, or not reached.
  - **duplicate:** the same content as another recommendation; its fate is that one's.
  - **no change asked:** "proceed", "run the simulation", or a result report.
- **Fuel.** The delta is the first converged mission after the change (after the first morph that followed) minus the last converged mission before the recommendation. For a carried recommendation, the "before" mission is iteration 1's last. It is reported only when the recommendation reached its target and a mission flew before the next recorded recommendation. Fuel is mission fuel burned (kg); no zero-fuel missions occurred (59 of 59 converged).

## Figures

**fig_a1_types_overall_ranked_bars**: Frequency of the fine recommendation categories, ranked, with the top 7 shown and the rest folded into "Other". Bars are coloured by type: specific, directional, general.
- Population: 60 recommendations.
- "Other" = Mass reduction goal 2, Area or AR (direction only) 2, Mission adjustment goal 2.

**fig_a2_types_per_structure_ranked_bars**: The same categories per structure, as the share of that structure's recommendations. Bar ends show counts.
- n = sequential 32, orchestrated 11, networked 17.

**fig_a3_types_per_combination_ranked_bars**: One ranked panel per combination, with counts. The top 5 categories are shown, and "Other" would hold the rest (none was needed).

**fig_a4_specificity_per_structure_stacked**: Share of specific, directional and general recommendations per structure, for all recommendations and for those recorded by the framework.
- Counts in the bars.
- Recorded: n = 9 / 7 / 5.

**fig_b1_fate_funnel_per_structure**: Counts per structure at each stage: made, recorded, reached target, followed (including "not flown"), and followed and flown with lower fuel.
- The stages are not strictly nested. Four refused recommendations reached their target in paraphrase, and one by re-issue, so "reached" can exceed "recorded" (sequential 12 > 9).

**fig_b2_fate_alluvial**: Every recommendation as one ribbon: Made -> Recorded? -> Reached? -> Orchestrator -> Outcome -> Fuel.
- Ribbons are coloured by structure.
- A ribbon stops where its fate ends: not reached, or run ended first.
- Node counts are in `fig_b2_fate_alluvial_nodes.csv`. The path of each recommendation is in `fig_b2_fate_alluvial.csv`.

**fig_c_timeline_per_combination**: One row per combination; iteration 1 on the left, iteration 2 on the right.
- x = minutes from the iteration's first logged event. The axis is symlog, linear to 30 min and logarithmic beyond, because the iterations last 15-279 min.
- Marker shape = type. Fill = outcome group.
- Vertical ticks = converged missions, in the structure's colour. The dense block in N_GR it1 is 10 identical re-flights.

**fig_d1_orchestrator_relay_kinds**: The orchestrated recommendations recorded by the framework (n = 7), by what the orchestrator did with them.

**fig_d2_orchestrator_asked_vs_assigned**: The integrator's text next to the task the orchestrator assigned, for the same 7 recommendations.

**fig_d3_orchestrator_task_similarity**: Character similarity (difflib ratio, lower-cased) between the assigned task and the recommendation, for the 4 relayed ones.
- It measures wording, not meaning. O_IF-1-02 is verbatim but scores 0.52, because the orchestrator appended tool instructions.

**fig_e_fuel_delta_followed_diverging**: Fuel change of the mission that followed each followed-and-flown recommendation (n = 12), split by type and coloured by structure.
- The dashed line marks 0.
- Excluded:
  - S_GR-1-09 was followed but not flown in iteration 1; it was re-issued and flown as S_GR-2-02.
  - O_GR-2-02 was not followed; its same-design re-flight changed fuel by 0.00 kg.
- All 12 lowered fuel, by 132-777 kg. Identical changes give identical deltas across combinations:
  - AR 9.36 -> 10: −571.12 kg in S_GR, N_IF and O_IF.
  - AR 10 -> 10.5: −351.10 kg.
  - AR 9.36 -> 9.5: −131.58 kg.
- Every followed recommendation raised aspect ratio, and S_SP and N_GR also raised the area. No recommendation that lowered AR or area was followed, so the plot says nothing about such advice.

## Tables

- `val2_recommendations.csv` / `.md`: one row per recommendation. Columns:
  - who made it: author, target, channel, time
  - text and type
  - reach and its evidence
  - orchestrator task, relay kind and reason
  - what was done
  - outcome and fuel
- `val2_recommendations_calls.csv`: one row per call (80), including the refused repeats and the 2 COMPLETE verdicts.
- `summary.txt`, `summary_by_structure.csv`, `summary_by_combo.csv`: the counts quoted above.

## Incomplete runs

- N_IF it2 ended at max cycles and N_GR it1/it2 on the stuck rule. They are included as they are.
- Their unrecorded or unacted recommendations show as "not acted (coordination stall)" or "not reached".
- No run was still running.
