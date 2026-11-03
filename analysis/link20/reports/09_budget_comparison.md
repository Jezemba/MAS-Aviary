# MAS combinations vs optimizers at an equal flight budget (B = 20)

Compiled 2026-11-01; updated 2026-11-03 with the finished sweep. All five optimizers (Bayesian optimization (BO),
GA, MADS, SLSQP and random search) ran 5 seeds each, between 2026-10-31 19:50 and 2026-11-02 23:35 on aipexws8.

Combinations: S / O / N = sequential / orchestrated / networked; SP / IF / GR = staged pipeline / iterative
feedback / graph-routed.

## Why compare per flight

One optimizer iteration flies several designs (PSO and GA about 6, SLSQP 11 for one finite-difference gradient),
while one MAS link flies about one. Comparing per link or per iteration therefore favours the optimizers. Following
WAFER (Song, Michalek, McComb; github.com/Chani-song/WAFER), every method is compared per simulator evaluation
under the same fixed budget.

## Setup

- **Budget unit = a flight:** one Aviary `run_simulation` that succeeded and burned more than 100 kg of fuel.
  SU2, meshing, mass and pyCycle calls are not charged, because the agents were not charged for them either.
  Tool counts per method are in `eval_counts.py`.
- **Budget:** B = 20 flights per optimizer run, 5 seeds per optimizer. A design that does not fly uses no budget.
  A run also stops after 40 designs sent through the pipeline. A design proposed twice is answered from a cache:
  it is not re-run and not counted again.
- **Optimizers** (WAFER configurations): BO (Ax 1.3.1, 5 Sobol initialisation trials), GA (pymoo 0.6.2,
  population 5), MADS (NOMAD 4.5.1), SLSQP (scipy, forward differences) and random search. All start from the
  agents' starting design, use the same 10 variables and bounds as the agents, and run the same coupled pipeline.
- **Objective:** the paper's penalised loss (WAFER eq. D1): fuel, plus 1e6 + 1e5 G when GTOW is over 90,000 kg.
- **Mission residuals (B236):** BO, GA, MADS and SLSQP also receive Aviary's range residual (NM) and mass
  residual (lbm) as constraints (residual ≤ 1 NM / 100 lbm). This gives them the information the agents read in
  the `run_simulation` output, as numbers instead of text. The residuals come from aviary-mcp branch
  `feat/optimizer-mission-residuals`, an optimizer-only version that returns them on every run. The agents saw them
  only when a mission did not converge, and only if they were among the 5 largest violations.
- **MAS flights:** each combination's single 20-link chain, read in order (link 1 to 20; inside a link, in the
  order of the knowledge base). Its first 20 flights are compared with each optimizer seed's 20 flights. N_IF made
  only 11 flights in 20 links.

## Main point: no method met the GTOW limit

No converged flight from any method reached GTOW ≤ 90,000 kg; only a few unconverged flights did. The lowest
converged GTOW is 91,701 kg (N_IF); the starting design is at 97,263 kg. Best feasible fuel is therefore empty for
every method. Three values are reported instead:

1. best fuel among **converged** flights (GTOW ignored)
2. lowest GTOW among converged flights (how close a method got to the limit)
3. best fuel among **all** flights, converged or not (shown for completeness; see the caution below)

The paper loss is in `data/09_budget_compare_at_20_flights.csv`, but it should not be used for ranking. Every
converged flight carries the 1e6 GTOW penalty. The only flights under the limit did not converge, and the loss
(no convergence check, as in D1) ranks them best.

## Two optimizers need a note

- **SLSQP's 5 seeds are identical.** SLSQP has no randomness, every seed starts from the same design, and the
  pipeline returns the same result for the same design. All 5 seeds ran exactly the same 21 designs, so SLSQP is
  effectively **n = 1**. Varying it would need a different start per seed, which would break "every method starts
  from the agents' design".
