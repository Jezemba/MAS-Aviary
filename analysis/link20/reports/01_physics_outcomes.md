# 01 Physics outcomes: fuel burn, GTOW, wing mass, constraints, convergence

20-link chained DLR-F25 study, 8 combinations (S_SP S_IF S_GR O_SP O_IF O_GR N_IF N_GR), 160 links.
Baseline (unchanged anchor design, flown 2026-10-22, SLSQP not converged): fuel 18,197.0 kg, GTOW 91,660.6 kg,
wing mass 8,815.2 kg. Limits from the prompts: fuel burn <= 15,000 kg, GTOW <= 90,000 kg.
Every number below comes from `scripts/physics.py` (tables in ws8 `~/Jessica/avion_analysis/reports_20261028/physics/`).
"Flown" = a successful mission above 100 kg fuel; averages of flight outputs use flown links only. "Converged" =
the `converged` flag of the exact mission the link's fuel comes from (last successful mission in the knowledge base),
read from the raw `knowledge_base.jsonl`.

## Key findings

- **No link met both limits, and none met the GTOW limit.** 0 of 140 flown links have GTOW <= 90,000 kg (lowest
  91,433.9 kg, N_GR link 16). 4 links meet fuel <= 15,000 kg (S_SP 11; N_GR 9, 15, 16), and all 4 come from
  unconverged missions. With converged missions only (n = 37), 0 links meet either limit.
- **Mean fuel is above the baseline in every combination**, from +1,354.7 kg (+7.4 %, N_IF, n = 7 flown) to
  +14,801.9 kg (+81.3 %, N_GR, n = 18). Pooled over all flown links: mean 27,232.9 kg (n = 140). The link-20 fuel is
  above the baseline in all 8 combinations (+3,626.5 kg O_IF to +15,824.9 kg O_SP).
- **The fuel results below the baseline are mostly unconverged and the lowest are not credible.** 11 of 140 flown
  links burn less than 18,197.0 kg; 8 of them are unconverged missions. The lowest, N_GR link 9 at 6,668.9 kg
  (-63.4 %), comes from an SLSQP run that stopped on "Positive directional derivative for linesearch" with 9
  violated constraints, a GTOW of 108,394.9 kg and a subsonic drag factor of 2.0 in the mission. Only 3 converged
  links beat the baseline: N_IF 18 (17,520.5 kg, -676.5 kg / -3.7 %), N_IF 7 (17,845.7 kg) and O_GR 8 (18,125.8 kg,
  -71.2 kg / -0.4 %).
- **Link 1 already starts about 25 % above the baseline.** The first mission of link 1 (after the agents' initial
  parameter set-up) burns 22,663.5 kg (O_GR, +24.5 %) to 23,077.5 kg (S_IF, +26.8 %); N_IF link 1 did not fly. The
  chain therefore effectively starts from a design much worse than the baseline, and most combinations never get
  back below it.
- **Designs drift to more fuel over the 20 links.** Mean of links 11-20 minus mean of links 1-10 is positive in
  7 of 8 combinations (from +850.4 kg S_IF to +6,628.2 kg O_SP); only N_GR goes down (-2,382.5 kg). The OLS slope
  of fuel on link number is positive in 6 of 8 (largest O_SP +684.0 kg/link; negative O_IF -26.7 and N_GR
  -43.9 kg/link).
- **Mission convergence is low and uneven:** 37 of 140 flown links converged. Share per combination runs from
  0 % (N_GR, 0 of 18) and 5 % (S_IF, 1 of 20) to 55 % (O_GR, 11 of 20). Restricting to converged links leaves too
  few links for S_IF (n = 1), S_GR (n = 2) and N_GR (n = 0) to compare.
- **Most handlers fly one design per link.** In S_SP, S_IF, O_SP and N_IF every flown link has exactly one distinct
  mission fuel (the extra "missions" are `get_results` re-reading the same `run_simulation`), so first and final
  fuel are identical on every link. Only graph-routed and N_GR try several designs within a link: N_GR flies
  more than one distinct design on 14 of 18 links, and ends lower than its first mission on 6 links, higher on 8.
