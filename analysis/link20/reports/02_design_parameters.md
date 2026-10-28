# 02 — Design parameters: how the design moved over 20 chained links

8 combinations x 20 links of the MAS-Aviary DLR-F25 study (S_SP S_IF S_GR O_SP O_IF = v2full; O_GR N_IF =
v3combos; N_GR = v4net; see `00_README.md`). Every number below comes from
`scripts/design_params.py`, run on aipexws8; tables in ws8 `~/Jessica/avion_analysis/reports_20261028/design_params/`
(file name given with each table, full console output in `run.log` there).

"Moved" = the value differs from the previous link's (link 1: from the baseline) by more than 0.1 %. The tolerance
exists because tigl re-measures a morphed wing: an untouched wing reads 122.83 m² instead of the anchor's 122.78 m².
Baseline = the unchanged anchor design (first line of `out_20261028/plots_run.log`); LPC and HPC pressure ratio have no
recorded baseline, so they are compared with link 1 (1.935 and 9.369 in every combination).

## Key findings

1. **Only the wing and the coupled factors really moved; tails, mission and taper never did.** No successful
   `configure_mission` call in any combination set anything but the fixed mission (2,500 nmi, 239 pax, M 0.78,
   33,000 ft), and `design.json` holds that mission on 160 of 160 links. The tails were morphed in 4 links only
   (O_SP 9, O_IF 13-15), always to their existing values (area change ≤ 0.00004 m²); no aviary tail key was ever
   set. Wing taper stayed 0.2462 on every link although the agents set it in aviary on every link and it was
   recommended 6 times (it never moved). Of 156 transitions with a value, wing area moved in 47, AR in 61, sweep in
   59, fuselage dimensions in 13-15; engine cycle inputs moved in 16-19 of 151-159 (`move_base_rate.csv`).
2. **Engine cycle inputs (T4, BPR, fan/LPC/HPC PR) were changed only by the orchestrated combinations** (plus N_IF:
   T4 2,857 → 2,900 → 2,857 °R, links 15 and 17). S_SP, S_IF, S_GR and N_GR end every link at the canonical cycle
   (T4 2,857 °R, BPR 5.105, fan PR 1.685). O_GR ends furthest away: T4 −16.0 %, BPR +76.3 %, fan PR −22.8 %, HPC PR
   9.369 → 1.8 (−80.8 % vs link 1). Cruise SFC changed only where the cycle did (and on B187 links), which is the
   expected physics.
3. **Every end state has a larger engine and a heavier, draggier aircraft than the anchor.** Engine scale factor at
   link 20 is +40.9 % (O_IF) to +105.2 % (S_SP), and +321.2 % in N_GR (4.21); drag factor +7.9 % to +63.2 % (N_GR
   +100 %, i.e. 2.0); wing mass scaler +45.8 % to +58.9 % (N_GR +5.8 %). These four "applied" values (CL, drag factor,
   wing mass scaler, engine scale) are written by the data plane from the SU2 / mass / pyCycle results, not chosen
   directly by the agents. Most of the jump happens on link 1: engine scale 1.354-1.376 at link 1 in 6 combinations
   (N_IF 0.994 = B187, N_GR 2.865), wing mass scaler 1.456-1.508 at link 1 in all 8.
4. **N_GR explored a physically implausible region.** Wing area −34.7 % (80.2 m²), sweep −9.96 ° at link 20 (negative
   on 12 links), AR above 15 on 11 links (max 21.05, link 4); its tigl morphs inside links reached 20.8-304.9 m²,
   AR 2.51-36.28 and sweep −77.05 ° (`wing_morph_range.csv`, 134 morphs). On 16 of its links SU2 returned CD 0.16-0.45
   (one CD 0.00073, link 15) and the drag factor sat at 2.0 on 19 of 20 links, the declared upper bound
   (0.5, 2.0) in `src/tools/data_plane.py`. All other combinations kept end-state area within 109.9-135.7 m² and AR
   within 8.5-13.5.
