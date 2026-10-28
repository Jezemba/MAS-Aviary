# 05 — Integrator verdicts, recommendations and follow-through (20-link study)

What the MDO integrator decided at the end of each link (verdict), what it recommended, which recommendation was
carried into the next link, and whether the next link acted on it. 8 combinations x 20 links = 160 links.
All numbers come from `scripts/recommendations_report.py` (tables in ws8
`~/Jessica/avion_analysis/reports_20261028/recommendations/`, full console output in `run.log` there).

Abbreviations: S/O/N = sequential / orchestrated / networked; SP/IF/GR = staged pipeline / iterative feedback /
graph-routed. Datasets: S_SP S_IF S_GR O_SP O_IF = `v2full` (pre-fix code, with the record_recommendation loop);
O_GR N_IF = `v3combos`; N_GR = `v4net` (fixed code).

## Key findings

1. **The carry-over works exactly as designed.** In all 152 link pairs (8 combinations x 19), the recommendation in
   `carry_over.json` of link k+1 is word-for-word the last recommendation recorded in link k (same text, same target
   role). So "the carried-forward recommendation" = "the last recommendation of the link" throughout this report.
2. **The integrator almost never says COMPLETE.** Over the 160 links, the carried-forward verdict is COMPLETE in only
   2 links (O_SP link 8, O_IF link 17). The final (link 20) verdict is CONTINUE in 7 of 8 combinations; O_SP link 20
   recorded no recommendation. CONTINUE dominates in sequential and networked (S_SP 19/20, S_IF 18/20, N_IF 18/20
   links); RETRY is concentrated in orchestrated (O_SP 9, O_IF 7, O_GR 5 links) and N_GR (5). All three orchestrated
   combinations open with a run of RETRY verdicts (O_SP links 1-6, O_IF and O_GR links 1-5).
3. **The pre-fix loop inflates recommendation counts, but adds little new advice.** v2full links record a mean of
   7.15-16.20 recommendations each (max 77, O_IF), against 1.30-2.45 on the fixed/v3 code. Counting distinct texts
   instead, v2full links carry 1.15-4.10 distinct recommendations on average (n=20 each). In S_SP and S_IF the first
   and last recommendation of a link are identical in 19 of 20 links: the loop repeats the same sentence (up to 10
   times). O_SP and O_IF do revise within a link (mean 4.10 and 3.85 distinct, n=20).
4. **The advice is mostly "geometry engineer: change wing aspect ratio / wing area".** The geometry engineer is the
   target in 63 of 160 carried-forward recommendations; aspect ratio is named in 51 and wing area in 36. Exceptions:
   O_GR mostly targets the propulsion analyst (12/20) and the engine scale factor (12/20); S_IF and O_IF spread their
   advice over 5-6 roles.
5. **Recommendations are followed about 60 % of the time where it can be measured, and clearly change behaviour.**
   Of 110 measurable parameter items parsed from the carried-forward recommendations, the next link moved the
   parameter the recommended way in 66 (60.0 %), left it unchanged in 33 and moved it the other way in 11. A
   recommended parameter changed in 70.0 % of link transitions (77 of 110) versus 24.7 % (551 of 2,232) when it was
   not recommended. Follow-through is highest in S_SP (20/21 items, 95.2 %) and N_GR (16/22, 72.7 %) and lowest in
   orchestrated (O_SP 40.0 %, O_IF 30.0 %, O_GR 30.8 %). By parameter: wing aspect ratio 34/38 and wing area 25/32
   followed; engine scale factor only 5/18; taper ratio 0/6 (taper ratio never changed in any link transition, 0 of
   144).
6. **Following the advice is not reliably tied to lower fuel (descriptive, small n).** When the next link followed
   every measurable item, fuel changed by a median -516.2 kg (mean +538.5 kg, n=48 link pairs where both links flew;
   25 of 48 improved); when it followed none, median +0.02 kg (mean -103.3 kg, n=30; 11 improved). The means and
   medians point in different directions because of a few large swings, and only 4 + 6 pairs have both missions
   converged. Treat as no clear evidence either way.