- **Random search rarely flies.** Every seed hit the 40-design cap with only 2–10 flights (29 flights from 200
  designs). Its only converged flight in every seed is the starting design (flight 1), so its best converged fuel
  is the starting design's 19,020 kg. Uniform random designs in this 10-variable space almost never produce a
  converged mission.

## Converged flights, after 20 flights

Optimizers: mean over seeds, with the range over seeds in brackets.

| Method | Flights | Best converged fuel (kg) | Lowest converged GTOW (kg) |
|---|---|---|---|
| MADS | 20 | **17,282** (16,638–18,248) | 97,263 |
| MAS N_IF | 11 | 17,521 | **91,701** |
| BO | 20 | 17,841 (17,364–18,446) | 97,191 |
| SLSQP (n = 1) | 20 | 18,204 | 96,234 |
| GA | 20 | 18,476 (17,395–19,020) | 97,091 |
| Random | 2–10 | 19,020 (the starting design) | 97,263 |
| MAS S_IF | 20 | 20,741 | 97,984 |
| MAS S_GR | 20 | 22,811 | 102,672 |
| MAS S_SP | 20 | 23,554 | 103,555 |
| MAS O_SP | 20 | 26,953 | 108,754 |
| MAS O_IF | 20 | 28,513 | 112,433 |
| MAS O_GR | 20 | no converged flight | – |
| MAS N_GR | 20 | no converged flight | – |

- **Fuel:** MADS is lowest on average and has the lowest single result (16,638 kg). BO, SLSQP and GA follow, all
  within about 1,200 kg of each other. Of the MAS, only N_IF is in this group (17,521 kg, with 11 flights). The
  other MAS combinations are 2,000–11,000 kg higher.
- **GTOW:** N_IF is the only method that moved GTOW close to the limit. The optimizers stay near the starting
  design's 97,263 kg (SLSQP lowest at 96,234 kg).

![Best converged fuel and lowest converged GTOW against flights used](figures/09_budget_compare.png)

## Fuel saving against the number of evaluations

Fuel saving = (18,197 − fuel) / 18,197, i.e. against the unchanged baseline mission used in reports 00–08. Above 0
= less fuel than the baseline. One evaluation = one flight. The optimizers' starting design (19,020 kg) is −4.5 %,
so every optimizer starts there. The MAS's first flights (~23,000 kg in link 1) already include agent changes and
the coupled SU2 / mass / engine results.

Best **converged** saving so far (%), at selected evaluations. Optimizers: mean over the seeds that reached that
evaluation. "–" = no converged flight yet. Every evaluation 1–20 is in
`data/09_best_converged_saving_by_evaluation.csv`.

| Evaluation | MADS | BO | SLSQP | GA | Random | N_IF | S_IF | S_GR | S_SP | O_SP | O_IF | O_GR, N_GR |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | −4.5 | −4.5 | −4.5 | −4.5 | −4.5 | – | – | – | – | – | – | – |
| 5 | −0.3 | 1.1 | 0.0 | −2.8 | −4.5 | 1.9 | – | −25.4 | −29.4 | – | – | – |
| 10 | −0.3 | 1.8 | 0.0 | −2.8 | −4.5 | 3.7 | – | −25.4 | −29.4 | −48.1 | – | – |
| 15 | 4.6 | 1.8 | 0.0 | −2.0 | (stopped) | 3.7 (at 11) | – | −25.4 | −29.4 | −48.1 | – | – |
| 20 | **5.0** | 2.0 | 0.0 | −1.5 | (stopped) | 3.7 (at 11) | −14.0 | −25.4 | −29.4 | −48.1 | −56.7 | – |

- Only MADS, BO and N_IF end below the baseline's fuel. SLSQP reaches the baseline (18,204 kg, −0.04 %); GA and
  random do not.
- N_IF gets to 3.7 % in 9 evaluations; MADS needs 15 to pass it, BO does not.
- Random search has its last value at evaluation 10 (its seeds stopped at 2–10 flights).

![Best converged fuel saving against the number of evaluations](figures/09_fuel_saving_vs_evaluations.png)