5. **Most parameters oscillate rather than drift.** Net movement over the chain divided by cumulative movement
   (1 = one-way drift, 0 = back where it started): engine scale 0.05-0.34 in all 8; S_SP wing AR 0.05 (cumulative
   124.8 % of the start value for a net +6.6 %, 5 direction reversals in 15 moves). One-way drifts: S_GR sweep
   (ratio 1.00, 21.65 → 5.19 °, −76.0 %), O_GR sweep (1.00, −28.5 %), S_IF sweep (1.00, but one move only), and the
   wing mass scaler (0.53-0.97 outside N_GR, mostly the link-1 jump).
6. **Recommended moves were mostly followed when anything moved, but often nothing moved.** Of 116 parsed
   recommendation items, 57 went the recommended way, 8 the opposite way, 28 did not move, 17 had no direction
   (all 17 are targets within 0.1 % of the value the design already had; 11 of them N_GR) and 6 had no recorded actual. Same direction in
   57 of 65 items that moved; 57 of 93 items with a direction and a recorded actual. S_SP alone gives 20 of 21; the
   other 7 combinations 37 of 72. Engine scale recommendations went the opposite way 5 times of 18 (the scale is
   set by the coupled pyCycle thrust, not by the agents).
7. **Fuel co-moves most strongly with the coupled factors (engine scale, drag factor); weakly with the geometry.** Over 123 consecutive flown link pairs (all 8
   combinations), Spearman ρ between a parameter's % move and the fuel change: engine scale 0.54 (n = 83 pairs where
   it moved), drag factor 0.50 (n = 81), wing mass scaler 0.35 (n = 63), wing area 0.35 (n = 37), AR 0.16 (n = 49),
   sweep −0.21 (n = 47). Engine scale down → fuel dropped in 27 of 40 pairs; up → 15 of 43. In 20 pairs where no
   parameter moved, fuel still dropped in 7. Descriptive only: small n, pooled across code versions, mostly
   unconverged missions (only 17 pairs have both missions converged), and fuel is partly a direct function of the
   coupled factors.
8. **B187 shows up as engine scale 0.99394 at the end of 17 links (S_GR 10, N_IF 7), and these are the only links
   whose scale is below 1.0.** Two of them are not marked by the `b187` flag: S_GR link 15 (did not fly) and
   **N_IF link 10, which flew (18,422.6 kg, unconverged) on the inherited 0.994 scale** — see Suspicious.

## Plots

On ws8 under `~/Jessica/avion_analysis/out_20261028/plots/`:

- `3_design_parameters/params_line_marker.png`, `params_line_linestyle.png` — all 16 parameters per link, 8 combinations, baseline line.
- `3_design_parameters/params_avg_strip.png` — per-combination spread and mean.
- `3_design_parameters/param_change_heatmap_<structure>_<handler>.png` — % change from the previous link.
- `3b_recommended_parameters/rec_params_all.png`, `rec_params_<structure>_<handler>.png`, `rec_change_heatmap_<structure>_<handler>.png` — the recommended targets / changes on the same axes.

Wing span is not in those plots (it is read here from `design.json` `mission_applied`).

## 1. What each combination changed

Transitions in which the parameter moved (> 0.1 %), out of 20 per combination (link 1 vs baseline + 19
link-to-link; fewer where a link has no value: O_IF and N_GR 18 for the chain parameters, LPC/HPC 19 because they
start at link 1). `change_counts_pivot.csv`, `transitions_pivot.csv`, link lists in `change_counts.csv`.