7. **Advice is rarely repeated word-for-word, but often repeated in substance.** Identical carried-forward text in
   consecutive links occurs in 0-3 pairs per combination. Same target role and same variable set in consecutive links
   is common in S_SP (15 of 19 pairs; one role for 11 links in a row) and N_GR (10 of 12 pairs; 8-link run) — the
   integrator keeps pushing the same knob ("reduce wing area, increase aspect ratio") link after link.
8. **N_GR loses recommendations to the role guard (suspicious, see end).** In N_GR, 249 of 277 `record_recommendation`
   calls were refused with "Only the mdo_integrator records the recommendation; you are agent_1."; 4 N_GR links
   (5, 8, 10, 11) end with no recorded recommendation despite 9-18 attempts each.

## 1. Verdicts per link

Carried-forward (last) verdict of each link. C = COMPLETE, N = CONTINUE, R = RETRY, - = no recommendation recorded.
(`last_verdict_per_link.csv`)

| | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S_SP | N | N | N | N | N | N | N | N | N | N | N | R | N | N | N | N | N | N | N | N |
| S_IF | R | N | N | N | N | N | N | R | N | N | N | N | N | N | N | N | N | N | N | N |
| S_GR | N | N | N | N | N | N | - | N | N | N | N | N | - | - | - | N | N | N | N | N |
| O_SP | R | R | R | R | R | R | N | C | N | N | N | N | N | N | R | R | N | R | N | - |
| O_IF | R | R | R | R | R | N | R | - | N | N | N | N | R | N | N | N | C | N | N | N |
| O_GR | R | R | R | R | R | N | N | N | - | N | N | N | N | N | N | N | N | N | N | N |
| N_IF | N | N | N | N | N | N | N | N | N | N | N | N | N | R | N | N | N | N | - | N |
| N_GR | R | N | N | N | - | R | N | - | R | - | - | N | N | N | N | N | R | N | R | N |

Per combination (`summary_per_combination.csv`). "Last" = carried-forward verdict (one per link); "all" = every
recorded recommendation (inflated by the loop in v2full).

| | dataset | links without any recommendation | last C / N / R | final verdict (link 20) | all C / N / R |
|---|---|---|---|---|---|
| S_SP | v2full | 0 | 0 / 19 / 1 | CONTINUE | 0 / 141 / 6 |
| S_IF | v2full | 0 | 0 / 18 / 2 | CONTINUE | 0 / 124 / 19 |
| S_GR | v2full | 4 (links 7, 13, 14, 15) | 0 / 16 / 0 | CONTINUE | 0 / 279 / 0 |
| O_SP | v2full | 1 (link 20) | 1 / 9 / 9 | none recorded | 2 / 99 / 164 |
| O_IF | v2full | 1 (link 8, failed) | 1 / 11 / 7 | CONTINUE | 3 / 166 / 155 |
| O_GR | v3combos | 1 (link 9) | 0 / 14 / 5 | CONTINUE | 0 / 33 / 16 |
| N_IF | v3combos | 1 (link 19) | 0 / 18 / 1 | CONTINUE | 0 / 26 / 3 |
| N_GR | v4net | 4 (links 5, 8, 10, 11) | 0 / 11 / 5 | CONTINUE | 0 / 18 / 8 |

The two COMPLETE verdicts carry no target role and no text, so the role/variable tables below count 14 links as
"None recorded" (12 without any recommendation + 2 COMPLETE). Plots: `out_20261028/plots/4_coordination/
verdict_{complete,continue,retry}_{avg_box,avg_strip,line_*}.png`.

## 2. Recommendations and responses recorded per link

