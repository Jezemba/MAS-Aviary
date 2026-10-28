# 04 — Coordination and organisational design (20-link DLR-F25 study)

Organisational structure (sequential S / orchestrated O / networked N) and handler (staged pipeline SP / iterative
feedback IF / graph-routed GR) behaviour over 8 combinations × 20 links = 160 links.

Every number below is printed by `analysis/link20/reports/scripts/coordination.py` (tables in ws8
`~/Jessica/avion_analysis/reports_20261028/coordination/*.csv`, full printout in `run_output.txt` there).
Averages are written `mean ± std (n = links counted)`. "Per link" = per 20-link chain iteration. A gap means the
link did not record the metric; gaps are never counted as 0.

## Key findings

1. **Networked graph-routed does by far the most coordination work, sequential/orchestrated staged pipeline the
   least.** Agent steps per link: N_GR 359 ± 122 (n=20), S_GR 178 ± 54.7, O_IF 175 ± 183, N_IF 165 ± 43.9,
   O_GR 134 ± 47.8, O_SP 75.8 ± 9.26, S_IF 67.5 ± 9.57, S_SP 60.8 ± 9.2 (all n=20). Pooled by handler: graph-routed
   224 ± 127 (n=60, 3 combinations), iterative feedback 136 ± 118 (n=60, 3), staged pipeline 68.3 ± 11.9 (n=40, 2).
   Pooled by structure: networked 262 ± 134 (n=40, 2), orchestrated 128 ± 115 (n=60, 3), sequential 102 ± 62.9
   (n=60, 3). These pools are unbalanced (networked has no staged pipeline) and mix code versions, so the structure
   ranking is partly a handler effect.
2. **Iterative feedback retries scale with decentralisation.** Retried turns per link: S_IF 0.55 ± 0.759 (n=20, 8
   links with any retry), O_IF 2.2 ± 1.54 (n=20, 16 links), N_IF 7.3 ± 1.84 (n=20, all 20 links). Stuck-detection
   stops: S_IF 0.1 (2 links), O_IF 0.75 (14 links), N_IF 2.5 (19 links). N_IF is also the combination that flew least
   (7 of 20 links).
3. **Graph-routed links almost never finish their route; they stop on a budget.** Of 60 graph-routed links, only 3
   (O_GR links 9, 13, 14) stopped with `complete`. S_GR: 20/20 stopped on a role-run budget; O_GR: 17/20 on a
   budget; N_GR: 13/20 on the 30-board-cycle cap, 3 on the role-run budget, 4 with no stop reason recorded (links 6
   and 8 timed out; links 9 and 19 have `stop_reason: null` in design.json). N_GR ended at the `complex` level on
   19/20 links, versus 9/20 (S_GR) and 8/20 (O_GR).
4. **The orchestrator takes about 28–30 % of all agent steps in every orchestrated combination**
   (orchestrator steps ÷ agent steps, derived here: O_SP 0.289 ± 0.039, O_IF 0.297 ± 0.206, O_GR 0.284 ± 0.086,
   n=20 each). The recorded `od.orchestrator_overhead_ratio` (0.5 / 0.099 / 0.075) counts messages, not steps, and is
   0.5 by construction in O_SP — it should not be used to compare orchestration cost across handlers.
5. **On the networked board, a large share of claim attempts is refused, and N_GR keeps re-taking TODOs.**
   claim_todo calls per link: N_IF 19.2 ± 5.43 (9.8 granted, 9.45 refused), N_GR 42.6 ± 18.0 (30.4 granted, 12.2
   refused), n=20 each. TODO revisit rate (share of granted claims that re-take a TODO already claimed in the link):
   N_GR 0.774 ± 0.102 vs N_IF 0.327 ± 0.162 (n=20 each). `release_todo` was never called (0 in all 40 links).
   N_IF waits on the board about 4× as often (26.1 ± 18.0 vs 6.5 ± 6.57 wait_for_board calls per link).
6. **No coordination metric trends over the 20 links.** Of 72 per-combination Spearman tests (metric vs link
   number), 1 reaches p < 0.05 (O_SP agent steps, rho = 0.491, p = 0.028), fewer than the 72 × 0.05 = 3.6 expected
   by chance alone. Coordination behaviour is stationary along the chain.