| Parameter | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR |
|---|---|---|---|---|---|---|---|---|
| Wing area | 7 | 6 | 7 | 2 | 1 | 3 | 6 | 15 |
| Wing AR | 15 | 1 | 5 | 6 | 6 | 4 | 8 | 16 |
| Wing sweep | 15 | 1 | 5 | 6 | 6 | 4 | 8 | 14 |
| Wing taper | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Wing span | 17 | 7 | 7 | 6 | 6 | 5 | 10 | 16 |
| Fuselage length | 0 | 0 | 2 | 0 | 6 | 1 | 0 | 4 |
| Fuselage height | 0 | 0 | 2 | 0 | 6 | 1 | 0 | 4 |
| Fuselage width | 0 | 0 | 2 | 0 | 6 | 2 | 0 | 5 |
| Engine scale | 19 | 10 | 13 | 15 | 16 | 13 | 12 | 9 |
| T4 | 0 | 0 | 0 | 2 | 8 | 4 | 2 | 0 |
| BPR | 0 | 0 | 0 | 2 | 10 | 7 | 0 | 0 |
| Fan PR | 0 | 0 | 0 | 4 | 10 | 4 | 0 | 0 |
| LPC PR | 0 | 0 | 0 | 4 | 9 | 4 | 0 | 0 |
| HPC PR | 0 | 0 | 0 | 2 | 9 | 8 | 0 | 0 |
| Design CL | 16 | 9 | 10 | 11 | 11 | 13 | 12 | 19 |
| Drag factor | 19 | 10 | 11 | 15 | 17 | 14 | 15 | 3 |
| Wing mass scaler | 18 | 8 | 7 | 7 | 7 | 6 | 7 | 19 |

Never touched (any combination):

- **Mission**: successful `configure_mission` calls per combination S_SP 23, S_IF 34, S_GR 61, O_SP 25, O_IF 80,
  O_GR 62, N_IF 18, N_GR 163; 0 differ from the fixed mission (`mission_settings.csv`).
- **Tails**: tigl tail morphs only in O_SP link 9 and O_IF links 13, 14, 15 (`tail_morph_calls.csv`), each
  re-stating the current area / AR / sweep (Wing2H 31.1184 m², AR 4.98097, 27.9999 °; Wing3V 21.5 m², AR 1.60694,
  37.2 °). No `Aircraft.HorizontalTail.*` / `Aircraft.VerticalTail.*` key in any `set_aircraft_parameters` call
  (`aviary_key_counts.csv` lists the 11 keys ever set). `agent_bounds.json`: htail area/AR/sweep min = max over 91
  values.
- **Wing taper**: set in aviary in every combination (21-224 successful calls per combination) but 0 moves; the
  end-state geometry comes from tigl's measured wing, and tigl ignores taper (2026-10-27 CHANGELOG).
- **Fuselage**: unchanged (≤ 0.1 %) in S_SP, S_IF, O_SP, N_IF. Changed in S_GR (links 19-20, back to the start),
  O_IF (6 transitions, back to the start), O_GR (+11.7 % height, +11.8 % width, +1.2 % length at link 20) and N_GR
  (min height 3.65 m at link 19, back to 4.13 m at link 20).

Who sets the engine: successful pyCycle `set_inputs` calls for T4 were made in every combination (S_SP 22 ... O_GR 39,
`cycle_key_counts.csv`), but outside the orchestrated combinations (and N_IF links 15, 17) they set the value the
cycle already had.

## 2. Link-20 end state vs baseline

All end states are link 20 (`end_link_pivot.csv`). % vs baseline (`end_pct_vs_baseline_pivot.csv`); LPC/HPC have no
baseline (link-1 value 1.935 / 9.369).