| | recs total | recs per link, mean (n) | median | max | distinct texts per link, mean (n) | max distinct | links with first = last text | max repeats of one text in a link | responses total | responses per link, mean (n) | links with no response |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S_SP | 147 | 7.35 (n=20) | 8.5 | 10 | 1.25 (n=20) | 6 | 19 of 20 | 10 | 228 | 11.40 (n=20) | 0 |
| S_IF | 143 | 7.15 (n=20) | 7.5 | 10 | 1.15 (n=20) | 4 | 19 of 20 | 10 | 273 | 13.65 (n=20) | 0 |
| S_GR | 279 | 13.95 (n=20) | 10.0 | 39 | 1.50 (n=20) | 6 | 9 of 16 | 39 | 1006 | 50.30 (n=20) | 0 |
| O_SP | 265 | 13.25 (n=20) | 17.5 | 20 | 4.10 (n=20) | 12 | 8 of 19 | 18 | 62 | 3.10 (n=20) | 0 |
| O_IF | 324 | 16.20 (n=20) | 13.0 | 77 | 3.85 (n=20) | 15 | 6 of 19 | 38 | 107 | 5.35 (n=20) | 1 |
| O_GR | 49 | 2.45 (n=20) | 2.0 | 5 | 2.40 (n=20) | 4 | 2 of 19 | 2 | 70 | 3.50 (n=20) | 1 |
| N_IF | 29 | 1.45 (n=20) | 1.0 | 6 | 1.25 (n=20) | 3 | 14 of 19 | 3 | 42 | 2.10 (n=20) | 3 |
| N_GR | 26 | 1.30 (n=20) | 1.0 | 3 | 1.30 (n=20) | 3 | 9 of 16 | 1 | 116 | 5.80 (n=20) | 1 |

"Distinct" = distinct `recommended_change` texts in the link after lower-casing and collapsing whitespace (links with
no recommendation count as 0). Responses (`respond_to_recommendation`) are inflated too in sequential v2full
(S_GR 50.30 per link). Recording agents: `mdo_integrator` in S/O; in networked, the peers `agent_1/2/3` (N_IF) and
`agent_1` only (N_GR). Plots: `plots/4_coordination/recommendations_*.png`, `responses_*.png`.

`record_recommendation` calls in the step logs, by the tool's reply (`record_recommendation_calls.csv`):

| | calls | recorded | refused: not the integrator | refused: already recorded |
|---|---|---|---|---|
| S_SP | 234 | 147 | 87 | 0 |
| S_IF | 250 | 143 | 107 | 0 |
| S_GR | 633 | 279 | 354 | 0 |
| O_SP | 294 | 265 | 29 | 0 |
| O_IF | 367 | 324 | 43 | 0 |
| O_GR | 136 | 49 | 74 | 13 |
| N_IF | 90 | 29 | 53 | 8 |
| N_GR | 277 | 26 | 249 | 2 |

"Recorded" matches the design.json count in every combination. The "already recorded" guard exists only in the newer
code (0 refusals of that kind in v2full, consistent with the loop). Non-integrator roles try to record a
recommendation often (S_GR 354 refusals).

## 3. Who and what the recommendations target

Carried-forward recommendation by target role (links, of 20; `carried_rec_by_role.csv`,
`plots/6_recommendations/recommendations_by_role_<combo>.png`):

| role | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR | total |
|---|---|---|---|---|---|---|---|---|---|
| Geometry engineer | 17 | 5 | 2 | 4 | 5 | 2 | 13 | 15 | 63 |
| Propulsion analyst | 2 | 0 | 0 | 7 | 2 | 12 | 1 | 0 | 24 |
| Structures analyst | 0 | 6 | 3 | 5 | 5 | 2 | 0 | 0 | 21 |
| Mission architect | 0 | 4 | 7 | 1 | 4 | 1 | 1 | 0 | 18 |
| Aerodynamics analyst | 0 | 5 | 4 | 1 | 1 | 2 | 2 | 1 | 16 |
| None recorded | 0 | 0 | 4 | 2 | 2 | 1 | 1 | 4 | 14 |
| Simulation executor | 1 | 0 | 0 | 0 | 1 | 0 | 2 | 0 | 4 |