- **Contamination barely moves the headline.** Excluding failed, B187 and B214 links changes the pooled mean fuel
  from 27,232.9 kg (n = 140) to 27,299.7 kg (n = 119); it mostly removes S_GR (16 -> 4 flown links).

## Fuel burn per combination (kg)

Baseline 18,197.0 kg. Mean / median / std / min / max over flown links. "Best" = lowest-fuel flown link.
Trend: first-10 = mean of flown links 1-10, last-10 = mean of flown links 11-20, slope = least-squares slope of
fuel on link number over flown links (numpy `polyfit`, degree 1).

| Combo | Dataset | n flown / 20 | Mean | Median | Std | Min | Max | Mean vs base | Link 20 | Link 20 vs base |
|---|---|---|---|---|---|---|---|---|---|---|
| S_SP | v2full | 20 | 25,071.0 | 25,143.4 | 6,522.2 | 11,823.8 | 36,414.7 | +6,874.0 (+37.8 %) | 31,501.4 | +13,304.4 (+73.1 %) |
| S_IF | v2full | 20 | 22,021.8 | 21,850.5 | 1,442.6 | 19,355.3 | 26,154.7 | +3,824.8 (+21.0 %) | 26,154.7 | +7,957.7 (+43.7 %) |
| S_GR | v2full | 16 | 25,456.3 | 27,919.7 | 3,492.7 | 19,280.7 | 28,290.9 | +7,259.3 (+39.9 %) | 28,161.9 | +9,964.9 (+54.8 %) |
| O_SP | v2full | 20 | 31,908.0 | 34,625.9 | 6,315.8 | 18,520.8 | 41,137.6 | +13,711.0 (+75.3 %) | 34,021.9 | +15,824.9 (+87.0 %) |
| O_IF | v2full | 19 | 25,003.1 | 23,553.8 | 6,585.0 | 17,302.2 | 41,808.1 | +6,806.1 (+37.4 %) | 21,823.5 | +3,626.5 (+19.9 %) |
| O_GR | v3combos | 20 | 30,969.4 | 32,892.7 | 5,573.2 | 18,125.8 | 40,918.3 | +12,772.4 (+70.2 %) | 22,848.4 | +4,651.4 (+25.6 %) |
| N_IF | v3combos | 7 | 19,551.7 | 18,452.1 | 2,659.7 | 17,520.5 | 25,196.4 | +1,354.7 (+7.4 %) | 25,196.4 | +6,999.4 (+38.5 %) |
| N_GR | v4net | 18 | 32,998.9 | 34,737.4 | 15,100.6 | 6,668.9 | 54,356.5 | +14,801.9 (+81.3 %) | 29,510.6 | +11,313.6 (+62.2 %) |

| Combo | Best link | Best fuel | Best vs base | Best converged? | Links below base (n) | First-10 mean (n) | Last-10 mean (n) | Last-10 minus first-10 | Slope (kg/link) |
|---|---|---|---|---|---|---|---|---|---|
| S_SP | 11 | 11,823.8 | -6,373.2 (-35.0 %) | no | 11, 12, 13 (3) | 24,348.9 (10) | 25,793.2 (10) | +1,444.3 | +401.2 |
| S_IF | 3 | 19,355.3 | +1,158.3 (+6.4 %) | no | none (0) | 21,596.6 (10) | 22,447.0 (10) | +850.4 | +102.3 |
| S_GR | 19 | 19,280.7 | +1,083.7 (+6.0 %) | no | none (0) | 24,335.3 (9) | 26,897.7 (7) | +2,562.4 | +259.3 |
| O_SP | 4 | 18,520.8 | +323.8 (+1.8 %) | no | none (0) | 28,593.9 (10) | 35,222.1 (10) | +6,628.2 | +684.0 |
| O_IF | 6 | 17,302.2 | -894.8 (-4.9 %) | no | 6 (1) | 24,431.7 (9) | 25,517.3 (10) | +1,085.6 | -26.7 |
| O_GR | 8 | 18,125.8 | -71.2 (-0.4 %) | yes | 8 (1) | 29,573.3 (10) | 32,365.6 (10) | +2,792.3 | +246.2 |
| N_IF | 18 | 17,520.5 | -676.5 (-3.7 %) | yes | 7, 18 (2) | 18,828.9 (5) | 21,358.5 (2) | +2,529.5 | +182.8 |
| N_GR | 9 | 6,668.9 | -11,528.1 (-63.4 %) | no | 4, 9, 15, 16 (4) | 34,190.1 (9) | 31,807.6 (9) | -2,382.5 | -43.9 |