7. **Coordination activity tracks the outcome only weakly and mostly in S_GR.** Within S_GR, links that flew had
   longer routes (20.6 role runs, n=16) than links that did not (13.25, n=4), and among flown S_GR links more agent
   steps went with lower fuel (Spearman rho = −0.581, p = 0.018, n=16). Of 37 within-combination outcome tests, the
   only 4 with p < 0.05 are all S_GR (agent steps, turns, agents stepping, route length — not independent). In N_IF,
   links that did not fly marked more TODOs failed (6.46, n=13) than links that flew (3.57, n=7).
8. **Several recorded org-theory metrics are constant or defaulted by construction** (details in Caveats):
   `claim_conflicts` = 0 everywhere (defaulted), `od.information_ratio` = 1.0 in all O_GR links (worker messages
   carry no token count), `od.orchestrator_overhead_ratio` = 0.5 in all O_SP links, the staged-pipeline
   `early_abort` flag fires on 41 stages whose agent did get `valid: true`, and the networked blackboard
   writes/utilisation are fallback estimates.

## 1. All combinations

| Combo | Dataset | Agent steps | total_turns (messages) | Agents stepping | Runner attempts | Links with >1 attempt |
|---|---|---|---|---|---|---|
| S_SP | v2full | 60.8 ± 9.2 (n=20) | 7 ± 0 (n=20) | 7 ± 0 (n=20) | 1.1 ± 0.308 (n=20) | 8, 19 |
| S_IF | v2full | 67.5 ± 9.57 (n=20) | 7.55 ± 0.759 (n=20) | 7 ± 0 (n=20) | 1.05 ± 0.224 (n=20) | 18 |
| S_GR | v2full | 178 ± 54.7 (n=20) | 19.1 ± 5.64 (n=20) | 5.3 ± 1.69 (n=20) | 1 ± 0 (n=20) | none |
| O_SP | v2full | 75.8 ± 9.26 (n=20) | 14 ± 0 (n=20) | 7.95 ± 0.224 (n=20) | 1.15 ± 0.366 (n=20) | 9, 16, 19 |
| O_IF | v2full | 175 ± 183 (n=20) | 12.9 ± 5.79 (n=18) | 7.75 ± 1.12 (n=20) | 1.5 ± 0.827 (n=20) | 4, 7, 8, 13, 16, 20 |
| O_GR | v3combos | 134 ± 47.8 (n=20) | 19.3 ± 5.48 (n=20) | 7.4 ± 0.503 (n=20) | 1 ± 0 (n=20) | none |
| N_IF | v3combos | 165 ± 43.9 (n=20) | 12.4 ± 3.02 (n=20) | 3 ± 0 (n=20) | 1.3 ± 0.571 (n=20) | 2, 3, 7, 15, 17 |
| N_GR | v4net | 359 ± 122 (n=20) | 30.2 ± 7.28 (n=18) | 2 ± 0 (n=20) | 1 ± 0 (n=20) | none |

- Agent steps come from the counted step log (highest `step_log_attempt<k>`), so they exist for every link;
  `total_turns` is result.json's message count (equal to `len(messages)` on every link checked) and is a gap on the 4
  links without result.json (O_IF 8, 19; N_GR 6, 8).
- O_IF's large spread is one link: **O_IF link 19 (failed) took 726 agent steps, 533 of them orchestrator steps, with
  444 `assign_task` calls and 71 `message_orchestrator` calls**; the O_IF medians are 93 agent steps and 15
  orchestrator steps (n=20). Medians are a fairer O_IF summary.
- "Agents stepping" counts distinct step-log agent names: 7 roles in sequential; roles + orchestrator in orchestrated;
  peers (agent_1..3) in networked — so N_GR runs 2 peers and N_IF 3.
- Plots: `out_20261028/plots/4_coordination/agent_steps_*`, `runner_attempts_*`.

## 2. Staged pipeline (S_SP, O_SP)