| Parameter (unit) | Baseline | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR |
|---|---|---|---|---|---|---|---|---|---|
| Wing area (m²) | 122.78 | −2.2 % | +1.8 % | +10.5 % | −4.2 % | +4.1 % | +5.9 % | +1.8 % | −34.7 % |
| Wing AR (–) | 9.37 | +6.6 % | +3.5 % | +35.9 % | +17.3 % | +12.0 % | +15.3 % | +12.1 % | +1.2 % |
| Wing sweep (deg) | 21.65 | −5.7 % | −3.2 % | −76.0 % | −22.0 % | −9.9 % | −28.5 % | −30.5 % | −146.0 % |
| Wing taper (–) | 0.246 | +0.1 % | +0.1 % | +0.1 % | +0.1 % | +0.1 % | +0.1 % | +0.1 % | +0.1 % |
| Wing span (m) | 33.918 | +2.1 % | +2.7 % | +22.6 % | +6.0 % | +8.0 % | +10.5 % | +6.8 % | −18.7 % |
| Fuselage length (m) | 37.68 | 0.0 % | 0.0 % | 0.0 % | 0.0 % | 0.0 % | +1.2 % | 0.0 % | −0.0 % |
| Fuselage height (m) | 4.15 | −0.0 % | −0.0 % | −0.0 % | −0.0 % | −0.0 % | +11.7 % | −0.0 % | −0.4 % |
| Fuselage width (m) | 3.96 | −0.0 % | −0.0 % | −0.0 % | −0.0 % | −0.0 % | +11.8 % | −0.0 % | +0.1 % |
| Engine scale (–) | 1.0 | +105.2 % | +70.2 % | +54.2 % | +83.1 % | +40.9 % | +42.4 % | +83.0 % | +321.2 % |
| T4 (°R) | 2,857 | 0.0 % | 0.0 % | 0.0 % | 0.0 % | 0.0 % | −16.0 % | 0.0 % | 0.0 % |
| BPR (–) | 5.105 | 0.0 % | 0.0 % | 0.0 % | 0.0 % | +56.7 % | +76.3 % | 0.0 % | 0.0 % |
| Fan PR (–) | 1.685 | 0.0 % | 0.0 % | 0.0 % | 0.0 % | +0.9 % | −22.8 % | 0.0 % | 0.0 % |
| Design CL (–) | 0.5 | +12.6 % | +12.7 % | +18.1 % | +17.7 % | +0.3 % | +6.4 % | +10.7 % | +41.1 % |
| Drag factor (–) | 1.0 | +59.0 % | +29.9 % | +63.2 % | +52.3 % | +7.9 % | +13.6 % | +13.5 % | +100.0 % |
| Wing mass scaler (–) | 1.0 | +47.4 % | +50.1 % | +58.9 % | +45.8 % | +51.9 % | +52.9 % | +49.7 % | +5.8 % |

Link-20 values (`end_value_pivot.csv`):

| Value (unit) | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR |
|---|---|---|---|---|---|---|---|---|
| Wing area (m²) | 120.09 | 125.00 | 135.69 | 117.58 | 127.87 | 130.00 | 125.00 | 80.19 |
| Wing AR (–) | 9.99 | 9.70 | 12.74 | 10.99 | 10.50 | 10.80 | 10.50 | 9.48 |
| Wing sweep (deg) | 20.41 | 20.96 | 5.19 | 16.89 | 19.51 | 15.49 | 15.05 | −9.96 |
| Wing span (m) | 34.64 | 34.83 | 41.57 | 35.95 | 36.63 | 37.47 | 36.23 | 27.57 |
| Engine scale (–) | 2.052 | 1.702 | 1.542 | 1.831 | 1.409 | 1.424 | 1.830 | 4.212 |
| T4 (°R) | 2,857 | 2,857 | 2,857 | 2,857 | 2,857 | 2,400 | 2,857 | 2,857 |
| BPR / fan PR | 5.105 / 1.685 | 5.105 / 1.685 | 5.105 / 1.685 | 5.105 / 1.685 | 8.0 / 1.70 | 9.0 / 1.30 | 5.105 / 1.685 | 5.105 / 1.685 |
| LPC PR / HPC PR | 1.935 / 9.369 | 1.935 / 9.369 | 1.935 / 9.369 | 1.935 / 9.369 | 2.0 / 10.0 | 1.6 / 1.8 | 1.935 / 9.369 | 1.935 / 9.369 |
| Aero CL (SU2) | 0.5632 | 0.5637 | 0.5904 | 0.5885 | 0.5016 | 0.5321 | 0.5534 | 0.7053 |
| Aero CD (SU2) | 0.0345 | 0.0252 | 0.0331 | 0.0319 | 0.0141 | 0.0168 | 0.0180 | 0.2797 |
| MTOM (kg) | 92,344 | 92,801 | 88,605 | 90,148 | 91,077 | 90,261 | 90,687 | 78,126 |
| Cruise SFC (as recorded) | 0.6432 | 0.6432 | 0.6432 | 0.6432 | 0.5314 | 0.8024 | 0.6432 | 0.6432 |
| Thrust Fn (lbf) | 12,181 | 10,102 | 9,154 | 10,869 | 8,365 | 8,452 | 10,861 | 25,000 |