Counting every recorded recommendation instead (`all_rec_signals_by_role.csv`, inflated in v2full) shifts S_GR to the
mission architect (138 of 279) and O_SP to the propulsion analyst (95 of 265); the carried-forward view above is the
one that reaches the next link.

Carried-forward recommendation by design variable (keyword map in `analysis/link20/recommendations.py`; one
recommendation can name several; `carried_rec_by_variable.csv`, `plots/6_recommendations/
recommendations_by_variable_<combo>.png`):

| variable | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR | total |
|---|---|---|---|---|---|---|---|---|---|
| Wing aspect ratio | 16 | 3 | 2 | 5 | 6 | 0 | 7 | 12 | 51 |
| Wing area | 7 | 6 | 1 | 0 | 0 | 0 | 7 | 15 | 36 |
| Engine scale factor | 0 | 0 | 5 | 3 | 2 | 12 | 0 | 0 | 22 |
| Wing mass / mass scaler | 0 | 5 | 3 | 3 | 4 | 1 | 2 | 0 | 18 |
| Engine cycle (BPR, PRs, T4, SFC, thrust) | 2 | 0 | 0 | 6 | 1 | 4 | 1 | 0 | 14 |
| None recorded | 0 | 0 | 4 | 2 | 2 | 1 | 1 | 4 | 14 |
| Aero coefficients (CL, CD) | 0 | 4 | 4 | 1 | 0 | 2 | 0 | 0 | 11 |
| Aircraft mass (OEM, MTOM) | 0 | 0 | 0 | 2 | 5 | 4 | 0 | 0 | 11 |
| Run or validate only | 0 | 1 | 0 | 3 | 4 | 1 | 2 | 0 | 11 |
| Optimizer settings | 1 | 6 | 1 | 0 | 1 | 0 | 1 | 0 | 10 |
| Wing taper ratio | 0 | 3 | 1 | 2 | 2 | 2 | 0 | 0 | 10 |
| Mission (range, Mach, trajectory, reserves) | 0 | 1 | 2 | 1 | 3 | 1 | 0 | 0 | 8 |
| Wing sweep | 0 | 1 | 2 | 1 | 0 | 0 | 2 | 0 | 6 |
| Other | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 1 |

## 4. Distinct vs repeated advice across links (`repetition.csv`)

| | links with carried text | distinct carried texts | consecutive pairs (both have text) | identical text | same role | same variable set | same role and variables | longest same-role run (links) |
|---|---|---|---|---|---|---|---|---|
| S_SP | 20 | 19 | 19 | 1 | 16 | 15 | 15 | 11 |
| S_IF | 20 | 19 | 19 | 1 | 9 | 7 | 6 | 5 |
| S_GR | 16 | 13 | 13 | 3 | 8 | 6 | 6 | 5 |
| O_SP | 18 | 18 | 16 | 0 | 5 | 1 | 1 | 3 |
| O_IF | 18 | 18 | 15 | 0 | 2 | 1 | 0 | 2 |
| O_GR | 19 | 19 | 17 | 0 | 8 | 4 | 4 | 5 |
| N_IF | 19 | 18 | 17 | 0 | 8 | 3 | 3 | 5 |
| N_GR | 16 | 12 | 12 | 2 | 11 | 10 | 10 | 8 |

Most repeated carried-forward texts (verbatim):
- S_GR, 4 links: "Reduce Aircraft.Engine.SCALE_FACTOR further to lower GTOW and fuel burn"
- N_GR, 4 links: "Reduce wing area further and increase wing aspect ratio to improve aerodynamic efficiency and reduce fuel burn"
- S_SP, 2 links: "Increase wing area further or adjust aspect ratio to reduce fuel burn and GTOW"
- S_IF, 2 links: "Reduce cruise lift coefficient closer to SU2's target CL (~0.56) to align aerodynamic conditions and improve Aviary optimizer convergence"
- N_IF, 2 links: "Increase wing area to reduce fuel burn"