Every evaluation's saving, per method: filled = converged, hollow = not converged (optimizers one colour per seed);
the line is the best converged saving so far. Points beyond −100 % or +60 % are drawn as triangles on the edge.

![Fuel saving at each evaluation, one panel per method](figures/09_fuel_saving_per_evaluation.png)

- BO, GA and MADS: savings at single evaluations jump between about −100 % and +60 %; nearly all positive values
  are unconverged. Converged points cluster between −10 % and +8 %.
- SLSQP: converged steps between −90 % and 0 %, ending at 0 %.
- MAS: most evaluations are unconverged (hollow). The positive savings of S_SP (+35 %) and O_IF (+5 %) are all
  unconverged. N_GR's evaluations after the first are all at or beyond −94 %.

## Where the best fuel came from (first 20 flights)

Flight = position in the method's flight sequence. MAS link = the chain link that flight belongs to.

| Combination | Converged flights | Best fuel, any (kg) | flight / link | Best converged fuel (kg) | flight / link |
|---|---|---|---|---|---|
| S_SP | 5 | 11,824 (unconverged) | 11 / 11 | 23,554 | 4 / 4 |
| O_IF | 3 | 17,302 (unconverged) | 6 / 6 | 28,513 | 20 / 12 |
| N_IF | 5 | 17,521 | 9 / 18 | 17,521 | 9 / 18 |
| O_SP | 5 | 18,521 (unconverged) | 4 / 4 | 26,953 | 9 / 9 |
| S_IF | 1 | 19,355 (unconverged) | 3 / 3 | 20,741 | 19 / 18 |
| S_GR | 4 | 19,867 (unconverged) | 6 / 2 | 22,811 | 2 / 1 |
| O_GR | 0 | 22,664 (unconverged) | 1 / 1 | – | – |
| N_GR | 0 | 22,998 (unconverged) | 1 / 1 | – | – |

| Optimizer, seed | Flights | Converged flights | Best fuel, any (kg) | flight | Best converged fuel (kg) | flight |
|---|---|---|---|---|---|---|
| BO 0 | 20 | 15 | 17,723 | 4 | 17,723 | 4 |
| BO 1 | 20 | 17 | 16,320 (unconverged) | 6 | 17,364 | 5 |
| BO 2 | 20 | 13 | 18,268 | 16 | 18,268 | 16 |
| BO 3 | 20 | 13 | 13,699 (unconverged) | 6 | 18,446 | 5 |
| BO 4 | 20 | 14 | 17,403 | 20 | 17,403 | 20 |
| GA 0 | 20 | 12 | 8,095 (unconverged) | 7 | 19,014 | 20 |
| GA 1 | 20 | 15 | 9,443 (unconverged) | 18 | 17,395 | 14 |
| GA 2 | 20 | 9 | 7,455 (unconverged) | 19 | 18,432 | 18 |
| GA 3 | 20 | 14 | 4,852 (unconverged) | 3 | 19,020 | 1 |
| GA 4 | 20 | 17 | 18,081 (unconverged) | 15 | 18,516 | 14 |
| MADS 0 | 20 | 17 | 16,695 | 18 | 16,695 | 18 |
| MADS 1 | 20 | 16 | 14,586 (unconverged) | 19 | 16,638 | 11 |
| MADS 2 | 20 | 17 | 16,638 | 20 | 16,638 | 20 |
| MADS 3 | 20 | 14 | 16,118 (unconverged) | 8 | 18,189 | 17 |
| MADS 4 | 20 | 15 | 11,591 (unconverged) | 18 | 18,248 | 13 |
| SLSQP (all 5 seeds) | 20 | 18 | 18,204 | 3 | 18,204 | 3 |
| Random 0 | 6 | 1 | 19,020 | 1 | 19,020 | 1 |
| Random 1 | 5 | 2 | 15,508 (unconverged) | 3 | 19,020 | 1 |
| Random 2 | 6 | 2 | 15,709 (unconverged) | 3 | 19,020 | 1 |
| Random 3 | 2 | 2 | 19,020 | 1 | 19,020 | 1 |
| Random 4 | 10 | 4 | 794 (unconverged) | 5 | 19,020 | 1 |

