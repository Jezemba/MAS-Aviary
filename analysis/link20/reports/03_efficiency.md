# 03 — Efficiency: time, tokens and compute effort (20-link DLR-F25 study)

Data: ws8 `~/Jessica/avion_analysis/out_20261028/links.csv` (160 links = 8 combinations x 20) plus the raw step
logs, `result.json` and knowledge bases of every link. Script: `analysis/link20/reports/scripts/efficiency.py`.
All tables below are copied from its CSVs in ws8 `~/Jessica/avion_analysis/reports_20261028/efficiency/`
(file name given under each table). "n" = links counted. Hours are wall-clock hours unless stated otherwise.

Combinations: S = sequential, O = orchestrated, N = networked; SP = staged pipeline, IF = iterative feedback,
GR = graph-routed. Datasets: S_SP, S_IF, S_GR, O_SP, O_IF = `v2full` (2026-10-14 code, ws8); O_GR, N_IF =
`v3combos` (run on ws3); N_GR = `v4net` (fixed code, ws8).

## Key findings

1. **Sequential is fastest and cheapest; networked graph-routed is by far the slowest.** Mean link duration ranges
   from 0.51 h (S_SP, n=20) to 4.04 h (N_GR, n=18). The 20-link chain took 10.2 h for S_SP and 72.7 h for N_GR
   (88.6 h counting the two N_GR links that hit the 480-min timeout). By structure: sequential 0.92 h per link
   (n=60), orchestrated 1.52 h (n=58), networked 2.61 h (n=38).
2. **Orchestrated produces the most output tokens, and most of them are thinking tokens.** O_GR averages
   91.0k output tokens per link (n=20) against 18.3k for S_SP (n=20). The orchestrated strategy switches
   thinking *on* for every worker (`orchestrated.py`: `thinking_enabled = not is_orchestrator`), although the
   study is the "nothink" run: 86% (O_SP), 67% (O_IF) and 85% (O_GR) of orchestrated output tokens come from
   thinking-enabled generations; sequential and networked have 0%. Token counts are therefore not comparable
   across structures (see "Possible bugs").
3. **Duration and output tokens move together within a combination** (Spearman rho 0.73–0.99 per combination),
   but the token rate differs by structure: 590–669 output tokens per minute for the three sequential combinations and
   O_SP/O_IF, 899 for O_GR (thinking), and only 336–391 for networked (n per combination in Table 2).
4. **The model's own turns dominate the agent-step time, not the simulations.** Over all links, 321.1 h of
   agent-step time: steps with no tool call 64.2 h, board tools 43.5 h, SU2 44.5 h, aviary 40.7 h,
   recommendations 33.8 h, final_answer 26.9 h. The time SU2 and aviary *report themselves* is 2.8–9.2% of link
   duration (mean per combination) everywhere except N_GR (25.2%), which ran 191 successful SU2 solves over its
   20 links (median 5.5 per link) against 20–36 in every other combination.
5. **Coordination overhead is large where there is a coordinator or a board.** Orchestrator tools take 38.5% of
   O_IF's step time (message_orchestrator alone 10.9 h of 47.3 h); board tools take 49.2% of N_IF's step time
   (wait_for_board alone 15.3 h of 56.9 h). The orchestrator takes 28–30% of all agent steps in orchestrated
   combinations (mean share per link, n=20 each).
6. **No combination buys fuel with effort on average.** Over flown links, mean fuel is *above* the 18,197.0 kg
   baseline in every combination, so "fuel saved per hour / per million output tokens" is negative everywhere
   (e.g. S_SP −13,437 kg per hour, n=20; N_IF −1,346 kg per hour, n=7). Only O_GR (1 link) and N_IF (2 links)
   reach a converged mission below baseline.
7. **Later links are not faster or cheaper.** No combination shows a clear trend in duration or output tokens
   over the chain (|rho| ≤ 0.29, all p > 0.2, n=18–20). The only nominally significant trend is O_SP agent steps
   rising (rho 0.49, p 0.028, uncorrected for the 48 tests run).