The orchestrated combinations change their advice the most from link to link (O_IF: same role in 2 of 15 pairs).

## 5. Follow-through: carried-forward recommendation vs what the next link changed

Method. The carried-forward recommendation of link k is parsed into parameter items (`rec_params.parse`, the same
items as `out_20261028/recommended_changes.csv`; 116 items, identical to a fresh parse). Wanted direction = the
item's direction, or sign(target - value at end of link k). Actual change = parameter at end of link k+1 minus end of
link k (links.csv). |relative change| < 0.1 % = unchanged. "Reached" (value / range / bound items only) = link k+1
value inside the target range widened by 1 %, or on the right side of the bound. Recommendations naming no plotted
parameter (CD, mission, optimizer, "validate", ...) or naming a parameter with no direction can't be scored.

How many carried-forward recommendations (links 1-19) can be scored:

| | carried recs with text | name >= 1 plotted parameter | items |
|---|---|---|---|
| S_SP | 19 | 18 | 21 |
| S_IF | 19 | 10 | 10 |
| S_GR | 15 | 8 | 8 |
| O_SP | 18 | 12 | 15 |
| O_IF | 17 | 10 | 12 |
| O_GR | 18 | 12 | 13 |
| N_IF | 18 | 10 | 11 |
| N_GR | 15 | 15 | 26 |

Per item (`follow_through_by_combination.csv`):

| | followed | unchanged | opposite | not measurable | followed % of measurable (n) | target items reached |
|---|---|---|---|---|---|---|
| S_SP | 20 | 1 | 0 | 0 | 95.2 % (n=21) | 11 of 11 |
| S_IF | 7 | 3 | 0 | 0 | 70.0 % (n=10) | 10 of 10 |
| S_GR | 3 | 5 | 0 | 0 | 37.5 % (n=8) | 1 of 2 |
| O_SP | 6 | 7 | 2 | 0 | 40.0 % (n=15) | 4 of 14 |
| O_IF | 3 | 5 | 2 | 2 | 30.0 % (n=10) | 2 of 8 |
| O_GR | 4 | 6 | 3 | 0 | 30.8 % (n=13) | 3 of 13 |
| N_IF | 7 | 4 | 0 | 0 | 63.6 % (n=11) | 3 of 4 |
| N_GR | 16 | 2 | 4 | 4 | 72.7 % (n=22) | 2 of 11 |
| all | 66 | 33 | 11 | 6 | 60.0 % (n=110) | 36 of 73 |

Not measurable: the parameter is missing on one side of the pair (O_IF links 19-20 and N_GR links 7-8, next to the
failed links O_IF 19 and N_GR 6 and 8, which left no end-state parameters), or the item has no direction.

Per parameter (measurable items; `follow_through_by_param.csv`):

| parameter | followed | unchanged | opposite |
|---|---|---|---|
| Wing aspect ratio | 34 | 2 | 2 |
| Wing area | 25 | 5 | 2 |
| Engine scale factor | 5 | 8 | 5 |
| Wing taper ratio | 0 | 6 | 0 |
| Mission design CL | 0 | 3 | 0 |
| Wing mass scaler | 1 | 2 | 0 |
| BPR | 1 | 3 | 0 |
| fan PR | 0 | 2 | 0 |
| hpc PR | 0 | 2 | 0 |
| T4 max | 0 | 0 | 1 |
| Wing sweep | 0 | 0 | 1 |