O_IF is the one case where the two trend measures disagree in sign (last-10 mean higher, slope slightly negative):
its high values sit at links 10-11 (40,172 and 41,808 kg in `links_physics.csv`) and its last links are lower.

By structure (pooled, flown links): sequential 24,092.1 kg (n = 56), orchestrated 29,366.2 kg (n = 59), networked
29,233.6 kg (n = 25). By handler: iterative feedback 22,877.3 kg (n = 46), staged pipeline 28,489.5 kg (n = 40),
graph-routed 30,012.4 kg (n = 54). These pool different code versions (see Caveats), and N_IF contributes only 7
flown links to "networked" and "iterative feedback".

Plots: `out_20261028/plots/1_fuel/fuel_burn_line_panels.png` (and `_linestyle`, `_marker` variants),
`1_fuel/fuel_burn_avg_box.png`, `1_fuel/fuel_burn_avg_strip.png`.

## GTOW per combination (kg)

Baseline 91,660.6 kg (already above the 90,000 kg limit).

| Combo | n flown | Mean | Median | Std | Min (link) | Max | Mean vs base | Link 20 | Below base (n) | First-10 / last-10 mean | Slope (kg/link) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S_SP | 20 | 107,516.1 | 105,742.5 | 8,638.8 | 92,676.3 (11) | 122,774.2 | +15,855.5 (+17.3 %) | 118,356.7 | 0 | 105,804.2 / 109,228.0 | +666.6 |
| S_IF | 20 | 102,702.0 | 102,635.1 | 2,041.2 | 97,983.6 (18) | 109,382.0 | +11,041.4 (+12.0 %) | 109,382.0 | 0 | 102,399.0 / 103,005.1 | +96.1 |
| S_GR | 16 | 109,476.5 | 114,045.4 | 6,488.6 | 95,958.4 (2) | 115,047.6 | +17,815.9 (+19.4 %) | 113,992.9 | 0 | 107,615.2 / 111,869.5 | +460.6 |
| O_SP | 20 | 116,284.7 | 120,997.3 | 10,102.6 | 94,093.5 (4) | 129,217.4 | +24,624.1 (+26.9 %) | 118,886.7 | 0 | 111,003.9 / 121,565.4 | +1,062.5 |
| O_IF | 19 | 109,389.4 | 108,239.1 | 12,892.2 | 95,406.4 (6) | 144,548.8 | +17,728.8 (+19.3 %) | 102,265.7 | 0 | 108,866.2 / 109,860.2 | -99.1 |
| O_GR | 20 | 117,866.1 | 121,102.6 | 9,689.3 | 96,128.0 (8) | 135,622.8 | +26,205.5 (+28.6 %) | 105,873.2 | 0 | 115,452.9 / 120,279.2 | +471.4 |
| N_IF | 7 | 96,281.1 | 94,349.0 | 6,136.9 | 91,700.7 (18) | 109,692.9 | +4,620.5 (+5.0 %) | 109,692.9 | 0 | 94,514.8 / 100,696.8 | +448.1 |
| N_GR | 18 | 130,263.3 | 130,488.6 | 22,473.8 | 91,433.9 (16) | 163,886.6 | +38,602.7 (+42.1 %) | 121,990.9 | 1 (link 16) | 133,495.3 / 127,031.3 | -100.0 |