8. **Networked peers overlap, but not by much.** Summed agent-step time is 2.21x the link duration for N_IF
   (3 peers, n=20) and 1.20x for N_GR (2 peers, n=18); sequential and orchestrated sit at 0.97–1.04x.

## 1. Link duration and failures

Duration = `result.json duration_seconds` of the attempt that counted. Failed links have no `result.json`.

| combo | n | mean (h) | median (h) | min (h) | max (h) | total over links (h) |
|---|---|---|---|---|---|---|
| S_SP | 20 | 0.512 | 0.488 | 0.265 | 0.885 | 10.231 |
| S_IF | 20 | 0.623 | 0.631 | 0.392 | 0.873 | 12.450 |
| S_GR | 20 | 1.629 | 1.348 | 0.985 | 2.565 | 32.580 |
| O_SP | 20 | 1.171 | 1.247 | 0.523 | 1.685 | 23.418 |
| O_IF | 18 | 1.735 | 1.419 | 0.523 | 4.635 | 31.230 |
| O_GR | 20 | 1.678 | 1.549 | 0.834 | 3.091 | 33.554 |
| N_IF | 20 | 1.334 | 1.340 | 0.526 | 2.245 | 26.689 |
| N_GR | 18 | 4.037 | 4.572 | 1.370 | 6.607 | 72.658 |

`duration_hours.csv`

| combo | failed | timed out (480 min) | total incl. step-log span of failed links (h) | links retried | earlier attempts, step-log span (h) | earlier attempts, summed step time (h) |
|---|---|---|---|---|---|---|
| S_SP | 0 | 0 | 10.23 | 2 | 0.98 | 0.97 |
| S_IF | 0 | 0 | 12.45 | 1 | 0.68 | 0.67 |
| S_GR | 0 | 0 | 32.58 | 0 | 0.00 | 0.00 |
| O_SP | 0 | 0 | 23.42 | 3 | 3.69 | 3.90 |
| O_IF | 2 | 1 | 40.30 | 6 | 31.83 | 44.20 |
| O_GR | 0 | 0 | 33.55 | 0 | 0.00 | 0.00 |
| N_IF | 0 | 0 | 26.69 | 5 | 39.30 | 21.44 |
| N_GR | 2 | 2 | 88.55 | 0 | 0.00 | 0.00 |

`status_timeouts.csv`, `chain_totals.csv`

- Timed-out links: O_IF 19, N_GR 6, N_GR 8. Their counted step logs span 28,790 s, 28,652 s and 28,556 s
  (7.93–8.00 h), consistent with the 480-min limit. O_IF 8 failed for zero fuel (3 attempts), not a timeout;
  its step log spans 3,869 s. No completed link reached 480 min (longest: N_GR, 6.61 h).
- Retries are not in `duration_s`. Earlier attempts add 31.8 h (span) to O_IF and 39.3 h to N_IF. N_IF link 15
  attempt 1 spans 31.9 h but contains a single 30.19 h gap between two consecutive steps (summed step time
  3.66 h), so its span is not working time.
- The step-log span (first step start to last step end) is 97.1–99.9% of `duration_s` for every link with both
  (the rest is setup before the first step and teardown after the last).

## 2. Tokens

Output tokens = sum of `output_tokens` over every generation in the counted step log (parse retries included).
`total_tokens` in `result.json` is wrong (B215) and is not used.

| combo | n | output tokens per link, mean (k) | median (k) | min (k) | max (k) | total (k) | output tokens per minute, mean (n) | per stepping agent, mean (k) | largest single agent, mean (k) |
|---|---|---|---|---|---|---|---|---|---|
| S_SP | 20 | 18.27 | 17.87 | 8.30 | 32.96 | 365.4 | 590 (20) | 2.61 | 6.35 |
| S_IF | 20 | 23.28 | 23.56 | 12.93 | 36.06 | 465.5 | 619 (20) | 3.33 | 8.38 |
| S_GR | 20 | 58.05 | 52.60 | 35.36 | 100.45 | 1,161.1 | 602 (20) | 11.84 | 22.93 |
| O_SP | 20 | 47.40 | 50.35 | 20.24 | 71.12 | 948.0 | 669 (20) | 5.96 | 17.22 |
| O_IF | 20 | 74.78 | 58.92 | 20.26 | 248.28 | 1,495.6 | 660 (18) | 9.74 | 32.96 |
| O_GR | 20 | 91.04 | 88.79 | 44.39 | 190.30 | 1,820.8 | 899 (20) | 12.20 | 32.82 |
| N_IF | 20 | 28.97 | 27.76 | 15.12 | 43.71 | 579.3 | 391 (20) | 9.66 | 13.53 |
| N_GR | 20 | 77.83 | 81.59 | 30.13 | 156.24 | 1,556.5 | 336 (18) | 38.91 | 67.54 |