Control (`change_base_rate.csv`): over all combinations, a parameter changed (>= 0.1 %) in 77 of 110 recommended
transitions (70.0 %) and 551 of 2,232 not-recommended ones (24.7 %). Wing area: 27 of 32 recommended vs 16 of 112 not;
aspect ratio: 36 of 38 vs 21 of 106. Some parameters move often without being asked (mission CL 91 of 142, drag-factor
93 of 145, scale factor 85 of 126 not-recommended transitions) — a "followed" item there is weaker evidence.

Per receiving link (all measurable items followed / some / none; `follow_through_links.csv`):

| | followed | partly | not followed |
|---|---|---|---|
| S_SP | 17 | 0 | 1 |
| S_IF | 7 | 0 | 3 |
| S_GR | 3 | 0 | 5 |
| O_SP | 5 | 1 | 6 |
| O_IF | 3 | 0 | 5 |
| O_GR | 3 | 1 | 8 |
| N_IF | 7 | 0 | 3 |
| N_GR | 9 | 1 | 3 |
| all | 54 | 3 | 34 |

Did following correlate with fuel? Fuel change from link k to k+1 (kg; negative = less fuel), only pairs where both
links flew (`follow_vs_fuel.csv`):

| subset | next link | n | mean (kg) | median (kg) | pairs with less fuel |
|---|---|---|---|---|---|
| both flew | followed | 48 | +538.5 | -516.2 | 25 |
| both flew | not followed | 30 | -103.3 | +0.02 | 11 |
| both flew | partly | 3 | +2976.7 | +3311.0 | 1 |
| both flew and converged | followed | 4 | -6932.1 | -9264.5 | 3 |
| both flew and converged | not followed | 6 | -94.9 | +5.4 | 2 |
| both flew, receiving link not B187 | followed | 43 | -80.3 | -948.6 | 23 |
| both flew, receiving link not B187 | not followed | 27 | +145.0 | +0.00 | 10 |

Per combination (both flew; mean / median kg, n): S_SP followed +496.4 / +1504.3 (n=17), not followed +7.3 (n=1);
S_IF followed +447.3 / +789.8 (n=7), not followed 0.0 / 0.0 (n=3); S_GR followed +1979.3 / 0.0 (n=3), not followed
-1769.0 / 0.0 (n=4); O_SP followed +5042.7 / -83.8 (n=5), not followed -2136.1 / -318.4 (n=6); O_IF followed
-2908.8 / -7395.6 (n=3), not followed +683.8 / +1082.5 (n=5); O_GR followed -6645.8 / -7050.5 (n=3), not followed
+2160.9 / +28.4 (n=8); N_IF followed -1155.5 (n=1), not followed +1971.0 (n=1); N_GR followed +1438.3 / -3823.4
(n=9), not followed -2946.0 (n=2). In S_SP, which follows almost everything, fuel tends to rise after following
(median +1504.3 kg, n=17): the advice is followed but does not make the design better.

Plots: recommended vs actual parameters `plots/3b_recommended_parameters/rec_params_all.png`,
`rec_params_<combo>.png`; recommended-change heatmaps `rec_change_heatmap_<combo>.png` (compare with
`plots/3_design_parameters/param_change_heatmap_<combo>.png`).

## 6. One typical carried-forward recommendation per combination (verbatim)

Picked by the script: the first link (1-19) whose carried-forward recommendation targets the combination's most common
role and names its most common variable (`examples.csv`). The next link received each one unchanged.