Only one flown link in the study has a GTOW below the baseline (N_GR 16, 91,433.9 kg, -226.7 kg), and it is an
unconverged mission. Plots: `out_20261028/plots/2_constraints/constraint_gtow_line_panels.png`,
`2_constraints/constraint_gtow_avg_box.png`.

## Wing mass per combination (kg)

Baseline 8,815.2 kg.

| Combo | n flown | Mean | Median | Std | Min (link) | Max | Mean vs base | Link 20 | Below base (links) | Slope (kg/link) |
|---|---|---|---|---|---|---|---|---|---|---|
| S_SP | 20 | 11,670.7 | 11,587.3 | 1,177.2 | 9,466.2 (11) | 13,717.7 | +2,855.5 (+32.4 %) | 13,035.9 | none | +86.4 |
| S_IF | 20 | 11,381.2 | 11,356.4 | 339.8 | 10,613.5 (3) | 12,559.2 | +2,566.0 (+29.1 %) | 12,559.2 | none | +21.5 |
| S_GR | 16 | 12,824.9 | 13,231.1 | 851.5 | 11,436.3 (2) | 14,631.2 | +4,009.7 (+45.5 %) | 14,631.2 | none | +86.1 |
| O_SP | 20 | 12,875.6 | 13,083.0 | 1,090.8 | 10,656.9 (4) | 14,820.6 | +4,060.4 (+46.1 %) | 13,099.2 | none | +100.5 |
| O_IF | 19 | 12,820.1 | 12,593.7 | 1,412.4 | 10,390.4 (6) | 16,386.2 | +4,004.9 (+45.4 %) | 12,340.3 | none | +46.2 |
| O_GR | 20 | 13,841.1 | 14,175.2 | 1,212.5 | 11,333.0 (1) | 15,989.6 | +5,025.9 (+57.0 %) | 12,931.2 | none | +102.5 |
| N_IF | 7 | 10,840.1 | 10,763.5 | 1,132.1 | 8,995.2 (18) | 12,759.2 | +2,024.9 (+23.0 %) | 12,759.2 | none | +8.1 |
| N_GR | 18 | 11,757.0 | 11,573.6 | 2,621.8 | 7,711.2 (16) | 16,060.5 | +2,941.8 (+33.4 %) | 8,438.1 | 15, 16, 20 | -164.6 |

Wing mass is above the baseline on every flown link except N_GR 15, 16 and 20 (all unconverged, as is every N_GR
mission).

## Constraint satisfaction (flown links, measured outputs vs prompt limits)

| Combo | n flown | Fuel <= 15,000 kg | GTOW <= 90,000 kg | Both | Fuel-OK links that converged | Lowest GTOW (kg) |
|---|---|---|---|---|---|---|
| S_SP | 20 | 1 (link 11) | 0 | 0 | 0 | 92,676.3 |
| S_IF | 20 | 0 | 0 | 0 | 0 | 97,983.6 |
| S_GR | 16 | 0 | 0 | 0 | 0 | 95,958.4 |
| O_SP | 20 | 0 | 0 | 0 | 0 | 94,093.5 |
| O_IF | 19 | 0 | 0 | 0 | 0 | 95,406.4 |
| O_GR | 20 | 0 | 0 | 0 | 0 | 96,128.0 |
| N_IF | 7 | 0 | 0 | 0 | 0 | 91,700.7 |
| N_GR | 18 | 3 (links 9, 15, 16) | 0 | 0 | 0 | 91,433.9 |

Plots: `out_20261028/plots/2_constraints/constraint_fuel_burn_line_panels.png`,
`2_constraints/constraint_fuel_burn_avg_box.png`, `2_constraints/constraint_gtow_line_panels.png`.

## Mission convergence