**Caution on "best fuel, any":** almost every low value comes from a mission that did not converge. These missions
stopped short of their range, so they burned less fuel because they flew less. Without the convergence filter, the
ranking flips: GA looks best by far, and S_SP becomes the best MAS combination.

## Fuel of every flown aircraft

Filled = the mission converged, hollow = it did not. Both figures use the same fuel axis.

![MAS: fuel per flight over each 20-link chain; dashed line = flight 20](figures/09_fuel_per_flight_mas.png)

![Optimizers: fuel per flight, one colour per seed](figures/09_fuel_per_flight_optimizers.png)

- **BO, GA and MADS:** they spread widely over the first ~10 flights (5,000–60,000 kg, many unconverged), then
  settle around 16,500–20,000 kg, mostly converged. MADS settles lowest.
- **SLSQP:** one sequence (the 5 seeds overlap), mostly converged, 18,000–35,000 kg.
- **Random search:** few flights, widely spread, mostly unconverged.
- **MAS re-flies identical designs.** The long flat rows are the same design flown again and again: N_GR flies
  ~22,700 kg about 35 times in a row (flights ~110–145), O_GR repeats one design about 12 times within its first 20
  flights, S_GR about 25 times. Each repeat counts as a MAS flight. A repeat proposed by an optimizer is answered
  from the cache and does not count. **Open question:** count identical MAS repeats once, as a second version of
  this comparison? It would move the graph-routed combinations a long way along the flight axis. It is a filter on
  the logs; nothing needs re-running.

## Other checks

- **SU2 aero not injected:** some optimizer designs had an SU2 lift coefficient outside Aviary's allowed range
  (0.05–1.0), so the SU2 drag was not passed to Aviary. None of them flew, so no flight in this comparison ran on
  Aviary's default drag polar (`data/09_optimizer_aero_not_injected.csv`).
- **BO relaunch (B236):** BO was restarted on 2026-10-31 at 19:50 to add the residual constraints. Each seed
  replayed its first 5 designs from the log, without re-running them. Those 5 records predate the residual field:
  converged missions get residual 0, unconverged ones get the reported value or the 1e6 fallback. GA, MADS, SLSQP
  and random ran entirely on the new code.
- **Designs tried for 20 flights:** BO 124, GA 124, MADS 129, SLSQP 105 (over 5 seeds); random 200 designs for 29
  flights.
- **Unconverged flights are common on both sides:** the MAS's first 20 flights converged 0–5 times; the
  optimizers' converged 9–18 times (random 1–4 of 2–10).

## Files and reproduction

- Script: `analysis/link20/budget_compare.py` (tests: `tests/test_link20_budget_compare.py`).
- Data copied here: `data/09_budget_compare_at_20_flights.csv`, `data/09_where_best_fuel_first_20_flights.csv`,
  `data/09_optimizer_aero_not_injected.csv`, `data/09_best_converged_saving_by_evaluation.csv`. On aipexws8
  (`~/Jessica/avion_analysis/out_20261028/budget_compare/`): the per-flight series `budget_compare_series.csv` and
  every evaluation's saving `fuel_saving_per_evaluation.csv`.
- Optimizer runs: aipexws8 `/data/Jessica/optimizer_budget/runs/<optimizer>/seed<k>/evaluations.jsonl`;
  W&B project `mas-aviary-optimizer-budget`.
- Optimizer code: MAS-Aviary branch `feat/optimizer-budget` (`scripts/optimizer_budget/`); aviary-mcp branch
  `feat/optimizer-mission-residuals`.

```
# aipexws8, from ~/Jessica/avion_analysis/code (Avion venv)
python -m analysis.link20.budget_compare --runs /data/Jessica/optimizer_budget/runs \
    --out ../out_20261028/budget_compare
```