Aero CL / CD, MTOM, SFC and thrust have no baseline value recorded (the baseline mission didn't run SU2, mass or
pyCycle), so they are given as values only. At link 1 (`outputs_link1_pivot.csv`): CL 0.5587-0.5604 and CD
0.0194-0.0231 in the 7 non-N_GR combinations (N_GR: 0.6364 / 0.0694), MTOM 78,126 kg in 7 of 8 (N_GR 88,033 kg),
SFC 0.643235 in all 8. SFC units are not converted (as recorded in `design.json` `propulsion.sfc_cruise`).

## 3. Range explored and extreme values

Min / max of the end-of-link values over the chain, with the link (`end_state.csv`; selection):

| Combination | Wing area (m²) | Wing AR | Sweep (deg) | Engine scale | T4 (°R) | BPR |
|---|---|---|---|---|---|---|
| S_SP | 109.94 (14) – 124.97 (18) | 9.00 (11) – 12.01 (4) | 17.22 (4) – 22.44 (11) | 1.376 (1) – 2.078 (8) | 2,857 | 5.105 |
| S_IF | 115.00 (3) – 125.00 (5) | 9.36 (1) – 9.70 (18) | 20.96 (18) – 21.65 (1) | 1.165 (18) – 1.825 (5) | 2,857 | 5.105 |
| S_GR | 125.00 (2) – 135.69 (20) | 9.36 (1) – 12.74 (20) | 5.19 (20) – 21.65 (1) | 0.994 (2) – 2.064 (18) | 2,857 | 5.105 |
| O_SP | 114.95 (7) – 122.83 (1) | 9.36 (1) – 13.53 (16) | 13.87 (16) – 21.65 (1) | 1.032 (4) – 2.214 (16) | 2,760 (18) – 2,857 | 5.10 (6) – 8.0 (13) |
| O_IF | 122.83 (1) – 128.06 (6) | 8.49 (6) – 11.51 (16) | 17.92 (16) – 23.62 (6) | 1.304 (6) – 3.188 (11) | 1,550 (3) – 3,240 (11) | 5.105 (1) – 10.0 (9) |
| O_GR | 122.83 (1) – 131.47 (8) | 9.36 (1) – 10.80 (10) | 15.49 (10) – 21.65 (1) | 1.217 (8) – 2.389 (9) | 2,350 (18) – 2,857 (1) | 4.5 (4) – 9.0 (18) |
| N_IF | 119.94 (7) – 125.86 (3) | 9.36 (1) – 10.50 (14) | 15.05 (14) – 21.65 (1) | 0.994 (2) – 1.830 (20) | 2,857 – 2,900 (15) | 5.105 |
| N_GR | 60.02 (15) – 122.29 (1) | 9.48 (20) – 21.05 (4) | −9.96 (18) – 11.30 (10) | 1.807 (16) – 4.212 (14) | 2,857 | 5.105 |