| Combo | n flown | Converged (n, %) | Converged links | Mean fuel, converged (n) | Mean fuel, unconverged | Min fuel, converged (link) | Below base: converged / unconverged |
|---|---|---|---|---|---|---|---|
| S_SP | 20 | 5 (25.0 %) | 4 5 9 17 18 | 27,302.5 (5) | 24,327.2 | 23,553.6 (4) | 0 / 3 |
| S_IF | 20 | 1 (5.0 %) | 18 | 20,740.5 (1) | 22,089.3 | 20,740.5 (18) | 0 / 0 |
| S_GR | 16 | 2 (12.5 %) | 1 18 | 25,244.1 (2) | 25,486.6 | 22,810.5 (1) | 0 / 0 |
| O_SP | 20 | 5 (25.0 %) | 7 8 9 19 20 | 30,022.3 (5) | 32,536.6 | 26,952.6 (9) | 0 / 0 |
| O_IF | 19 | 10 (52.6 %) | 10 12-20 | 25,353.8 (10) | 24,613.5 | 18,807.4 (19) | 0 / 1 |
| O_GR | 20 | 11 (55.0 %) | 6 8 10 12 14-20 | 30,351.1 (11) | 31,725.2 | 18,125.8 (8) | 1 / 0 |
| N_IF | 7 | 3 (42.9 %) | 7 18 20 | 20,187.5 (3) | 19,074.7 | 17,520.5 (18) | 2 / 0 |
| N_GR | 18 | 0 (0.0 %) | none | n/a (0) | 32,998.9 | n/a | 0 / 4 |

All flown links: 37 of 140 converged; mean fuel of converged links 27,184.2 kg (n = 37), median 27,239.2 kg.

The unconverged missions stop on one of two SLSQP messages, "Iteration limit reached" or "Positive directional
derivative for linesearch", with 9-10 violated constraints on every below-baseline unconverged link
(`below_baseline_links.csv`). Below-baseline links, lowest first:

| Combo | Link | Fuel (kg) | GTOW (kg) | Converged | Optimizer message | Violated constraints | Drag factor applied |
|---|---|---|---|---|---|---|---|
| N_GR | 9 | 6,668.9 | 108,394.9 | no | Positive directional derivative for linesearch | 9 | 2.0 |
| N_GR | 16 | 8,354.9 | 91,433.9 | no | Positive directional derivative for linesearch | 9 | 2.0 |
| N_GR | 15 | 9,505.6 | 93,126.4 | no | Iteration limit reached | 9 | 2.0 |
| S_SP | 11 | 11,823.8 | 92,676.3 | no | Positive directional derivative for linesearch | 9 | 1.61 |
| S_SP | 13 | 16,563.4 | 97,388.7 | no | Positive directional derivative for linesearch | 9 | 1.50 |
| S_SP | 12 | 16,585.6 | 97,400.0 | no | Iteration limit reached | 9 | 1.49 |
| O_IF | 6 | 17,302.2 | 95,406.4 | no | Positive directional derivative for linesearch | 9 | 1.09 |
| N_IF | 18 | 17,520.5 | 91,700.7 | yes | - | - | 2.0 |
| N_GR | 4 | 17,562.3 | 105,914.1 | no | Positive directional derivative for linesearch | 10 | 2.0 |
| N_IF | 7 | 17,845.7 | 93,308.2 | yes | - | - | not recorded |
| O_GR | 8 | 18,125.8 | 96,128.0 | yes | - | - | 0.95 |

"Drag factor applied" is `Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR` from the link's `design.json`
(`mission_applied`); for N_GR 9 the same 2.0 is also in that mission's own `get_results` design parameters. A
mission flying the doubled-drag design at 6,668.9 kg (about a third of the baseline) with 9 violated constraints is
not a physical result; the sub-15,000 kg fuels should not be reported as designs that meet the fuel limit.

## Did-not-fly, failed and contaminated links