`output_ktokens.csv`, `output_tokens_per_min.csv`, `output_ktokens_per_stepping_agent.csv`, `max_agent_output_ktokens.csv`

Tokens per minute use `duration_s`, so failed links drop out (n in brackets). "Per stepping agent" = output
tokens / number of agents that took at least one step (7 in sequential SP/IF; 7–8 in orchestrated including the
orchestrator; 3 peers in N_IF and 2 in N_GR in every link). The orchestrator's own share of output tokens is
small: mean 6.9% (O_SP), 19.0% (O_IF, median 7.3%, max 87.7%), 12.8% (O_GR), n=20 each
(`orchestrator_output_token_share.csv`).

**Thinking tokens** (`generation_coverage.csv`):

| combo | generations | thinking-enabled generations | output tokens from thinking-enabled generations | output tokens per generation |
|---|---|---|---|---|
| S_SP | 1,702 | 0 | 0% | 215 |
| S_IF | 1,970 | 0 | 0% | 236 |
| S_GR | 5,257 | 0 | 0% | 221 |
| O_SP | 1,891 | 1,213 | 86.0% | 501 |
| O_IF | 4,431 | 2,085 | 66.7% | 338 |
| O_GR | 3,187 | 2,153 | 84.8% | 571 |
| N_IF | 3,762 | 0 | 0% | 154 |
| N_GR | 8,040 | 0 | 0% | 194 |

**Prompt size.** `max_prompt_tokens` in `result.json` is the largest prompt *before* the B80 trim
(`context_budget.fit_to_budget` records it before trimming), not what the model saw. The prompt actually sent
is in the step logs for sequential and orchestrated only (networked step logs have no `prompt_tokens`).

| combo | n | largest untrimmed prompt, mean (tokens) | max | largest prompt sent, mean (tokens) | max | prompt tokens sent per link, mean (k) | context trims per link, mean |
|---|---|---|---|---|---|---|---|
| S_SP | 20 | 23,933 | 34,180 | 23,360 | 29,993 | 889 | 0.5 |
| S_IF | 20 | 25,662 | 129,213 | 20,316 | 27,644 | 1,091 | 0.15 |
| S_GR | 20 | 33,764 | 134,382 | 26,330 | 29,976 | 3,940 | 6.75 |
| O_SP | 20 | 18,085 | 26,813 | 18,085 | 26,813 | 821 | 0 |
| O_IF | 18 / 20 | 26,340 | 72,681 | 22,305 | 30,000 | 3,382 | 20.6 (n=18) |
| O_GR | 20 | 21,897 | 66,412 | 19,797 | 30,000 | 1,587 | 3.8 |
| N_IF | 20 | 41,882 | 101,611 | not recorded | — | not recorded | 40.2 |
| N_GR | 18 | 108,941 | 456,069 | not recorded | — | not recorded | 232.8 |

`max_prompt_tokens_untrimmed.csv`, `max_prompt_tokens_sent.csv`, `prompt_ktokens_sent_sum.csv`, `context_trims.csv`
(O_IF: 18 links with `result.json`, 20 with step logs.) No link recorded `prompt_over_limit_count` > 0. N_GR also
has 1,066 message truncations (18 links) and 705 summary-model calls in its step logs (3.14 h of summary-model
time from `kb_summary_seconds`); N_IF 170 calls / 0.50 h; every other combination 20–35 calls / 0.07–0.16 h.
Summary-model (Qwen3-4B) tokens are not in the output-token counts.