| | link | verdict, to | text | what link k+1 did |
|---|---|---|---|---|
| S_SP | 2 | CONTINUE, geometry_engineer | "Wing1 aspect_ratio 9.36 -> increase to 10.5" | aspect ratio 9.3632 -> 10.5074 (followed) |
| S_IF | 1 | RETRY, mission_architect | "Increase Aviary optimizer's maximum iterations from 200 to 500 to allow better convergence" | not scorable (no plotted parameter) |
| S_GR | 8 | CONTINUE, mission_architect | "Adjust Aircraft.Engine.SCALE_FACTOR to reduce GTOW and fuel burn" | not scorable (no direction) |
| O_SP | 6 | RETRY, propulsion_analyst | "hpc.PR 9.37 -> 8.5" | hpc PR 9.37 -> 9.369 (unchanged) |
| O_IF | 6 | CONTINUE, geometry_engineer | "Adjust Aircraft.Wing.ASPECT_RATIO from 8.49 to 9.5" | aspect ratio 8.4929 -> 9.508 (followed) |
| O_GR | 1 | RETRY, propulsion_analyst | "Reduce engine SCALE_FACTOR from 1.35 to 1.20 to decrease GTOW and improve MTOM constraint margin" | scale factor 1.3543 -> 1.6358 (opposite) |
| N_IF | 3 | CONTINUE, geometry_engineer | "Reduce wing area or increase wing aspect ratio to reduce wing mass" | area 125.863 and aspect ratio 9.7589 both unchanged |
| N_GR | 1 | RETRY, geometry_engineer | "Increase wing aspect ratio further and reduce wing area to improve aerodynamic efficiency" | aspect ratio 13.045 -> 16.003, area 122.2938 -> 99.9805 (followed) |

S_IF: no carried recommendation combines its most common role (structures analyst) with its most common variable
(optimizer settings), so the script falls back to the first carried recommendation.

## Caveats

- **Code versions differ.** v2full (S_SP S_IF S_GR O_SP O_IF) has the record_recommendation loop: recommendation and
  response counts are not comparable with O_GR / N_IF (v3combos) or N_GR (v4net). Use carried-forward or distinct
  counts for cross-dataset comparisons.
- Follow-through compares link end-states, not the actions inside link k+1: a parameter can move the recommended way
  for other reasons (see the control above), and "unchanged" can mean the change was attempted and refused or reverted.
- The parser (`rec_params.py`) and variable map (`recommendations.py`) are literal keyword rules on free text written
  by the model; 116 items from 152 carried texts, many recommendations can't be scored (S_IF 10 of 19, S_GR 8 of 15).
  Model unit labels are taken as written (T4 in K vs degR).
- The 0.1 % "unchanged" and 1 % "reached" tolerances are choices made here.
- Fuel: last successful mission per link, no convergence check (B102); unconverged missions can show unrealistically
  low fuel, which is why the converged-only subset is shown (n=4 and 6). B187 links are included except in the
  "not B187" row. Failed links: O_IF 8, 19; N_GR 6, 8 (N_GR link 6 still recorded recommendations).
- N_IF is the v3combos run; a fixed-code N_IF re-run is in progress on ws8 and not used here.
- Small n everywhere (at most 19 pairs per combination); nothing here is a significance test.

## Suspicious / for Jessica

- **N_GR role guard.** 249 of 277 `record_recommendation` calls refused as "not the integrator" (the same peer,
  agent_1, is sometimes allowed and sometimes refused, presumably depending on whether it holds the evaluation TODO at
  that moment). Links 5, 8, 10, 11 end with no recommendation after 9, 15, 18 and 9 attempts, so links 6, 9, 11, 12
  start without one. Worth checking whether the guard should key on the TODO the peer just finished.
- **S_GR links 7, 13, 14, 15** have no recommendation; every call (21, 17, 26, 7) came from a non-integrator role and was
  refused — the integrator either did not run or did not record.
- **Taper ratio never changes** (0 of 144 transitions, 6 of them recommended) — possibly not settable in practice.
- In v2full, the response counts (S_GR 1006) are as inflated as the recommendation counts.

## How to reproduce

On aipexws8: `source ~/Jessica/Avion/.venv/bin/activate; cd ~/Jessica/avion_analysis/code; PYTHONPATH=. python
analysis/link20/reports/scripts/recommendations_report.py --links ../out_20261028/links.csv --items
../out_20261028/recommended_changes.csv --out ../reports_20261028/recommendations`