| Combo | Did not fly | Failed (still flew) | B187 unsized cycle | B187 inherited only | B214 | Contaminated (flown) |
|---|---|---|---|---|---|---|
| S_SP | none | none | none | none | 1 | 1 (1) |
| S_IF | none | none | none | none | none | 0 (0) |
| S_GR | 7 13 14 15 | none | 2 4 6 7 12 13 14 17 19 | 3 5 8 16 18 20 | none | 15 (12) |
| O_SP | none | none | none | none | none | 0 (0) |
| O_IF | 8 | 8, 19 (19) | 4 | none | none | 3 (2) |
| O_GR | none | none | 11 | none | none | 1 (1) |
| N_IF | 1 2 5 8 9 11-17 19 | none | 1 5 6 9 15 17 | 2 | none | 7 (1) |
| N_GR | 5 11 | 6, 8 (6, 8) | 10 14 | none | none | 4 (4) |

"Contaminated" = failed, any B187 flag, or B214. Failed links that flew a mission before failing (no
`result.json`): O_IF 19 (480-min timeout, 18,807.4 kg, converged), N_GR 6 (timeout, 48,811.0 kg, unconverged),
N_GR 8 (timeout, 30,003.7 kg, unconverged). O_IF 8 failed with zero fuel and did not fly.

**The chain does not carry a failed link's end state.** `chain_start_fuel_kg` of the next link equals the previous
link's fuel on every link except the ones after a failure: O_IF 20 starts at 19,675.9 kg (link 18's fuel), not
link 19's 18,807.4 kg; N_GR 7 starts at 17,562.3 kg (link 4), not link 6's 48,811.0 kg; N_GR 9 starts at
48,758.1 kg (link 7), not link 8's 30,003.7 kg. So the fuel shown for a failed link belongs to a design that did not
continue down the chain. After a did-not-fly link the chain continues (S_GR 8, 14, 15, 16 and N_IF 6, 9, 10, 12-18,
20 all have a chain start).

Sensitivity of the headline numbers (flown links; `sensitivity.csv`):

| Combo | All flown: n, mean fuel | Excl. contaminated: n, mean fuel | Converged only: n, mean fuel | Clean and converged: n, mean fuel |
|---|---|---|---|---|
| S_SP | 20, 25,071.0 | 19, 25,176.0 | 5, 27,302.5 | 5, 27,302.5 |
| S_IF | 20, 22,021.8 | 20, 22,021.8 | 1, 20,740.5 | 1, 20,740.5 |
| S_GR | 16, 25,456.3 | 4, 26,920.8 | 2, 25,244.1 | 1, 22,810.5 |
| O_SP | 20, 31,908.0 | 20, 31,908.0 | 5, 30,022.3 | 5, 30,022.3 |
| O_IF | 19, 25,003.1 | 17, 25,449.2 | 10, 25,353.8 | 9, 26,081.1 |
| O_GR | 20, 30,969.4 | 19, 30,665.0 | 11, 30,351.1 | 11, 30,351.1 |
| N_IF | 7, 19,551.7 | 6, 19,643.4 | 3, 20,187.5 | 3, 20,187.5 |
| N_GR | 18, 32,998.9 | 14, 32,207.9 | 0, n/a | 0, n/a |
| All | 140, 27,232.9 | 119, 27,299.7 | 37, 27,184.2 | 35, 27,409.4 |

Links below the baseline: 11 (all flown), 11 (excl. contaminated), 3 (converged only), 3 (clean and converged).
Links meeting fuel <= 15,000 kg: 4, 4, 0, 0. GTOW <= 90,000 kg: 0 under every filter. Dropping contaminated links
changes the ranking of combinations by mean fuel only in that S_SP and O_IF swap 3rd/4th place; it is the
convergence filter, not the contamination filter, that removes the below-limit results.

## Within a link: first mission vs final mission