## 3. Agent steps

| combo | n | agent steps per link, mean | median | min | max | agents stepping, mean | orchestrator share of steps, mean |
|---|---|---|---|---|---|---|---|
| S_SP | 20 | 60.8 | 61.5 | 43 | 79 | 7.00 | — |
| S_IF | 20 | 67.5 | 64.5 | 55 | 84 | 7.00 | — |
| S_GR | 20 | 177.9 | 159 | 75 | 271 | 5.30 | — |
| O_SP | 20 | 75.8 | 78.5 | 53 | 90 | 7.95 | 28.9% |
| O_IF | 20 | 174.8 | 93 | 68 | 726 | 7.75 | 29.7% (median 20.4%) |
| O_GR | 20 | 133.8 | 122 | 58 | 247 | 7.40 | 28.4% |
| N_IF | 20 | 165.0 | 172.5 | 82 | 231 | 3.00 | — |
| N_GR | 20 | 359.3 | 393.5 | 174 | 525 | 2.00 | — |

`agent_steps.csv`, `agents_stepping.csv`, `orchestrator_step_share.csv`

## 4. Where the agent-step time goes

Agent-step time = wall time of each agent step (one model generation plus the tool call it makes), credited to
the tool it called (split evenly over several calls), summed over agents. Model time and tool time inside a step
cannot be separated: no CPU/GPU time is recorded and per-tool durations in `result.json` are all 0. Grouping is
the one used in `plots.py` (`5b_time_breakdown`); MCP server from the knowledge base `server` field.

Share of agent-step time (%), all links of each combination (n=20 each):

| combo | total (h) | no tool call | final_answer | recommendations | orchestrator tools | board tools | TiGL | SU2 | mass | pyCycle | aviary | other |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S_SP | 10.08 | 32.5 | 13.4 | 22.3 | 0.0 | 0.0 | 9.6 | 9.0 | 1.2 | 4.0 | 7.3 | 0.6 |
| S_IF | 12.29 | 37.1 | 9.8 | 26.2 | 0.0 | 0.0 | 7.7 | 7.3 | 1.1 | 3.4 | 7.1 | 0.3 |
| S_GR | 31.73 | 39.8 | 8.4 | 29.1 | 0.0 | 0.1 | 2.2 | 3.0 | 0.6 | 2.5 | 13.7 | 0.7 |
| O_SP | 23.26 | 9.4 | 17.6 | 27.3 | 5.7 | 0.0 | 4.7 | 7.9 | 1.0 | 8.5 | 17.7 | 0.3 |
| O_IF | 47.30 | 12.8 | 10.2 | 13.7 | 38.5 | 0.0 | 2.9 | 5.9 | 0.5 | 5.0 | 10.1 | 0.4 |
| O_GR | 33.86 | 3.1 | 23.8 | 8.4 | 15.0 | 0.2 | 1.3 | 11.2 | 0.7 | 17.7 | 17.8 | 0.7 |
| N_IF | 56.92 | 14.1 | 2.1 | 2.4 | 0.0 | 49.2 | 6.1 | 5.8 | 1.5 | 3.1 | 10.3 | 5.4 |
| N_GR | 105.69 | 25.0 | 3.3 | 1.9 | 0.0 | 14.6 | 6.7 | 28.4 | 1.4 | 2.1 | 13.2 | 3.4 |

`step_time_by_group_share_pct.csv`, hours in `step_time_by_group_total_hours.csv`, per tool in `step_time_by_tool_hours.csv`