| Metric | S_SP | O_SP |
|---|---|---|
| stage_count (recorded) | 7 ± 0 (n=20) | 14 ± 0 (n=20) — 7 worker + 7 orchestrator turns |
| Stages not meeting completion | 1.05 ± 0.224 (n=20; ≥1 in all 20 links) | 0.3 ± 0.733 (n=20; ≥1 in 4 links) |
| Stages re-done | 0 (n=20) | 0 (n=20) |
| Completion rate, workers only | 0.85 ± 0.0319 (n=20; min 0.714, max 0.857) | 0.957 ± 0.105 (n=20; min 0.571, max 1) |
| Completion rate, as recorded | 0.85 ± 0.0319 (n=20) | 0.979 ± 0.0523 (n=20) |
| `early_abort` flags per link | 1 ± 0 (n=20) | 1.05 ± 0.394 (n=20; 19 links) |

- Completion is the handler's own `completion_met` per stage (B171), not the keyword heuristic. O_SP's recorded rate
  includes the orchestrator's turns, which always pass; use the workers-only row.
- No stage was ever re-done in either staged combination: the staged pipeline runs each role once per link.
- S_SP never reaches full completion (best link 6/7 stages met); O_SP meets all worker stages in 16/20 links.
- `early_abort` is suspicious, see Caveats: it is set on mission_architect in every S_SP link and 19 O_SP links (plus
  mdo_integrator in O_SP links 1 and 6), yet the pipeline continued past it every time, and the knowledge base shows
  a `set_aircraft_parameters` with `valid: true` by that agent in all 41 flagged stages.
- Plots: `4_coordination/stages_not_met_*`, `stages_redone_*`, `early_aborts_*`; `7_org_design/completion_rate_*`.

## 3. Iterative feedback (S_IF, O_IF, N_IF)

| Metric | S_IF | O_IF | N_IF |
|---|---|---|---|
| Retried turns (`is_retry` messages) | 0.55 ± 0.759 (n=20; 8 links >0) | 2.2 ± 1.54 (n=20; 16 links) | 7.3 ± 1.84 (n=20; 20 links) |
| Stuck-detection stops | 0.1 ± 0.308 (n=20; 2 links) | 0.75 ± 0.55 (n=20; 14 links) | 2.5 ± 1.4 (n=20; 19 links) |
| Early aborts | 0 (n=20) | 0 (n=20) | 0 (n=20) |
| `mean_attempts_to_success` (recorded) | 1.08 ± 0.109 (n=20) | 1.62 ± 0.723 (n=18) | 4.13 ± 1.01 (n=20) |
| Ambidexterity score | 0.00245 ± 0.00462 (n=8) | 0.0228 ± 0.0281 (n=16) | 0.0365 ± 0.0322 (n=20) |
| Escalation of commitment (turns) | 0.1 ± 0.308 (n=20) | 0.278 ± 0.752 (n=18) | 0.5 ± 0.827 (n=20) |

- **What the recorded metrics actually measure** (`src/logging/org_theory_metrics.py`, `_iterative_feedback_metrics`):
  - `mean_attempts_to_success` = messages ÷ distinct agent names. It is not "attempts until success": in N_IF the
    "agents" are peers that each take several TODOs, so 4.13 mostly reflects how many turns a peer takes; in O_IF
    the orchestrator counts as an agent.
  - Ambidexterity = mean over agents with ≥2 messages of the variance of consecutive-message TF-IDF similarity. It is
    undefined (gap, not 0) when no agent has two messages: S_IF has it on 8/20 links (exactly the 8 retry links),
    O_IF on 16/18 (the 16 retry links). In N_IF it is defined on all 20 links because peers always have several
    messages, so N_IF's value is not a retry measure and is not comparable with S_IF/O_IF.
  - Escalation of commitment = longest run of consecutive messages from one agent with similarity > 0.8.
- `is_retry` messages also occur outside IF (S_GR 1.05, O_GR 3.9 per link); they are not used here.
- Plots: `4_coordination/retries_*`, `stuck_stops_*`, `attempts_to_success_*`; `7_org_design/ambidexterity_*`,
  `escalation_of_commitment_*`.

## 4. Graph-routed (S_GR, O_GR, N_GR)