| Combo | Links where both flew | Links with >1 distinct design flown | Mean distinct designs per link | Final lower / higher / same (±1 kg) | Mean final minus first (kg) | Range (kg) |
|---|---|---|---|---|---|---|
| S_SP | 20 | 0 | 1.0 | 0 / 0 / 20 | 0.0 | 0.0 to 0.0 |
| S_IF | 20 | 0 | 1.0 | 0 / 0 / 20 | 0.0 | 0.0 to 0.0 |
| S_GR | 16 | 6 | 1.6 | 5 / 1 / 10 | -1,237.0 | -10,136.0 to +2,679.0 |
| O_SP | 20 | 0 | 1.0 | 0 / 0 / 20 | 0.0 | 0.0 to 0.0 |
| O_IF | 19 | 1 | 1.1 | 0 / 1 / 18 | +592.0 | 0.0 to +11,248.3 |
| O_GR | 20 | 6 | 1.4 | 2 / 4 / 14 | +499.0 | -575.4 to +4,776.1 |
| N_IF | 7 | 0 | 1.0 | 0 / 0 / 7 | 0.0 | 0.0 to 0.0 |
| N_GR | 18 | 14 | 3.7 | 6 / 8 / 4 | +2,868.9 | -23,334.8 to +34,892.0 |

"Distinct design" = distinct fuel value among the link's flown missions (`run_simulation` and the `get_results`
that reads it back report the same mission, so `n_missions_flown` overcounts). In 4 of 8 combinations the agents
fly once per link, so any improvement can only happen between links. Where agents do fly several designs, the
last one is as often worse as better (N_GR link 1: first 22,997.6 kg, final 40,022.2 kg).

## The chained design

Link k starts from link k-1's end state (checked: `chain_start_fuel_kg` equals the previous link's fuel on every
pair where both exist, except after the three failed links above). Consequences visible in the data:

- Fuel stays identical from one link to the next when no agent changes the design: S_IF links 9-17 all burn
  21,850.5 kg (9 consecutive repeats; repeats also at link 2), S_GR repeats on links 4, 9-12, 17, O_SP on 6 and 18,
  O_GR on 4, 5, 18 (`chain.csv`).
- A bad link is inherited: means and trends describe a path, not 20 independent samples, so the std over links is
  not a sampling error and links are not independent observations.

## Caveats

- Code versions differ: S_SP, S_IF, S_GR, O_SP, O_IF = v2full (pre-B214/B187/record_recommendation fixes);
  O_GR, N_IF = v3combos; N_GR = v4net (fixed code). Structure/handler comparisons mix versions.
- The baseline mission is itself unconverged (SLSQP), so "vs baseline" compares unconverged to unconverged on most
  links. No converged baseline exists.
- Link fuel is the last successful mission (B102 rule) with no convergence check; this report adds the
  convergence of that same mission from the raw knowledge base. `ec_converged` in links.csv agrees with it on every
  flown link that has a `result.json` (the one difference is O_IF 19, which has no `result.json`). Note that
  `ec_converged` is `True` on did-not-fly links in links.csv, so it must not be read without `flew`.
- N_IF flew only 7 of 20 links, so its means (n = 7) and its last-10 mean (n = 2) are thin. The fixed-code N_IF
  re-run (`v4net_iter_qwen_nothink_aipexws8`, running) is not used here.
- `Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR` in the low-fuel table is the link's `design.json` value; only N_GR 9's
  was cross-checked against its mission's own design parameters. Missing for N_IF 7.
- Why the unconverged low-fuel missions report such low fuel was not investigated beyond the optimizer message
  and violated-constraint count; the violated constraints' names are truncated in the knowledge base.

## How to reproduce

On aipexws8: `cd ~/Jessica/avion_analysis/code && source ~/Jessica/Avion/.venv/bin/activate && python -m analysis.link20.reports.scripts.physics --links ~/Jessica/avion_analysis/out_20261028/links.csv --out ~/Jessica/avion_analysis/reports_20261028/physics`
(console output saved as `physics_stdout.txt` in the same folder).