Largest single tools (hours over 20 links, share of the combination's step time):
S_SP no tool call 3.28 h (32.5%), final_answer 1.35 h (13.4%), record_recommendation 1.29 h (12.8%);
S_GR no tool call 12.62 h (39.8%), respond_to_recommendation 5.21 h (16.4%);
O_SP record_recommendation 5.72 h (24.6%), final_answer 4.08 h (17.6%);
O_IF message_orchestrator 10.89 h (23.0%), assign_task 5.95 h (12.6%);
O_GR final_answer 8.07 h (23.8%), run_su2_solver 2.86 h (8.5%);
N_IF wait_for_board 15.27 h (26.8%), no tool call 8.00 h (14.1%), claim_todo 4.24 h (7.5%);
N_GR no tool call 26.44 h (25.0%), run_su2_solver 24.51 h (23.2%), run_simulation 6.92 h (6.5%).

Note: the v2full combinations (S_*, O_SP, O_IF) carry the pre-fix record_recommendation loop, which inflates
their "recommendations" time; compare that column only within a code version.

**Time the simulations report themselves** (knowledge base `runtime_seconds` of successful `run_su2_solver` and
`run_simulation`; the only tool-side times recorded):

| combo | successful SU2 solves (20 links) | SU2 reported (h) | SU2 step time (h) | aviary run_simulation reported (h) | aviary step time, all aviary tools (h) | reported SU2 + aviary as share of duration, mean (n) |
|---|---|---|---|---|---|---|
| S_SP | 21 | 0.70 | 0.91 | 0.18 | 0.74 | 9.2% (20) |
| S_IF | 20 | 0.68 | 0.90 | 0.11 | 0.87 | 6.7% (20) |
| S_GR | 20 | 0.70 | 0.95 | 0.32 | 4.33 | 2.8% (20) |
| O_SP | 23 | 0.76 | 1.83 | 0.14 | 4.12 | 4.1% (20) |
| O_IF | 36 | 1.15 | 2.78 | 0.07 | 4.79 | 3.7% (18) |
| O_GR | 33 | 2.50 | 3.80 | 0.27 | 6.02 | 8.5% (20) |
| N_IF | 28 | 2.10 | 3.29 | 0.03 | 5.87 | 8.7% (20) |
| N_GR | 191 | 22.03 | 29.99 | 3.41 | 13.92 | 25.2% (18) |

`reported_sim_runtimes.csv`

## 5. Effort versus outcome

Definitions (flown links only, i.e. `flew == True`, and with a `duration_s`): fuel saved = 18,197.0 kg − link fuel
(`fuel_kg`, last successful mission of the link, B102 rule, **no convergence check**). "Per hour" and "per million
output tokens" are ratios of sums over the links (sum of fuel saved / sum of hours or Mtok). This is not the
marginal effect of a link: in a cumulative chain each link's fuel is the state of the whole chain so far.
Negative = more fuel than the baseline.

| combo | subset | n | mean duration (h) | mean output tokens (k) | mean fuel saved (kg) | fuel saved per hour (kg/h) | fuel saved per Mtok (kg) |
|---|---|---|---|---|---|---|---|
| S_SP | flown | 20 | 0.51 | 18.27 | −6,874 | −13,437 | −376,248 |
| S_SP | converged | 5 | 0.53 | 19.13 | −9,106 | −17,026 | −476,095 |
| S_IF | flown | 20 | 0.62 | 23.28 | −3,825 | −6,144 | −164,323 |
| S_IF | converged | 1 | 0.69 | 26.80 | −2,544 | −3,701 | −94,919 |
| S_GR | flown | 16 | 1.76 | 62.37 | −7,259 | −4,126 | −116,387 |
| S_GR | converged | 2 | 2.17 | 77.49 | −7,047 | −3,243 | −90,942 |
| O_SP | flown | 20 | 1.17 | 47.40 | −13,711 | −11,710 | −289,272 |
| O_SP | converged | 5 | 0.89 | 34.98 | −11,825 | −13,308 | −338,013 |
| O_IF | flown | 18 | 1.73 | 67.22 | −7,150 | −4,121 | −106,367 |
| O_IF | converged | 9 | 1.64 | 65.21 | −7,884 | −4,802 | −120,898 |
| O_GR | flown | 20 | 1.68 | 91.04 | −12,772 | −7,613 | −140,292 |
| O_GR | converged | 11 | 1.64 | 88.49 | −12,154 | −7,426 | −137,350 |
| N_IF | flown | 7 | 1.01 | 25.77 | −1,355 | −1,346 | −52,568 |
| N_IF | converged | 3 | 0.79 | 24.48 | −1,991 | −2,535 | −81,328 |
| N_GR | flown | 16 | 4.05 | 74.96 | −14,001 | −3,459 | −186,782 |
| N_GR | converged | 0 | — | — | — | — | — |

`effort_vs_outcome.csv` (also per-link medians). O_IF link 19 and N_GR links 6 and 8 flew but have no duration,
so they are not in this table (flown links incl. those: O_IF 19, N_GR 18).

Whole chain (all 20 links), `chain_totals.csv`:

| combo | chain hours (recorded) | chain output tokens (M) | flown | flown converged | flown below baseline | converged below baseline | best converged fuel (kg) |
|---|---|---|---|---|---|---|---|
| S_SP | 10.23 | 0.365 | 20 | 5 | 3 | 0 | 23,553.6 |
| S_IF | 12.45 | 0.466 | 20 | 1 | 0 | 0 | 20,740.5 |
| S_GR | 32.58 | 1.161 | 16 | 2 | 0 | 0 | 22,810.5 |
| O_SP | 23.42 | 0.948 | 20 | 5 | 0 | 0 | 26,952.6 |
| O_IF | 31.23 (18 links) | 1.496 | 19 | 9 | 1 | 0 | 19,671.7 |
| O_GR | 33.55 | 1.821 | 20 | 11 | 1 | 1 | 18,125.8 |
| N_IF | 26.69 | 0.579 | 7 | 3 | 2 | 2 | 17,520.5 |
| N_GR | 72.66 (18 links) | 1.557 | 18 | 0 | 4 | 0 | none converged |

Only O_GR (best converged 18,125.8 kg, 71.2 kg below baseline, after 33.6 h and 1.82 M output tokens) and N_IF
(17,520.5 kg, 676.5 kg below baseline, after 26.7 h and 0.58 M output tokens) ever reach a converged mission
below the baseline. The lowest fuel of all, N_GR 6,668.9 kg, is unconverged and should not be read as a
result. All fuels with no convergence check: see `01_physics_outcomes.md`.

## 6. Trend over the chain

Spearman rho of link number vs the metric, per combination (n=20; 18 for duration-based metrics in O_IF and
N_GR); p uncorrected. Means of links 1–5 and 16–20, n=5 each (O_IF links 16–20 duration: n=4).

| combo | rho duration (p) | duration links 1–5 → 16–20 (h) | rho output tokens (p) | tokens links 1–5 → 16–20 (k) | rho agent steps (p) | steps links 1–5 → 16–20 |
|---|---|---|---|---|---|---|
| S_SP | 0.12 (0.61) | 0.49 → 0.54 | 0.06 (0.80) | 17.8 → 19.5 | 0.36 (0.12) | 59.8 → 65.6 |
| S_IF | 0.24 (0.30) | 0.59 → 0.61 | 0.22 (0.34) | 22.5 → 22.9 | 0.31 (0.19) | 62.2 → 70.0 |
| S_GR | −0.29 (0.22) | 2.09 → 1.73 | −0.25 (0.29) | 68.8 → 59.5 | −0.23 (0.32) | 221.0 → 194.2 |
| O_SP | −0.20 (0.41) | 1.44 → 1.13 | −0.18 (0.45) | 57.7 → 45.5 | 0.49 (0.028) | 70.6 → 78.4 |
| O_IF | −0.21 (0.40) | 1.57 → 1.84 | −0.01 (0.98) | 63.7 → 105.2 | 0.08 (0.75) | 87.8 → 294.6 |
| O_GR | 0.14 (0.56) | 1.75 → 1.83 | 0.22 (0.35) | 102.2 → 104.4 | 0.06 (0.81) | 123.8 → 140.2 |
| N_IF | −0.04 (0.87) | 1.35 → 1.43 | 0.06 (0.80) | 26.0 → 28.6 | −0.08 (0.72) | 164.4 → 164.4 |
| N_GR | 0.27 (0.28) | 3.96 → 4.59 | 0.20 (0.40) | 87.6 → 82.5 | 0.39 (0.09) | 341.0 → 421.0 |

`trend_over_chain.csv` (also tokens per minute, step time and largest prompt). The O_IF late-link jump in
tokens and steps comes from link 19 (timed out, 248k tokens) and link 20 (12,526 s); the medians do not move
the same way. The untrimmed prompt size of N_GR grows from a mean 75,048 (links 1–5) to 158,387 tokens
(links 16–20), rho 0.37 (p 0.13): the knowledge base grows along the chain and the trimmer works harder.

## 7. Parallelism (networked)

Ratio of summed agent-step time to link duration (n = links with a duration):

| combo | n | mean | median | min | max |
|---|---|---|---|---|---|
| S_SP | 20 | 0.985 | 0.985 | 0.977 | 0.991 |
| S_IF | 20 | 0.987 | 0.986 | 0.981 | 0.991 |
| S_GR | 20 | 0.973 | 0.972 | 0.961 | 0.982 |
| O_SP | 20 | 0.992 | 0.993 | 0.982 | 1.012 |
| O_IF | 18 | 1.039 | 0.994 | 0.984 | 1.413 |
| O_GR | 20 | 0.999 | 0.979 | 0.932 | 1.199 |
| N_IF | 20 | 2.214 | 2.156 | 1.634 | 2.875 |
| N_GR | 18 | 1.202 | 1.198 | 1.050 | 1.359 |

`step_time_over_duration.csv` (and `step_time_over_steplog_span.csv`, which also covers the failed links)

- N_IF runs 3 peers and reaches 2.21x on average, but 26.8% of its step time is `wait_for_board`, i.e. a peer
  waiting for another. Busy parallel work is therefore lower than the ratio suggests.
- N_GR runs 2 peers and reaches only 1.20x on average (max 1.36x), so its two peers seldom work at the same
  time.
- O_IF links 7, 9, 20 and O_GR links 8, 13 exceed 1.05x. Orchestrated runs one model call at a time
  (`max_concurrent_generations` = 1 in every orchestrated link), so the excess is overlapping step intervals.
  Checked on O_IF link 20 and O_GR link 8: the orchestrator's message-turn steps run *inside* a worker's
  `message_orchestrator` step (1.47 h nested in O_IF link 20, 0.42 h in O_GR link 8), so the same wall time is
  counted twice. This inflates the orchestrator-tools share, mostly in O_IF.

## 8. By structure and handler

| structure | links | n with duration | mean duration (h) | median (h) | total (h) | mean output tokens (k) | median (k) | mean tokens/min | mean agent steps | mean step time (h) |
|---|---|---|---|---|---|---|---|---|---|---|
| sequential | 60 | 60 | 0.921 | 0.669 | 55.26 | 33.20 | 24.80 | 604 | 102.0 | 0.90 |
| orchestrated | 60 | 58 | 1.521 | 1.399 | 88.20 | 71.07 | 60.27 | 746 | 128.1 | 1.74 |
| networked | 40 | 38 | 2.614 | 2.044 | 99.35 | 53.40 | 39.14 | 365 | 262.1 | 4.07 |

| handler | links | n with duration | mean duration (h) | median (h) | total (h) | mean output tokens (k) | median (k) | mean tokens/min | mean agent steps | mean step time (h) |
|---|---|---|---|---|---|---|---|---|---|---|
| staged pipeline | 40 | 40 | 0.841 | 0.652 | 33.65 | 32.83 | 23.73 | 629 | 68.3 | 0.83 |
| iterative feedback | 60 | 58 | 1.213 | 0.914 | 70.37 | 42.34 | 27.78 | 553 | 135.7 | 1.94 |
| graph-routed | 60 | 58 | 2.393 | 1.976 | 138.79 | 75.64 | 73.77 | 622 | 223.7 | 2.86 |

`by_structure.csv`, `by_handler.csv`. Staged pipeline exists only for sequential and orchestrated, so the handler
means are not balanced over structures, and the structure means mix three code versions.

## Plots

ws8 `~/Jessica/avion_analysis/out_20261028/plots/`:
- `5_general/duration_{avg_box,avg_strip,line_*}.png`, `output_tokens_*.png`, `tokens_per_minute_*.png`,
  `agent_step_time_*.png`
- `5_general/duration_vs_output_tokens_scatter.png`, `5_general/step_time_vs_output_tokens_scatter.png`
- `5b_time_breakdown/time_by_tool_group_{hours,share}.png`, `5b_time_breakdown/time_top_tools_<structure>_<handler>.png`

The 5b plots merge "no tool call" and "final_answer" into one "model only" group; the tables above keep them apart.

## Caveats

- **Three code versions and two machines.** v2full (S_*, O_SP, O_IF) and v4net (N_GR) ran on ws8; v3combos
  (O_GR, N_IF) ran on ws3. Durations depend on the machine (SU2 picks its MPI ranks from the free cores) and on
  whatever else ran there, which is not recorded. Cross-dataset duration comparisons mix hardware and code.
- **No CPU/GPU time.** Agent-step time includes the model's generation and the tool call together. Only SU2 and
  aviary report their own runtime.
- **Output tokens only.** Input tokens are not saved for networked; the prompt tokens sent are in the step logs
  for sequential and orchestrated only. The summary model's tokens (Qwen3-4B) are not counted.
- **Thinking was on for orchestrated workers** (finding 2), so output tokens, tokens per minute and probably
  duration are not like-for-like between orchestrated and the other two structures.
- **Failed links**: O_IF 8 and 19, N_GR 6 and 8 have no `duration_s`; they are in token/step tables (from step
  logs) but not in duration-based ones. Retries (17 links) are not in any duration; see Section 1.
- **Fuel** has no convergence check (B102 rule); unconverged missions can show unrealistically low fuel (N_GR
  6,668.9 kg). Pre-fix v2full links include B187/B214 contamination; this report keeps every link.
- Trend p-values are uncorrected for the 48 tests run (8 combinations x 6 metrics).
- `result.json generations` counts first-attempt generations only (equal to the step-log count with
  `attempt == 0` in S_SP, S_IF, S_GR, O_SP, O_GR; O_IF lacks 2 result.json; networked records 0); output tokens
  here include parse retries.

## Possible bugs / suspicious (for Jessica)

1. **Thinking on in the "nothink" study, orchestrated only.** `src/coordination/strategies/orchestrated.py`
   line ~420 sets `self._model.thinking_enabled = not is_orchestrator` on every coordination step, overriding
   `enable_thinking: false` from the model config. 1,213 of 1,891 (O_SP), 2,085 of 4,431 (O_IF) and 2,153 of
   3,187 (O_GR) generations ran with thinking enabled; sequential and networked: 0. This affects tokens, time
   and possibly decision quality of every orchestrated link, in both v2full and v3combos.
2. **Networked `result.json` has no generation counters.** `max_concurrent_generations` is 0 and `generations`
   is 0 for all N_IF and N_GR links, and step logs have no `prompt_tokens`; `generation_wait_seconds` is 0
   everywhere (all combinations). Parallelism and input tokens can't be read from the run's own counters.
3. **Overlapping steps in orchestrated** (Section 7): summed step time > duration in 5 links (up to 1.41x,
   O_IF link 20): the orchestrator's message turn is logged as its own steps *and* inside the worker's
   `message_orchestrator` step (checked on O_IF 20 and O_GR 8). The step-time breakdown double-counts that time.
4. **N_IF link 15, attempt 1**: a 30.19 h gap between two consecutive steps inside one attempt (span 31.9 h).
   Something stalled or paused the run; worth a look at the v3combos log on ws3.
5. **N_GR SU2 volume**: 191 successful SU2 solves (22.0 h reported) vs 20–36 in every other combination;
   untrimmed prompts up to 456,069 tokens, 232.8 context trims per link, 1,066 message truncations. Fixed code,
   but the graph-routed networked loop re-runs SU2 far more often.

## How to reproduce

On aipexws8: `cd ~/Jessica/avion_analysis/code && source ~/Jessica/Avion/.venv/bin/activate && python -m
analysis.link20.reports.scripts.efficiency --links ~/Jessica/avion_analysis/out_20261028/links.csv --out
~/Jessica/avion_analysis/reports_20261028/efficiency` (prints every table to stdout; saved there as `run_output.txt`).