| Metric | S_GR | O_GR | N_GR |
|---|---|---|---|
| Route length (role runs) | 19.1 ± 5.64 (n=20) | 18 ± 5.63 (n=20) | 15.1 ± 6.94 (n=20) |
| Backward moves on the route | 4.3 ± 1.08 (n=20) | 2.55 ± 1.05 (n=20) | gap (peers, not roles) |
| Final level simple / moderate / complex (links) | 1 / 10 / 9 | 0 / 12 / 8 | 0 / 1 / 19 |
| Complexity escalations per link | 1.2 ± 0.523 (n=20) | 0.4 ± 0.503 (n=20) | 1.3 ± 0.47 (n=20) |
| `request_complexity` calls | 0.35 ± 0.933 (n=20) | 0.25 ± 0.444 (n=20) | 9.9 ± 9.24 (n=20) |
| `set_complexity` calls (orchestrator) | — | 1.4 ± 0.754 (n=20) | — |
| Board reopenings | — | — | 1.3 ± 0.979 (n=20; 16 links) |
| Routing accuracy = 1 − misroute rate | 0.235 ± 0.0633 (n=20) | 0.385 ± 0.156 (n=20) | not used (see Caveats) |

Stop reasons (links):

| Combo | Stop reason | Links |
|---|---|---|
| S_GR | budget of 15 role runs at moderate spent | 10 (3, 6, 7, 8, 9, 11, 12, 13, 14, 16) |
| S_GR | budget of 25 role runs at complex spent | 9 (1, 2, 4, 5, 10, 17, 18, 19, 20) |
| S_GR | budget of 8 role runs at simple spent | 1 (15) |
| O_GR | budget of 15 role runs at moderate spent | 11 (1, 3, 4, 5, 6, 11, 12, 15, 16, 19, 20) |
| O_GR | budget of 25 role runs at complex spent | 6 (2, 7, 8, 10, 17, 18) |
| O_GR | complete | 3 (9, 13, 14) |
| N_GR | 30 board cycles dispatched | 13 (1, 3, 5, 7, 10–15, 17, 18, 20) |
| N_GR | budget of 25 role runs at complex spent | 3 (2, 4, 16) |
| N_GR | none recorded | 4 (6, 8 = timed out; 9, 19 = `stop_reason: null` in design.json) |

- Route, backward moves, level and escalations come from the per-turn `graph_route` message metadata (S/O) or the
  router's own `design.json → graph_route` record (N_GR, where `route_length` = `role_runs`).
- Misroute rate (recorded) = share of transitions that return to an already-visited state; since the MDO graph's
  messages carry no `graph_state`, the state is the agent name (role) in S_GR/O_GR. A 7-role pipeline that runs 19
  roles must revisit, so low "routing accuracy" here mostly says "the route loops", not "the router chose wrongly".
  O_GR's message sequence also contains the orchestrator's message(s).
- The orchestrator in O_GR sets complexity directly (1.4 set_complexity per link) and escalates least
  (0.4 per link, 8 links), and is the only graph-routed combination that ever reaches `complete`.
- Plots: `4_coordination/route_length_*`, `backward_moves_*`, `complexity_level_*`, `complexity_escalations_*`,
  `complexity_requests_*`, `set_complexity_*`, `board_reopenings_*`, `graph_stop_reason_avg_bar.png`,
  `graph_stop_reason_line.png`; `7_org_design/routing_accuracy_*`.

## 5. Orchestrated (O_SP, O_IF, O_GR)

| Metric | O_SP | O_IF | O_GR |
|---|---|---|---|
| Orchestrator steps | 21.6 ± 1.82 (n=20; median 21) | 79.2 ± 136 (n=20; median 15) | 37.9 ± 20.3 (n=20; median 31.5) |
| Orchestrator share of agent steps (derived) | 0.289 ± 0.0391 (n=20) | 0.297 ± 0.206 (n=20; median 0.204) | 0.284 ± 0.0858 (n=20) |
| Orchestrator turns (messages) | 7 ± 0 (n=20) | 1.22 ± 0.428 (n=18) | 1.3 ± 0.571 (n=20) |
| Worker turns (messages) | 7 ± 0 (n=20) | 11.7 ± 5.44 (n=18) | 18 ± 5.63 (n=20) |
| Workers used (`agents_spawned`) | 6.95 ± 0.224 (n=20) | 7 ± 0 (n=18) | 6.4 ± 0.503 (n=20) |
| create_agent | 6.95 ± 0.224 (n=20) | 8.05 ± 2.33 (n=20) | 8.3 ± 2.7 (n=20) |
| assign_task | 7.1 ± 0.447 (n=20) | 54.5 ± 107 (n=20; median 7) | 14.5 ± 7.8 (n=20; median 11.5) |
| message_orchestrator (worker → orchestrator) | 0.15 ± 0.671 (n=20; 1 link) | 10.9 ± 21.5 (n=20; 6 links) | 3.8 ± 8.6 (n=20; 10 links) |
| reply_to_agent | 0.2 ± 0.894 (n=20; 1 link) | 2.2 ± 8.46 (n=20; 4 links) | 2.25 ± 5.87 (n=20; 5 links) |
| set_complexity | 0 | 0 | 1.4 ± 0.754 (n=20) |
| `od.orchestrator_overhead_ratio` (recorded) | 0.5 ± 0 (n=20) | 0.0988 ± 0.0192 (n=18) | 0.0747 ± 0.0406 (n=20) |
| `od.cost_per_agent` (orchestrator s per worker) | 36.8 ± 5.76 (n=20) | 56.1 ± 33.8 (n=18) | 74.2 ± 53.8 (n=20) |
| `od.information_ratio` (recorded) | 0.0225 ± 0.00664 (n=20) | 0.0288 ± 0.052 (n=18) | 1 ± 0 (n=20) |