Values set *inside* links go further than the end states (`agent_bounds.json`, every successful morph and
`set_inputs`, all combinations): wing area 20.84-304.92 m², AR 2.51-36.28, sweep −77.05 to 23.62 ° (all wing
extremes from N_GR link 20's morphs), T4 1,500 °R (O_SP link 19) to 5,531.67 °R (O_GR link 16), BPR 3.5-10,
fan PR 1.2-1.8, HPC PR 1.8-14. Per-combination morph ranges (`wing_morph_range.csv`): outside N_GR, all morphed
wings stayed within 109.94-135.69 m², AR 8.49-13.53, sweep 5.19-23.62 °.

Flagged end-of-link values (`extremes_flagged.csv`; thresholds chosen here: sweep < 0 °, AR > 15,
T4 < 2,000 °R):

- **Negative sweep**: N_GR links 3, 4, 5, 7, 9, 14-20 (−3.61 to −9.96 °). 11 of these 12 links flew.
- **AR > 15**: N_GR links 2, 3, 4, 5, 10-16 (15.01-21.05); 9 of these 11 flew.
- **T4 = 1,550 °R** (looks like a value in K): O_IF link 3, which flew 23,553.8 kg. The 5,531.67 °R set in O_GR
  link 16 is not an end state (O_GR's T4 that link is 2,800 °R) but that link's SFC is 3.667 (see Suspicious).
- **Engine scale > 3**: N_GR links 2-5, 9-14, 17, 19, 20 (max 4.21); O_IF link 11 (3.19).

## 4. Drift vs oscillation

Net / cumulative movement from the baseline (link 1 for LPC/HPC) to link 20, and direction reversals / moves
(`drift.csv`, `drift_ratio_pivot.csv`, `cumulative_pct_pivot.csv`). Parameters that never moved are left out.

| Parameter | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR |
|---|---|---|---|---|---|---|---|---|
| Wing area: ratio (rev./moves) | 0.08 (2/7) | 0.07 (3/6) | 0.56 (4/7) | 0.49 (1/2) | 0.91 (0/1) | 0.70 (1/3) | 0.12 (4/6) | 0.35 (4/15) |
| Wing AR | 0.05 (5/15) | 0.96 (0/1) | 0.82 (2/5) | 0.19 (3/6) | 0.22 (3/6) | 0.64 (2/4) | 0.33 (4/8) | 0.00 (5/16) |
| Wing sweep | 0.06 (5/15) | 1.00 (0/1) | 1.00 (0/5) | 0.35 (3/6) | 0.22 (3/6) | 1.00 (0/4) | 0.43 (4/8) | 0.46 (6/14) |
| Engine scale | 0.23 (14/19) | 0.34 (4/10) | 0.05 (12/13) | 0.23 (7/15) | 0.08 (8/16) | 0.07 (7/13) | 0.29 (9/12) | 0.22 (6/9) |
| BPR | – | – | – | 0.00 (1/2) | 0.09 (8/10) | 0.48 (3/7) | – | – |
| T4 | – | – | – | 0.00 (1/2) | 0.00 (6/8) | 0.82 (1/4) | 0.00 (1/2) | – |
| Design CL | 0.36 (14/16) | 0.82 (8/9) | 0.30 (4/10) | 0.31 (6/11) | 0.01 (6/11) | 0.04 (9/13) | 0.06 (9/12) | 0.08 (11/19) |
| Drag factor | 0.15 (12/19) | 0.20 (4/10) | 0.27 (6/11) | 0.17 (7/15) | 0.02 (10/17) | 0.03 (7/14) | 0.04 (11/15) | 0.95 (2/3) |
| Wing mass scaler | 0.53 (11/18) | 0.71 (6/8) | 0.89 (2/7) | 0.76 (3/7) | 0.70 (3/7) | 0.97 (1/6) | 0.80 (2/7) | 0.03 (9/19) |

Cumulative movement as % of the start value: engine scale 205.7 % (S_IF) to 1,455.0 % (N_GR); wing AR 124.8 % in
S_SP and 396.7 % in N_GR; O_IF BPR 627.9 %. The engine scale and the drag factor go back and forth on almost every
link in every combination; the geometry drifts one way only in S_GR and O_GR (sweep down, AR and span up).

## 5. Parameter moves and fuel (descriptive)

Consecutive link pairs (k−1, k) where both links flew: 123 (S_SP 19, S_IF 19, S_GR 13, O_SP 19, O_IF 17, O_GR 19,
N_IF 2, N_GR 15); both missions converged: 17; neither link with a B187 hit: 104. `fuel_comovement.csv`,
pair-level data in `fuel_pairs.csv`. Pooled over combinations:

| Parameter | Up: pairs, fuel dropped | Down: pairs, fuel dropped | Spearman ρ (% move, Δfuel), n moved pairs | ρ, no B187 (n) |
|---|---|---|---|---|
| Engine scale | 43, 15 | 40, 27 | 0.54 (83) | 0.55 (67) |
| Drag factor | 37, 12 | 44, 27 | 0.50 (81) | 0.50 (68) |
| Wing mass scaler | 34, 12 | 29, 19 | 0.35 (63) | 0.35 (54) |
| Wing area | 18, 5 | 19, 12 | 0.35 (37) | 0.24 (28) |
| Wing span | 36, 14 | 25, 16 | 0.28 (61) | 0.27 (51) |
| Design CL | 38, 17 | 41, 24 | 0.25 (79) | 0.26 (66) |
| Wing AR | 33, 14 | 16, 11 | 0.16 (49) | 0.21 (40) |
| Wing sweep | 13, 9 | 34, 14 | −0.21 (47) | −0.38 (38) |
| T4 / BPR / fan / LPC / HPC PR | 6-9 per direction | 8-11 per direction | −0.04 to 0.15 (14-18) | |
| (no parameter moved) | 20 pairs, 7 with fuel dropping | | | |

The 17 pairs with both missions converged are too few to say anything (e.g. engine scale ρ 0.90 over 10 pairs, drag
factor 0.72 over 10). Fuel falls when the engine gets smaller and the drag factor smaller, which is what aviary
computes from those inputs; the geometry's link with fuel goes mostly through the SU2 CD that sets the drag factor.

## 6. Recommended vs actual change

Each item in `recommended_changes.csv` (integrator's last recommendation in link k−1, parsed per parameter) vs the
actual move from link k−1 to link k (> 0.1 %). Recommended direction = sign(target − value at k−1) for value / range /
bound / % items (with the same 0.1 % tolerance), else the stated up/down. `rec_vs_actual*.csv`.

| Combination | Same direction | Opposite | Did not move | No direction | Actual not recorded | Items |
|---|---|---|---|---|---|---|
| S_SP | 20 | 0 | 1 | 0 | 0 | 21 |
| S_IF | 7 | 0 | 0 | 3 | 0 | 10 |
| S_GR | 2 | 0 | 4 | 2 | 0 | 8 |
| O_SP | 6 | 2 | 7 | 0 | 0 | 15 |
| O_IF | 3 | 2 | 5 | 0 | 2 | 12 |
| O_GR | 4 | 3 | 6 | 0 | 0 | 13 |
| N_IF | 7 | 0 | 3 | 1 | 0 | 11 |
| N_GR | 8 | 1 | 2 | 11 | 4 | 26 |

- By parameter: wing AR 29 same / 0 opposite / 2 no move; wing area 21 / 1 / 3; engine scale 5 / 5 / 8; taper
  0 / 0 / 6; BPR 1 / 0 / 3; HPC PR, fan PR: 0 moved of 2 each; T4 and sweep 1 opposite each (`rec_vs_actual_by_param.csv`).
- Value / range targets reached within 2 %: 37 items yes, 32 no.
- Items per parameter: wing AR 42, wing area 34, engine scale 18, taper 6, BPR 4, design CL 3, wing mass scaler 3,
  fan PR 2, HPC PR 2, T4 1, sweep 1 (116 in all). Recommendations were mostly about the wing and the engine scale.
- Base rate for comparison: over all 156 transitions, wing area moved in 47 and AR in 61 whether recommended or not.

## Caveats

- **Code versions**: S_SP, S_IF, S_GR, O_SP, O_IF are v2full (2026-10-14 code, before the B187 / B214 /
  record_recommendation fixes); O_GR, N_IF v3combos; N_GR v4net (fixed code). N_IF is the complete v3combos run,
  not the fixed-code re-run still in progress on ws8.
- **Contamination kept and flagged**: B187 on S_GR (15 links), N_IF (7), O_GR 11, O_IF 4, N_GR 10 and 14; plus the
  2 unflagged 0.994 links in finding 8. S_GR's and N_IF's engine-scale trajectories are partly B187 artefacts.
  B214 (S_SP link 1) does not touch the design variables here.
- **Gaps**: O_IF links 8 and 19 and N_GR links 6 and 8 have no `result.json`, so no chain parameters; drift and
  change counts compare across the gap with the last recorded value. N_IF has no CL / drag factor on 3 links.
  Wing span is read from `design.json` (`mission_applied`, 159 links; `design_space` 1 link) and so has 20 values
  in O_IF and N_GR.
- **Not agent choices**: design CL, drag factor, wing mass scaler and engine scale are written by the data plane from
  SU2 / mass-mcp / pyCycle; aviary wing geometry is tigl's measured wing (requested values differ, and taper is
  ignored). "What the agents changed" for these means what their tool calls caused, not what they typed.
- **Fuel co-movement**: fuel = last successful mission, no convergence check; only 17 of 123 pairs are converged on
  both sides; pairs pool 3 code versions and 8 organisations; several parameters move together (area, AR, sweep and
  span always move in the same links), so no single-parameter effect can be separated. Purely descriptive.
- **Recommendation parsing**: items come from a regex parser of free text (`rec_params.py`); clauses naming no or two
  parameters are dropped. Pre-fix v2full logged up to 77 recommendations per link; only the last one per link is
  used here, so counts are comparable per link but not the recommendation volume.
- **Thresholds** (0.1 % move, sweep < 0, AR > 15, T4 < 2,000 °R, CD outside 0.01-0.1, scale > 3) are chosen for this
  report, not physical limits from the study.

## Suspicious (possible bugs, for Jessica)

1. **B187 flag misses inherited scale without a set call.** N_IF link 10 ends at scale 0.99394 and flew
   (18,422.6 kg, unconverged) but `b187` is False: the link never called `set_aircraft_parameters` with the scale and
   its successful `run_cycle` gave Fn 6,336 lbf, so neither extractor rule fires; the 0.994 came in through the aviary
   session start. Same for S_GR link 15 (did not fly). `extract.b187_hits` should also check the end-state scale
   (`b187_scale_unflagged.csv`).
2. **N_GR drag factor pinned at 2.0** on 19 of 20 links, with SU2 CD 0.16-0.45 on 16 links and CD 0.00073 /
   CL 0.155 on link 15 (`cd_outliers.csv`); its link-20 thrust is 24,999.99999997 lbf and
   MTOM 78,126 kg. Looks like SU2 on the extreme morphed wings plus clamping at the declared bound, so N_GR's fuel
   numbers rest on clamped aero.
3. **MTOM 78,126 kg recurs exactly** on 50 of 159 link-ends, in all 8 combinations (e.g. S_IF links 1-19, every
   link 1 outside N_GR, N_GR link 20; `value_checks.csv`). Looks like a default / unsized mass-mcp value, not a computed MTOM. Not checked
   in mass-mcp.
4. **O_GR link 16 SFC 3.667** (others 0.52-0.80) in the link where T4 5,531.67 °R was set via `set_inputs`, while
   `design.json` records T4 2,800 °R for that link. **N_IF link 14 SFC exactly 1.0** with an unchanged cycle (looks
   like a placeholder). `sfc_changes.csv`.
5. **Design CL ≠ SU2 CL** on 7 link-ends (S_GR 5, O_IF 19, O_GR 8, N_GR 13, 15, 17, 18), where elsewhere they are
   equal; the injected CL may be from an earlier SU2 run in those links.
6. **Recommendations that restate the current value**: 17 of 116 items (11 of N_GR's 26) have a target within 0.1 %
   of the value the design already had (e.g. N_GR link 14: wing area 70 when it is 69.9999), i.e. the integrator
   recorded what had been done, not a change for the next link.

## How to reproduce

On aipexws8: `source ~/Jessica/Avion/.venv/bin/activate; cd ~/Jessica/avion_analysis/code; python -m analysis.link20.reports.scripts.design_params --links ../out_20261028/links.csv --recs ../out_20261028/recommended_changes.csv --bounds ../out/agent_bounds.json --plots-log ../out_20261028/plots_run.log --out ../reports_20261028/design_params`
(script: MAS-Aviary-analysis `analysis/link20/reports/scripts/design_params.py`; reads the run folders read-only).