- The orchestrator's own spend is steady at roughly 28–30 % of steps (derived share) regardless of handler; what
  changes is how much it talks to workers. O_SP is close to a scripted pipeline (≈7 assign_task, almost no
  messaging); O_IF and O_GR open a two-way channel (message_orchestrator in 6 and 10 links).
- O_IF link 19 is the outlier (444 assign_task, 71 message_orchestrator, 533 orchestrator steps, failed run).
- `orchestrator_overhead_ratio` = orchestrator messages ÷ all messages. In O_SP the handler logs one orchestrator
  message per stage, so it is 0.5 by construction; in O_IF/O_GR the orchestrator emits ~1 message per link however
  many steps it takes. Use the step share instead.
- `cost_per_agent` = summed orchestrator-message duration ÷ distinct workers (seconds).
- Plots: `4_coordination/orchestrator_steps_*`, `create_agent_*`, `assign_task_*`, `message_orchestrator_*`,
  `reply_to_agent_*`, `set_complexity_*`; `7_org_design/orchestrator_overhead_*`, `cost_per_agent_*`,
  `information_ratio_*`.

## 6. Networked (N_IF, N_GR)

| Metric | N_IF (v3combos) | N_GR (v4net) |
|---|---|---|
| Peers stepping | 3 ± 0 (n=20) | 2 ± 0 (n=20) |
| spawn_peer calls | 0.2 ± 0.41 (n=20; 4 links: 7, 11, 16, 20) | 0 (n=20) |
| read_blackboard | 6.85 ± 5.15 (n=20) | 5.25 ± 2.38 (n=20) |
| write_blackboard | 5.95 ± 6.07 (n=20) | 0.65 ± 0.875 (n=20) |
| read_todos | 8.15 ± 4.76 (n=20) | 21.3 ± 11.1 (n=20) |
| claim_todo calls | 19.2 ± 5.43 (n=20) | 42.6 ± 18.0 (n=20) |
| … granted | 9.8 ± 2.31 (n=20) | 30.4 ± 11.0 (n=20) |
| … refused | 9.45 ± 4.38 (n=20) | 12.2 ± 9.27 (n=20) |
| Claim violations (tool used on another peer's TODO, refused) | 5.25 ± 4.99 (n=20; 19 links) | 0 ± 0 (n=18) |
| claim_conflicts (recorded) | 0 (n=20) — defaulted | 0 (n=18) — defaulted |
| wait_for_board | 26.1 ± 18.0 (n=20) | 6.5 ± 6.57 (n=20) |
| TODOs marked done | 9 ± 3.32 (n=20) | 16.6 ± 7.69 (n=20) |
| TODOs marked failed | 5.45 ± 5.01 (n=20; 19 links) | 3.9 ± 6.7 (n=20; 13 links) |
| release_todo | 0 (n=20) | 0 (n=20) |
| request_complexity | 0 (n=20) | 9.9 ± 9.24 (n=20) |
| TODO revisit rate (`od.todo_revisit_rate`) | 0.327 ± 0.162 (n=20) | 0.774 ± 0.102 (n=20) |
| Duplicate work rate | 0.179 ± 0.153 (n=20) | 0.215 ± 0.14 (n=18) |
| Blackboard utilisation (recorded) | 0.575 ± 0.211 (n=20; max 1.222) | 0.529 ± 0.0171 (n=18) |
| Convergence score | 0.219 ± 0.0735 (n=20; max 0.357) | 0.272 ± 0.0643 (n=18; max 0.429) |

- Board tool counts come from the step logs (they are not in the knowledge base); claims granted/refused are parsed
  from the `claim_todo` replies.
- **N_IF coordinates by waiting, N_GR by re-claiming.** N_IF peers wait on the board about 4× more and are refused
  claims about as often as they are granted; N_GR's two peers claim more than twice as often, and three quarters
  of their granted claims re-take a TODO already claimed earlier in the same link (the router reopens TODOs: 1.3
  board reopenings per link).
- Claim violations exist only in N_IF (v3combos code); on the fixed v4net code N_GR has none. This is a code-version
  difference as much as a handler difference.
- Convergence score (mean pairwise TF-IDF similarity of peer messages) stays below the 0.6 joint-myopia threshold
  on every networked link (max 0.357 N_IF, 0.429 N_GR), so no link classifies as joint myopia.
- Duplicate work rate = share of peer messages with similarity > 0.8 to some other peer message.
- Plots: `4_coordination/board_reads_*`, `board_writes_*`, `todo_reads_*`, `claims_granted_*`, `claims_refused_*`,
  `board_waits_*`, `todos_done_*`, `todos_failed_*`, `peers_spawned_*`; `7_org_design/claim_routing_accuracy_*`
  (= 1 − TODO revisit rate), `duplicate_work_*`, `blackboard_utilization_*`, `convergence_score_*`.

## 7. Organisational-design (od.*) metrics per combination

Definitions in `MAS-Aviary-analysis/src/logging/org_theory_metrics.py` (unchanged since B171, 2026-10-10, before
every run). Each is computed only for the structure or handler it belongs to; blank = not defined there.

| Metric (unit) | Defined for | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR |
|---|---|---|---|---|---|---|---|---|---|
| Completion rate, workers (–) | SP | 0.85 ± 0.032 (20) | | | 0.957 ± 0.105 (20) | | | | |
| Propagation time (s) | S | 1568 ± 513 (20) | 1954 ± 494 (20) | 5585 ± 1867 (20) | | | | | |
| Stage independence (–) | S (needs tool calls on messages) | 0.726 ± 0.049 (20) | 0.726 ± 0.052 (20) | gap (20/20) | | | | | |
| Ambidexterity (–) | IF | | 0.00245 ± 0.0046 (8) | | | 0.0228 ± 0.0281 (16) | | 0.0365 ± 0.0322 (20) | |
| Escalation of commitment (turns) | IF | | 0.1 ± 0.31 (20) | | | 0.278 ± 0.752 (18) | | 0.5 ± 0.827 (20) | |
| Misroute rate (–) | GR | | | 0.765 ± 0.063 (20) | | | 0.615 ± 0.156 (20) | | 0.962 ± 0.015 (18) — do not use |
| Orchestrator overhead ratio (–) | O | | | | 0.5 ± 0 (20) | 0.0988 ± 0.019 (18) | 0.0747 ± 0.041 (20) | | |
| Cost per agent (s) | O | | | | 36.8 ± 5.77 (20) | 56.1 ± 33.8 (18) | 74.2 ± 53.8 (20) | | |
| Information ratio (–) | O | | | | 0.0225 ± 0.0066 (20) | 0.0288 ± 0.052 (18) | 1 ± 0 (20) — artefact | | |
| Blackboard utilisation (–) | N | | | | | | | 0.575 ± 0.211 (20) | 0.529 ± 0.017 (18) |
| Duplicate work rate (–) | N | | | | | | | 0.179 ± 0.153 (20) | 0.215 ± 0.14 (18) |
| Convergence score (–) | N | | | | | | | 0.219 ± 0.073 (20) | 0.272 ± 0.064 (18) |
| TODO revisit rate (–, derived) | N | | | | | | | 0.327 ± 0.162 (20) | 0.774 ± 0.102 (20) |

(n in brackets.) Propagation time = last minus first message timestamp, i.e. roughly the sequential link's wall
time; it is not a per-stage propagation delay. Recorded `routing_accuracy` is 0 by construction and is not shown;
routing accuracy for S_GR/O_GR is 1 − misroute rate (Section 4).

## 8. Structure and handler comparison (pooled)

| Pool | Agent steps | total_turns | Agents stepping | Runner attempts |
|---|---|---|---|---|
| Sequential (3 combos) | 102 ± 62.9 (n=60) | 11.2 ± 6.51 (n=60) | 6.43 ± 1.25 (n=60) | 1.05 ± 0.22 (n=60) |
| Orchestrated (3 combos) | 128 ± 115 (n=60) | 15.5 ± 5.28 (n=58) | 7.7 ± 0.743 (n=60) | 1.22 ± 0.555 (n=60) |
| Networked (2 combos) | 262 ± 134 (n=40) | 20.8 ± 10.5 (n=38) | 2.5 ± 0.506 (n=40) | 1.15 ± 0.427 (n=40) |
| Staged pipeline (2 combos) | 68.3 ± 11.9 (n=40) | 10.5 ± 3.55 (n=40) | 7.48 ± 0.506 (n=40) | 1.13 ± 0.335 (n=40) |
| Iterative feedback (3 combos) | 136 ± 118 (n=60) | 10.9 ± 4.39 (n=58) | 5.92 ± 2.2 (n=60) | 1.28 ± 0.613 (n=60) |
| Graph-routed (3 combos) | 224 ± 127 (n=60) | 22.6 ± 7.91 (n=58) | 4.9 ± 2.45 (n=60) | 1 ± 0 (n=60) |

Honest reading: there are 8 cells, one 20-link chain each, and links within a chain are not independent (each
continues from the previous link's design). The effective sample per structure/handler is 2–3 chains, so these
are descriptive contrasts, not tests. The handler ordering (staged < iterative < graph-routed in steps and turns)
holds inside every structure when medians are used (S: 61.5 < 64.5 < 159; O: 78.5 < 93 < 122; N: IF 172.5 <
GR 393.5 agent steps per link, n=20 each); by mean, O_IF (175) exceeds O_GR (134) only because of O_IF link 19. The structure ordering is confounded with the
missing N_SP cell and with code version (N_GR is the only v4net chain). Graph-routed links never needed a second
runner attempt (n=60); iterative feedback needed the most (1.28 per link).

## 9. Trends over the 20 links

72 Spearman tests (metric vs link number, per combination; table `trends.csv`, with link 1–10 vs 11–20 means).
Only O_SP agent steps reaches p < 0.05 (rho = 0.491, p = 0.028; 74.4 → 77.2 steps, n=10 each). The largest
other movements are below significance: N_GR duplicate work rate rho = 0.448, p = 0.062 (0.18 → 0.243, n=8 / 10);
S_IF retries rho = 0.397, p = 0.083 (0.2 → 0.9 per link, n=10 each); N_GR agent steps rho = 0.389, p = 0.090
(304 → 415, n=10 each). Conclusion: no systematic learning or drift in coordination behaviour along the chain.
Line plots for every metric: `4_coordination/*_line*.png`, `7_org_design/*_line*.png`.

## 10. Coordination activity and outcome (descriptive only)

Flew: S_SP 20/20, S_IF 20/20, S_GR 16/20, O_SP 20/20, O_IF 19/20, O_GR 20/20, N_IF 7/20, N_GR 18/20.

- Within combinations where both groups exist:
  - S_GR: flown links had more agent steps (190, n=16 vs 130, n=4), longer routes (20.6 vs 13.25) and more agents
    stepping (5.81 vs 3.25). Among flown links, agent steps vs fuel rho = −0.581 (p = 0.018), route length rho =
    −0.520 (p = 0.039), agents stepping rho = −0.622 (p = 0.010), n=16. These four are one effect (a longer route
    involves more roles and steps), and fuel here has no convergence check.
  - N_IF: not-flown links had more agent steps (179, n=13 vs 139, n=7) and more TODOs marked failed (6.46 vs 3.57);
    claims granted vs fuel rho = −0.692, p = 0.085 (n=7 flown).
  - N_GR: the 2 non-flying links (5, 11) marked 20 TODOs failed on average vs 2.11 (n=18) for flown links.
- Across the 8 combinations (combination means, n=8): mean agent steps vs share of links flown rho = −0.761
  (p = 0.028); mean turns vs mean fuel of flown links rho = 0.762 (p = 0.028). With 8 points and 10 tests in this
  family, treat these as hypotheses, not results; N_IF (7/20 flew, third-highest mean steps) weighs heavily in the
  first one.
- Of 37 within-combination fuel correlations, 4 have p < 0.05, all S_GR (above).

## Caveats

- **Code versions differ** (brief): S_SP, S_IF, S_GR, O_SP, O_IF = v2full (2026-10-14 code, before the
  record_recommendation-loop, B214 and B187 fixes); O_GR, N_IF = v3combos; N_GR = v4net (fixed code). N_IF vs N_GR
  differences (claim violations, waits) are partly code differences. A fixed-code N_IF re-run (v4net_iter) is still
  running and is not used here.
- **Gaps**: O_IF links 8, 19 and N_GR links 6, 8 have no result.json, so `total_turns` and every result.json-derived
  metric (od.*, orchestrator turns, agents_spawned, blackboard totals) is a gap there; step-log counts exist.
- **Contamination** (B187/B214, failed and did-not-fly links) is kept, per Jessica's decision; process metrics
  include every link with data. See `00_README.md` for the per-link list.
- **Misroute / routing accuracy**: recorded `routing_accuracy` is 0 by construction (counts states named
  output_reviewer/COMPLETE, which this graph has none of); routing accuracy = 1 − misroute rate. For N_GR the
  recorded misroute counts peers (agent_1/agent_2), not roles (0.962 ± 0.015, n=18) and is not used; the labelled
  networked analogue is the TODO revisit rate.
- **Metrics that are constant, defaulted or estimated by construction:**
  - `claim_conflicts` = 0 in all 38 networked links with a result: no message carries `metadata.claim_conflicts`, so
    the metric code defaults it to 0 (warning recorded in 38/38). It counts conflicts on the old blackboard
    `entry_type="claim"` path, which the TODO board (`claim_todo`) does not use. Use claims refused / claim
    violations instead.
  - `blackboard_writes_total` is a "Tier 2 fallback" estimate on all 38 networked links (explicit writes + calls to
    a `mark_task_done` tool that does not exist in these runs + one per turn + 1); `blackboard_reads_total` falls
    back to the turn count on 2 N_IF links. `blackboard_utilization` = estimated writes ÷ (peers × turns), so it is
    mostly a function of the number of peers, and it exceeds 1 on at least one N_IF link (max 1.222).
  - `od.information_ratio` = 1.0 on all 20 O_GR links because all 360 O_GR worker messages have `token_count`
    = null; in O_SP and O_IF the orchestrator message's `token_count` is the constant 4 on most links (20/20 O_SP,
    13/18 O_IF), so the ratio is not a measure of information asymmetry in any orchestrated combination.
  - `od.orchestrator_overhead_ratio` = 0.5 on all O_SP links (one orchestrator message per stage).
  - `od.stage_independence_score` is a gap on all S_GR links (no tool calls on the graph-routed messages).
  - `mean_attempts_to_success` is messages per agent name, not attempts to success (Section 3).
- **`early_abort` (staged pipeline) looks like a false positive**: 41 flags (S_SP 20, O_SP 21), and in all 41 the
  knowledge base has a `set_aircraft_parameters` by that agent returning `valid: true`, and the pipeline still ran
  the later stages (2 worker stages after every mission_architect flag). The handler's `_validation_exhausted`
  parses `tool_calls[].output` as JSON; a plausible but unverified cause is that the recorded output is not
  parseable JSON. The flag did not stop any link; it should not be read as "aborted".
- **record_recommendation loop** in v2full inflates agent steps for the 5 v2full combinations (up to 77
  recommendations per link); step counts are not fully comparable across code versions.
- Fuel in Section 10 is the last successful mission with no convergence check (see `01_physics_outcomes.md`).

## How to reproduce

On aipexws8: `cd ~/Jessica/avion_analysis/code && source ~/Jessica/Avion/.venv/bin/activate && python -m
analysis.link20.reports.scripts.coordination --links ../out_20261028/links.csv --out
../reports_20261028/coordination` (reads links.csv plus result.json / knowledge_base.jsonl for the checks; writes
22 CSVs and prints every table).
