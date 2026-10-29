# Methods answers: 20-iteration DLR-F25 MAS study (8 combinations)

Answers to the Methods questionnaire (questions 1-7), taken from the code each run actually used, the run configs
and logs, and the recorded run files. Compiled 2026-10-29. Every claim cites its source as `path:line @ commit` or a
run-file path. Paths without a repo prefix are in `MAS-Aviary/`.

Combinations: S / O / N = sequential / orchestrated / networked; SP / IF / GR = staged pipeline / iterative
feedback / graph-routed. S_SP, S_IF, S_GR, O_SP, O_IF, O_GR, N_IF, N_GR (no networked staged pipeline).

## Code versions and machines

| Dataset | Combinations | MAS-Aviary commit | Machine | Launched | Evidence |
|---|---|---|---|---|---|
| v2full | S_SP, S_IF, S_GR, O_SP, O_IF | `117b55e` | aipexws8 | 2026-10-14 08:39 | W&B metadata; ws8 reflog (checkout 08:38) |
| v3combos | O_GR, N_IF | `a1dafcc` | aipexws5 | 2026-10-16 14:20 | ws5 reflog (checkout 14:19) |
| v4net | N_GR | `7b0a8bc` | aipexws8 | 2026-10-21 13:38 | W&B metadata; ws8 reflog (checkout 13:38) |

- Ancestry: `117b55e` -> `a1dafcc` (21 commits) -> `7b0a8bc` (4 commits). All three ran with
  `--config config/mdo_f25_run_qwen32b_2gpu.yaml --repeats 20 --timeout 480`.
- The prompt files, the runner, every config yaml, the LLM code and the orchestrator tools are byte-identical
  across the three commits. The differences are in graph-routed wiring (O_GR, N_GR), the N_IF handler fix (B207,
  from `a1dafcc`) and the data-plane / engine guards (`7b0a8bc`). The sections below say where this matters.
- MCP servers: identical commits on both machines for all three datasets: tigl-mcp `0a3bfdd`, su2-mcp
  `b9e0be8`, mass-mcp `1f25297`, pycycle-mcp `cc1a88d`, aviary-mcp `27c7b81`.

## Points a Methods section could easily get wrong (details in the sections)

1. **Sampling settings:** the yaml's temperature 0.3 is never applied. Generation used Qwen3-32B's own
   `generation_config.json` (sampling on, temperature 0.6, top-p 0.95, top-k 20). There is no LLM seed;
   `--base-seed 42` only labels the chain anchor. (4f)
2. **Thinking:** OFF everywhere (environment variable `MAS_AVIARY_ENABLE_THINKING=0`), EXCEPT orchestrated
   workers. The orchestrated strategy switches thinking ON for every worker and OFF for the orchestrator, in all
   three commits (B216). (4f)
3. **Task prompt:** the runner's task string never reaches any agent in verbatim mode. The effective task is the
   TOP-LEVEL REQUIREMENTS block in each system prompt, word-identical in all 8 combinations. (4a)
4. **Peers:** N_GR effectively ran with 2 peers (agent_3 never stepped). N_IF's spawned peers never took a step.
   (1)
5. **Coupling:** agent-set engine-cycle values (BPR, pressure ratios, T4) change only TSFC, which never reaches
   aviary's fuel burn (B3). Lift is set by weight (SU2 trimmed to CL = W/qS), not solved for. The flown wing mass
   is aviary's FLOPS wing times a mass-mcp scaler, not mass-mcp's wing itself. (2)
6. **Clamps and bounds:** no tool rejects an out-of-range design variable. The only hard limits are framework
   clamps (drag factor <= 2.0, design thrust <= 25,000 lbf) and a geometry filter. (2, 4b)
7. **Baseline:** the 18,197.0 kg baseline is an infeasible SLSQP end point, not just an unconverged one. It
   ends with mass and distance defects unclosed, and it has none of the SU2 / mass / cycle values the study's
   missions carry. Its "44 iterations" are OpenMDAO model evaluations, not SLSQP iterations. (6)
8. **Recommendation scoring:** the two reports differ only on 17 items that restate the current value.
   Recommended definition: 57 of 92 scored items followed (62.0 %). (7)

## Contents

| Question | Section |
|---|---|
| 1. Roles | Part A, "1. Roles" |
| 2. Coupling between tools | Part B, "2. Coupling between tools" |
| 3. mass-mcp | Part B, "3. mass-mcp" |
| 4a. Task prompt | Part A, "4a. Task prompt" |
| 4b. Design-variable bounds | Part B, "4b. Design-variable bounds" |
| 4c-4h. Staged pipeline, graph-routed, iterative feedback, model settings, timeout and retry, hardware | Part C |
| 5. Carry-over between iterations | Part A, "5. Carry-over between iterations" |
| 6. Baseline | Part D, "6. Baseline" |
| 7. Recommendation scoring | Part D, "7. Recommendation scoring" |


---

# Part A: roles, task prompt, carry-over (questions 1, 4a, 5)

**Conventions.** `RUNNER` = `scripts/stat_batch_runner.py`. `PA` = `src/coordination/prompt_assembly.py`. Repo: `/home/aipexws3/Jessica/Avion/MAS-Aviary`. ws8 = aipexws8 `~/Jessica/Avion/MAS-Aviary/logs/stat_results/`. v3 = aipexws8 `~/Jessica/avion_analysis/data/v3combos_from_ws3/stat_results/` (an rsync copy of ws3 `/data/avion_runs/v3combos`).

**Which code each run used**

| Batch | Combos | Commit | Evidence |
|---|---|---|---|
| v2full | S_SP, S_IF, S_GR, O_SP, O_IF | 117b55e | W&B metadata `~/Jessica/.cache/wandb_runs/wandb/run-20261014_083913-199x7j47/files/wandb-metadata.json` (ws8): `git.commit 117b55e5…`, args `--config config/mdo_f25_run_qwen32b_2gpu.yaml --repeats 20 --timeout 480` |
| v3combos | O_GR, N_IF (its N_GR is not used) | a1dafcc | aipexws5 `git reflog` (checked by the lead session): HEAD moved to `a1dafcc` at 2026-10-16 14:19:05, one minute before the run log `v3combos_qwen_nothink_20261016_142046.log`, and did not move again during the run. The run data also fits: O_GR's orchestrator calls `set_complexity` 28 times, and that tool first appears after 117b55e (`src/tools/recommendation_tools.py:257 @ a1dafcc`). |
| v4net | N_GR | 7b0a8bc | W&B metadata for both launches (`wandb/run-20261021_133824-fad9u3zp`, `.cache/wandb_runs/wandb/run-20261023_165150-ctnjqc9m`): `git.commit 7b0a8bcc…`, same config and arguments |

Ancestry is 117b55e → a1dafcc (21 commits) → 7b0a8bc (4 commits). All three commits have byte-identical copies of: `RUNNER`; `config/mdo_f25_sequential_agents.yaml`; `orchestrated.md`; `networked.md`; `config/aviary_mdo_f25_networked.yaml`; `config/orchestrated.yaml`; `config/mdo_f25_orchestrated_agents.yaml`; `config/mdo_f25_run_qwen32b_2gpu.yaml`; `src/tools/orchestrator_tools.py`; and `src/coordination/strategies/sequential.py`. I checked this with `git diff --stat` (no output). The only prompt-text change is `config/best_practices.md`: +8 lines at a1dafcc and +11 at 7b0a8bc. That file is returned only by the `read_best_practices` tool. The code differences that matter are listed in each section below.

Each structure gets its agent and coordination YAML from `src/runners/batch_runner.py:54-58 @117b55e` (`_MDO_F25_STRATEGY_CONFIGS`):
- sequential: `mdo_f25_sequential_agents.yaml` + `aviary_mdo_f25_sequential.yaml`
- orchestrated: `mdo_f25_orchestrated_agents.yaml` + `orchestrated.yaml`
- networked: `mdo_f25_networked_agents.yaml` + `aviary_mdo_f25_networked.yaml`

All three use **verbatim prompt assembly** (`prompt_assembly: verbatim`):
- `config/mdo_f25_sequential_agents.yaml:10`
- `config/mdo_f25_orchestrated_agents.yaml:38-39` (`prompt_source: orchestrated.md`)
- `config/mdo_f25_networked_agents.yaml:9-10` (`prompt_source: networked.md`)

---

## 1. Roles

### 1.1 The seven discipline roles (the same set in all three structures)

The role texts appear in three places: the sequential YAML stages (`config/mdo_f25_sequential_agents.yaml:81-434`), the orchestrated `ROLE TEMPLATES` (`orchestrated.md:89-380`, repeated in each section), and the networked `THE SEVEN ROLES` (`networked.md:100-384`, repeated). The "Tools" column is the `allowed_tools` list for sequential and `TOOLS FOR THIS ROLE` for the other two. Design-value ownership comes from the "Set in (tool: input)" column of the GLOBAL DESIGN SPACE table in every system prompt (`config/mdo_f25_sequential_agents.yaml:29-71`, `orchestrated.md:28-70`).

| Role (agent name) | One-line responsibility | Discipline tools (exact names) | Design values it owns |
|---|---|---|---|
| `geometry_engineer` | Load, morph and export the CPACS geometry (TiGL), and generate the CFD volume mesh | open_cpacs, close_cpacs, export_cpacs, set_high_level_parameters, get_high_level_parameters, get_wing_summary, get_fuselage_summary, get_configuration_summary, list_geometric_components, export_component_mesh, generate_volume_mesh (`…sequential_agents.yaml:118-129`) | Wing1 area / aspect_ratio / sweep; Wing2H and Wing3V area / AR / sweep; Fuselage1 length / diameter, all set via `set_high_level_parameters`. Span, taper, quarter-chord sweep and fuselage height/width are **derived** from the geometry. |
| `aerodynamics_analyst` | SU2 Euler solve on the mesh, giving CL, CD, L/D and convergence | create_su2_session, close_su2_session, set_mesh, configure_from_cpacs, update_config_entries, get_valid_config_options, run_su2_solver, read_history_csv, sample_surface_solution, list_result_files, get_su2_status (`:171-187`) | None. AoA is trimmed by the framework to target CL; CL and the drag factor are framework-injected into Aviary. |
| `structures_analyst` | mass-mcp OEM, wing mass and component breakdown | validate_cpacs_inputs, estimate_mass, get_cpacs_mass_breakdown (`:226-229`) | None. Mass method and material are pinned by the `<<WING_MASS_METHOD>>`/`<<MATERIAL>>` placeholders, which are filled from `config/mdo_f25_canonical_baseline.yaml:76,78` (flops / aluminum). The wing mass scaler is framework-derived. |
| `propulsion_analyst` ("PROPULSION AGENT") | pyCycle turbofan sizing, giving SFC and thrust | create_cycle_model, close_cycle_model, get_design_inputs, list_variables, set_inputs, run_cycle, get_outputs, get_cycle_summary (`:272-280`) | `splitter.BPR`, `T4_MAX`, `fan.PR`, `lpc.PR`, `hpc.PR` (component efficiencies are "Set (fixed)"; `Fn_DES` and the engine scale factor are framework-derived) |
| `mission_architect` | Check the framework-applied values in the existing Aviary session, set mission values, validate | get_design_space, set_aircraft_parameters, configure_mission, **plus `create_session` in sequential only** (`:315-319`; absent from `orchestrated.md:255` and `networked.md:261`). The runner refuses every agent `create_session` (`RUNNER:588-592`). | "only the mission values you choose" via `set_aircraft_parameters` (`:295,308`). Range, pax, Mach and altitude are fixed "Condition" rows. |
| `simulation_executor` | Run the Aviary SLSQP mission and report results without judging them | run_simulation, get_results, get_trajectory (`:360-363`) | None |
| `mdo_integrator` | Synthesize all disciplines, compute the optimality gap, issue VERDICT COMPLETE/CONTINUE/RETRY, and record ONE recommendation to ONE role | check_constraints, get_results (`:431-433`) + `record_recommendation` | None. It owns the verdict and recommendation (`:383-425`). |

**Tools the framework adds to every agent in all structures** (`PA:53-72 @117b55e`, `with_reference_tools`, verbatim mode only): `read_design_json`, `read_best_practices`, `get_design_state`, `record_recommendation`, `respond_to_recommendation`. `record_recommendation` is refused for any agent that does not hold the `mdo_integrator` role (`src/tools/recommendation_tools.py:171-174 @7b0a8bc`).

**Further additions by combination:**
- Graph-routed combos: every agent also gets `request_complexity`. Sources: `src/coordination/conditional_graph_handler.py:58-62 @117b55e` (S_GR); `:191-192 @a1dafcc` (O_GR); and `networked.py:813-815 @7b0a8bc` (N_GR).
- Orchestrated workers also get `message_orchestrator` (`src/tools/orchestrator_tools.py:375`).
- If an orchestrated worker's name or persona matches a discipline, any missing "cannot-work-without" tools are added (`_complete_toolset`, `orchestrator_tools.py:461-517`, e.g. geometry → open_cpacs, generate_volume_mesh, set_high_level_parameters).

### 1.2 Sequential (S_SP, S_IF, S_GR; all at 117b55e)

- **Agents:** the 7 roles above, each a fixed stage agent. Each agent's system message is the shared `base_instructions` (`…sequential_agents.yaml:11-71`; `sequential.py:350-358`). Its role text is sent in the user message (see §5).
- **Step limits:** `stage_max_steps: 12` (`config/aviary_mdo_f25_sequential.yaml:7`).
- **S_SP and S_IF stage order (fixed):** geometry_engineer → aerodynamics_analyst → structures_analyst → propulsion_analyst → mission_architect → simulation_executor → mdo_integrator. This is the YAML template order (`…sequential_agents.yaml:81-369`, `templates.mdo_f25.stages`). S_SP runs each stage once (`batch_runner.py:209-221`). S_IF uses the same order with per-stage retry-on-warnings (the iterative_feedback handler; `config/aviary_mdo_f25_sequential.yaml` `execution_handler: "iterative_feedback"`).
- **S_GR: no fixed order.** The conditional router (`batch_runner.py:271-289`, `handler_config {"predefined_graph":"mdo_f25","router":"conditional"}`) starts every link at SIMPLE, entering at mission_architect → simulation_executor → mdo_integrator. The level rises to MODERATE (entering at aero) or COMPLEX (entering at geometry) on a rule or a `request_complexity` call. Constants are in `graph_router.py:22-29`: `ENTRY_OF_LEVEL`, and role-run budgets `BUDGET = {simple 8, moderate 15, complex 25}`. Before each link, `graph_preinit.prepare` opens the sessions and seeds the discipline results (`conditional_graph_handler.py:63-66`). Observed order in v2full S_GR link 1 (ws8 `repeat_000/…sequential_graph_routed/step_log_attempt1.jsonl`): mission_architect, simulation_executor, mdo_integrator, geometry_engineer, aerodynamics_analyst, structures_analyst, propulsion_analyst, mission_architect, …

### 1.3 Orchestrated (O_SP, O_IF at 117b55e; O_GR at a1dafcc)

- **Orchestrator:** a single agent named `orchestrator`, `max_steps: 20` (`config/mdo_f25_orchestrated_agents.yaml:41-47`). Its system message is one section of `orchestrated.md`, sent verbatim. The section is chosen in `orchestrated.py:236-241`: `orchestrated_staged_pipeline` if `lifecycle_mode == per_stage`, `orchestrated_graph_routed` if graph roles exist, otherwise `orchestrated_iterative_feedback`.
- **Orchestrator tools** (`orchestrated.py:300-316`; `orchestrated.md:82-87, 830-836`):
  - all three combos: `list_available_tools`, `create_agent`, `assign_task`, `give_tools`, `reply_to_agent` (+ a gated `final_answer`)
  - O_GR also: `list_graph_roles`, and `set_complexity`, which the conditional handler adds at a1dafcc (`conditional_graph_handler.py:186-187 @a1dafcc`; `batch_runner.py:313-314 @a1dafcc`, `"decider": "orchestrator"`)
- **Limits:**
  - `max_agents: 8` (`config/orchestrated.yaml:29`), enforced by `create_agent` with "Maximum agent limit (8) reached" (`orchestrator_tools.py:346-347`)
  - `worker_max_steps: 20` (`:35`)
  - `lifecycle_mode` per combo: O_SP `per_stage`, O_IF `setup_only`, O_GR `setup_only` (`batch_runner.py:224-257, 291-315`)
- **Fixed list or free choice? The prompt fixes the roles; the run data mostly follows it.** Every section says "Create the agents with create_agent, **one per role in the ROLE TEMPLATES below, named exactly as the template names it**" (`orchestrated.md:15, 389, 763`) and gives 7 templates headed `AGENT NAME: <name>` with `TOOLS FOR THIS ROLE` (`orchestrated.md:91-325`). The persona is written by the orchestrator. The framework then appends that template's `DELIVERABLE…SESSION STATE FILE` block to the persona of any agent created under a template name (`PA:542-555` `role_contracts`; `orchestrator_tools.py:369-381`).
  - **O_SP:** the orchestrator is told the fixed 7-stage order, and each message names the current stage, e.g. "Stage 1 of 7: geometry_engineer" (`orchestrated.md:72-75`).
  - **O_IF:** "You decide which agents to create, what each one does, and in what order they work" (`orchestrated.md:446-449`).
  - **O_GR:** "Call list_graph_roles and create one agent for each role it returns" (`orchestrated.md:820-823`). `list_graph_roles` returns the 7 names from `config/mdo_f25_graph.yaml` `agent:` fields (`batch_runner.py` graph-roles injection; `mdo_f25_graph.yaml:27-495 @a1dafcc`).
- **What `create_agent` actually did** (`jq` over every `step_log_attempt*.jsonl`):

| Combo | Links where the created names were exactly the 7 template names | Exceptions |
|---|---|---|
| O_SP (ws8 v2full) | 19/20 | `repeat_019`: only 6 names, no `mdo_integrator` |
| O_IF (ws8 v2full) | 14/20 | 6 links (`repeat_003, 006, 007, 012, 015, 019`) also created off-template agents, all in links that needed extra attempts or orchestrator re-calls. Names included `geometry_setup_agent`, `mass_estimator`, `mission_configurator`, `propulsion_analysis_agent`, `evaluation`, `simulation_agent`. Several echo the `required_tool_phases` keys in `config/mdo_f25_orchestrated_agents.yaml:4-28`. |
| O_GR (v3) | 18/20 | `repeat_005`: `geometry_setup` was created as an 8th agent, then `aerodynamic_analysis, mass_estimation, mission_setup, simulation_phase, evaluation_phase` were refused by the 8-agent cap. `repeat_014`: `geometry_setup`, `aerodynamic_analysis` added. |

  In short, the prompt prescribes the roles, and the 7 template roles were always created except in O_SP link 20, which had no integrator. The orchestrator's freedom showed only as extra, re-named agents on retries.

### 1.4 Networked (N_IF at a1dafcc; N_GR at 7b0a8bc)

- **Peers are generic.** They are named `agent_1…agent_N` (`networked.py:234-236 @7b0a8bc`), and all share the same system message: the `networked_iterative_feedback` or `networked_graph_routed` section of `networked.md` (`networked.py:159-162`). That one prompt contains all seven roles (`networked.md:100-384` / `487-770`) and says: "You have every tool, but while you hold a role, focus ONLY on that role's discipline" (`networked.md:16`).
- **Role-to-peer mapping is dynamic: whoever claims a TODO holds that role.**
  - N_IF TODOs: `geometry, aero, mass, propulsion, mission, simulation, evaluation` (`networked.md:74-82`; seeded from `config/aviary_mdo_f25_networked.yaml:67-81`). The board does not enforce an order.
  - N_GR TODOs: `GEOMETRY_SETUP, AERO_ANALYSIS, MASS_ESTIMATION, PROPULSION_SIZING, MISSION_CONFIG, SIMULATION_RUN, RESULTS_REVIEW` (`networked.md:452-460`; `board_graph.py:27-29 @7b0a8bc`).
  - The tools map a held TODO to a role (`recommendation_tools.py:68-76 @7b0a8bc`, `_TODO_ROLE`). For example, only the peer holding `evaluation` / `RESULTS_REVIEW` may call `record_recommendation`.
  - For N_GR, the runner also registers the role names as aliases of the peers (`batch_runner.py:1394-1404`), so `trace.json` lists `geometry_engineer` etc. beside `agent_1…3`.
- **Peer tools:**
  - **All** loaded tools: 63, i.e. every MCP tool plus the framework tools `get_design_state, read_design_knowledge, read_procedure, read_best_practices, read_design_json` (`coordinator.py:205-214 @7b0a8bc`; `src/tools/tool_loader.py` extras; list printed in each run log)
  - plus `read_blackboard, write_blackboard, spawn_peer, mark_task_done, read_todos, claim_todo, mark_todo_done, mark_todo_failed, wait_for_board` (`networked.py:779-816`)
  - plus `record_recommendation, respond_to_recommendation` (`PA` reference tools)
  - N_GR also: `request_complexity`
  - Peers did call tools outside their role list, e.g. `morph_wing`, `get_session_info` and `get_config_text` in N_GR.
- **Peer counts:** 3 at start (`initial_agents: 3`), at most 7 (`max_agents: 7`; prompt: "spawn_peer(reason) adds a peer… (at most 7 peers)", `networked.md:96, 474`). `agent_max_steps: 15`. Source: `config/aviary_mdo_f25_networked.yaml:10, 17, 22`.
- **Cycles:**
  - N_IF: up to `max_concurrent_runs: 2` parallel cycles (`:54`), each dispatching every peer.
  - N_GR (board router): up to `graph_max_cycles`, default 30 (`networked.py:308`). Each cycle dispatches **one peer per AVAILABLE TODO**, capped at the peer count (`networked.py:1373`).
- **Peers that actually ran:**
  - **N_IF:** agent_1–3 ran in every link. `spawn_peer` succeeded in 5 links (e.g. `repeat_001` spawned agent_4–6), but no spawned peer ever took a step: there are no agent_≥4 steps in any step log or `trace.json`. I did not verify the cause. Spawned peers are added to the dispatch order only at the next cycle (`networked.py:402-407 @a1dafcc`), and there are only 2 cycles.
  - **N_GR:** only agent_1 and agent_2 ever ran. `agent_3` has an empty `system_prompt`/`steps` in `trace.json` and 0 "New run - agent_3" lines in either v4net log. On this board at most two TODOs are AVAILABLE at once (AERO_ANALYSIS ∥ MASS_ESTIMATION), so `min(len(available), 3)` never reaches 3. There were 0 `spawn_peer` calls in 20 links.
- **N_GR board router (7b0a8bc; also present at a1dafcc):** every link starts at SIMPLE with only MISSION_CONFIG → SIMULATION_RUN → RESULTS_REVIEW on the board (`board_graph.py:6-8`). Any peer can call `request_complexity` (MODERATE adds aero, mass and propulsion; COMPLEX adds geometry). The integrator's recorded recommendation reopens the TODO that produces the named variable (`board_graph.py:9-12`). Observed across v4net: 124 `request_complexity complex`, 74 `moderate`; 155 `claim_todo GEOMETRY_SETUP`.

---

## 4a. Task prompt

**Two things are called "the task", and only one of them reaches the model.**

**(i) The runner's task string is built for every link but is NOT sent to any agent.** The base text, identical for all 8 combos and all 3 commits (`RUNNER:611-617`, unchanged across 117b55e/a1dafcc/7b0a8bc):

> Design a DLR-F25 class aircraft for minimum fuel burn. Use CPACS file at {_D150_FIXTURE}. Target: 2500 nmi range, 239 passengers, Mach 0.78 cruise, 33000 ft altitude. Constraints: fuel_burned_kg <= 15000, gtow_kg <= 90000. Report the optimality gap versus the F25 reference (MTOM 85700 kg, fuel 12100 kg).

- On links after the first, the CPACS path is replaced with the carried file (`base_task_for`, `RUNNER:620-636`).
- `build_task_with_session` (`RUNNER:525-594`) wraps it with a session/parameters preamble and a "=== FEEDBACK FROM THE PREVIOUS DESIGN ITERATION ===" block.
- In verbatim mode this text is discarded: "no runner task text, no knowledge-base block, no 'New task:' prefix" (`PA:7-14`).
- **Verified:** `grep -c "Design a DLR-F25 class aircraft"`, `"A session has already been created"` and `"FEEDBACK FROM THE PREVIOUS DESIGN"` all return 0 in link-1 and link-2 step logs for S_SP, S_IF, S_GR, O_SP, O_IF (ws8 v2full) and O_GR (v3). They also return 0 in every file of N_IF `repeat_000` (v3) and N_GR `repeat_000` (v4net).

**(ii) What the model actually receives as the task is the requirements block of the system message.** It is the same text in all 8 combos:

```
TOP-LEVEL AIRCRAFT REQUIREMENTS & CONSTRAINTS
- Aircraft Type: Single aisle commercial aircraft
- Mission:       239 passengers | 2,500 nmi range | Mach 0.78 at FL330
- Objective:     Minimize fuel burn <= 15,000 kg
- Constraint:    Gross Takeoff Weight (GTOW) <= 90,000 kg
```

That block is followed by "GLOBAL DESIGN SPACE — The following STARTING parameters have been applied as an initial baseline they are valid but NOT optimized:" and a 39-row variable table (Variable | Type | Set in | Used by | Default | Range | Units).

| Where it lives | Structure | Sent as |
|---|---|---|
| `config/mdo_f25_sequential_agents.yaml:22-71` (`stage_defaults.base_instructions`, "SYSTEM PROMPT: MULTI-STAGE MDO PIPELINE AGENT") | S_SP, S_IF, S_GR | system message of all 7 stage agents |
| `orchestrated.md:21-70` (O_SP section), `:395-444` (O_IF), `:769-818` (O_GR) ("SYSTEM PROMPT: MDO ORCHESTRATOR") | O_* | orchestrator system message. Workers instead get the orchestrator-written persona + template contract and **do not see this block** unless the orchestrator copies it into the persona. |
| `networked.md:20-69` (N_IF), `:398-447` (N_GR) ("SYSTEM PROMPT: NETWORKED MDO PEER AGENT") | N_* | system message of every peer |

- **Identical across versions:** none of these three files differs between 117b55e, a1dafcc and 7b0a8bc.
- **Across structures:** the requirements block is word-identical. The design-space table differs only in typography: `m²`/`°R`/`·`/`×` in the sequential YAML versus `m^2`/`degR`/`*` in the `.md` files.
- **What differs between combos** is the structure-specific instruction text around that block:
  - sequential: ROLE & CONTEXT
  - orchestrated: ROLE & CONTEXT + HOW THE WORK RUNS + RECOMMENDATIONS + YOUR TOOLS + ROLE TEMPLATES
  - networked: WORKING WITH THE OTHER PEERS + (N_GR) WORKFLOW ROUTE + THE SEVEN ROLES
- **Machine-specific paths:** the `<<…>>` placeholders and absolute Avion paths are substituted at load (`src/config/canonical.py:41-53, 144-154`). On ws8 the prompts name `/home/aipexws8/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml` (checked in the step logs); on ws5, `/home/aipexws5/…`.
- **No objective/constraint text in user messages:** the per-turn user messages (§5) add data only.

---

## 5. Carry-over between iterations

### 5.1 What the runner carries from link k to link k+1 (all structures, same `RUNNER` in all three commits)

| Item | Source | Where it goes |
|---|---|---|
| **Aviary design variables** (`chain_end_params`: wing area/AR/sweep/taper, fuselage length/height/width, engine scale factor) | `read_end_state_design` reads the end session's `get_results` (`RUNNER:1700-1701, 1724-1725`) | Applied to the new link's pre-created Aviary session by the pre-hook (`setup_session_with_params`, `RUNNER:1443`). Recorded as `carry_over.json → aviary_session_start` (`RUNNER:519`). **Not shown in any prompt.** |
| **Geometry (CPACS)** | the link's last exported morph, copied to `repeat_k/<combo>/geometry_end.xml` (`carry_geometry_forward`, `RUNNER:639-657, 1705-1709`) | `carry_over.json → starting_design.CPACS_FILE`. It replaces the baseline D150 path in every prompt (`PA:316-321` sequential, `PA:393-399` orchestrated and networked). |
| **Design table** (geometry, engine inputs, mission scale factor) | the previous link's `design.json → design_space` (`RUNNER:1746-1748`) | `carry_over.json → starting_design`, rendered as "STARTING DESIGN…" or "DESIGN_STATE (the starting design…)" (`PA:192-209, 446-473`) |
| **Integrator's final text** | `integrator_text()`: the last message from an agent whose name contains "integrator". Otherwise the last message containing `DESIGN_STATE`/`VERDICT`/`RECOMMENDED_CHANGE`/`MDO_REASONING`, else the last message. Capped at 7000 chars (`RUNNER:431-459`). | `carry_over.json → previous_integrator_design_state`, rendered as "DESIGN_STATE from the previous iteration (mdo_integrator):" |
| **Recorded recommendation + verdict** | the last `kind:"recommendation"` signal in the previous link's `design.json → coordination_signals`, i.e. the `record_recommendation` call (`RUNNER:508-512`) | `carry_over.json → recommendation`. Rendered as "RECOMMENDATION … RECOMMENDATION_TO / RECOMMENDED_CHANGE" and arms the response gate (`rt.activate`) for the named role. |
| **Whole previous `design.json`** (aero, structures, propulsion results, sessions, signals) | `repeat_{k-1}/<combo>/design.json` (`RUNNER:502-507`) | `carry_over.json → previous_design`. **Used only by graph-routed combos:** `graph_preinit.prepare` seeds those discipline results into the data plane so SIMPLE can start at the mission (`graph_preinit.py:134-160`). Not printed in prompts. |
| **Knowledge base** | only a `link_start` entry (`knowledge_base.py:168-176 @117b55e`) holding `{summary, end_state}` | The new link's KB file is new. The seed **summary text is replaced by a size marker**: e.g. ws8 `v2full…/repeat_001/mdo_f25_sequential_staged_pipeline/knowledge_base.jsonl` line 1 has `"summary": "<1202 chars>"`. Only `end_state` survives. It is reachable only through `read_design_knowledge`, which networked peers have and sequential stages do not. **No KB text is placed in any verbatim prompt** (`PA:11`). |
| **Not carried** | | MCP sessions: new each link, since `reset_design_state()` runs at `RUNNER:1440-1441`. Board/blackboard and orchestrator-created agents: rebuilt each link. Pinned controls (mission, SLSQP, flops/aluminum, SU2 numerics): re-applied from canonical (`RUNNER:12-15`). The runner's `chain_feedback` quick-metrics text: built at `RUNNER:1750-1759` but only used in the discarded task string (§4a). |

**Verified against a real file.** ws8 `v2full_qwen_nothink_aipexws8/repeat_001/mdo_f25_sequential_staged_pipeline/carry_over.json` has keys `recommendation, previous_design, link, previous_integrator_design_state, starting_design, aviary_session_start, baseline_cpacs`. Its values:
- `recommendation`: `{verdict: CONTINUE, recommendation_to: geometry_engineer, recommended_change: "Wing1 area 122.8 m² -> reduce to 115 m²"}`
- `starting_design.CPACS_FILE`: `…/repeat_000/mdo_f25_sequential_staged_pipeline/geometry_end.xml`
- `previous_integrator_design_state`: the integrator's `record_recommendation` tool-call JSON followed by `TASK_COMPLETE`

**What the "DESIGN_STATE from the previous iteration (mdo_integrator)" slot actually held.** I counted the 19 `carry_over.json` files (links 2–20) per combo. Each cell is the number of links whose text matches:

| Combo | contains VERDICT | is the `record_recommendation` call JSON | contains `GEOMETRY_SET` (a geometry DESIGN_STATE) | a recorded recommendation present |
|---|---|---|---|---|
| S_SP | 17 | 16 | 0 | 19 |
| S_IF | 16 | 14 | 0 | 19 |
| S_GR | 14 | 13 | 0 | 15 |
| O_SP | 14 | 11 | 0 | 19 |
| O_IF | 8 | 6 | 1 | 18 |
| O_GR | 8 | 0 | 1 | 18 |
| N_IF | 7 | 0 | 8 | 18 |
| N_GR | 6 | 0 | 12 | 15 |

Two cases explain most of this:
- **Networked:** peers are named `agent_N`, so the "integrator" name match never fires. The slot falls back to the last message containing "DESIGN_STATE", which is often another peer's output. Example: N_GR link 2's slot holds a `GEOMETRY_SET…` geometry DESIGN_STATE (`v4net…/repeat_001/…/trace.json`). N_IF link 2 holds a peer's `final_answer` "blocked… Evaluation task claimed by agent_2".
- **Sequential and orchestrated:** the slot often holds only the integrator's `record_recommendation` tool call, not a written DESIGN_STATE.

### 5.2 Where it appears in link k+1's prompt

The system message never changes between links, apart from the CPACS path substitution (§4a). **All carry-over goes into user messages.**

**Sequential (S_SP, S_IF, S_GR).** This is the first user message of each stage (`verbatim_stage_input`, `PA:307-344`).

The **first** stage of the link gets, in order:
1. the role text as written
2. "DESIGN_STATE from the previous iteration (mdo_integrator):" + the integrator text, with `TASK_COMPLETE` stripped
3. "STARTING DESIGN for this iteration (the previous iteration's design, from its design.json):"
4. "RECOMMENDATION from the previous iteration (mdo_integrator, recorded with record_recommendation): RECOMMENDATION_TO / RECOMMENDED_CHANGE", followed by "This recommendation is addressed to the role named in RECOMMENDATION_TO, but any agent may act on it… respond_to_recommendation…"
5. "design.json (…)", if one exists

**Later** stages get the role text, then "DESIGN_STATE from the previous agent (<name>):", then the same RECOMMENDATION block plus "Responses so far: RECOMMENDATION_IMPLEMENTED (reported by …)", then design.json. They do not get the integrator text or the STARTING DESIGN.

S_GR appends "GRAPH ROUTING (framework): complexity level …" (`conditional_graph_handler.py:37-45`). Its first role (mission_architect at SIMPLE) also gets design.json, because preinit has already written it.

Sequential does not print the VERDICT. If the recorded verdict is COMPLETE, the recorded recommendation is not shown at all; the code falls back to parsing the text (`PA:250-273`).

Verified in ws8 `…/repeat_001/mdo_f25_sequential_staged_pipeline/step_log_attempt1.jsonl`. Seq 1 (`geometry_engineer`) has messages `[system, user]`, and the user message contains sections 1–4 above (no design.json yet). `aerodynamics_analyst`'s first message has "DESIGN_STATE from the previous agent (geometry_engineer)", the RECOMMENDATION, "Responses so far…" and design.json. The `repeat_001/mdo_f25_sequential_graph_routed` first message (mission_architect) has the integrator text, STARTING DESIGN, RECOMMENDATION, design.json and the GRAPH ROUTING lines.

**Orchestrated (O_SP, O_IF, O_GR).** The orchestrator's first user message is `orchestrator_first_message` (`PA:538-539`, i.e. `starting_design_state`, `PA:446-473`). In order:
1. O_SP only: a stage line ("Stage 1 of 7: geometry_engineer")
2. "DESIGN_STATE from the previous iteration (mdo_integrator):" + integrator text
3. "RECOMMENDATION recorded by the mdo_integrator (record_recommendation): RECOMMENDATION_TO / RECOMMENDED_CHANGE / **VERDICT**"
4. "DESIGN_STATE (the starting design for this iteration, from the previous iteration's design.json):" + "SESSIONS (already open -- reuse them)"
5. design.json

The orchestrator system prompt's RECOMMENDATIONS paragraph tells it to pass the recommendation to the right agent (`orchestrated.md:79-80, 453-454, 827-828`).

Workers (`worker_message`, `PA:580-591`):
- The first worker gets the orchestrator's task + inbox + the same `starting_design_state`, so it sees the integrator text and the recommendation too, + design.json.
- Later workers get the task + "DESIGN_STATE from the agent that worked before you" + one-line summaries of earlier agents + sessions + design.json. They see the recommendation only if the orchestrator wrote it into their task.

Verified: ws8 `…/repeat_001/mdo_f25_orchestrated_iterative_feedback/step_log_attempt1.jsonl` seq 1 user message, and `…/repeat_001/mdo_f25_orchestrated_staged_pipeline` (begins "Stage 1 of 7: geometry_engineer" then the integrator DESIGN_STATE). In v3 `repeat_001/mdo_f25_orchestrated_graph_routed`, the first worker's message is the orchestrator's task followed by the same carry-over blocks.

**Networked (N_IF, N_GR).** Each peer, each cycle, gets `peer_message` (`PA:532-535 @117b55e` = `:541-544 @7b0a8bc`). In order:
1. "YOUR WORK SO FAR (agent_N, from the knowledge base…)" (this link's KB only; absent on a peer's first cycle)
2. the same `starting_design_state` blocks as orchestrated: integrator text, recorded RECOMMENDATION with VERDICT, starting design, sessions
3. "TODO BOARD:" + DESIGN_STATEs posted by finished TODOs. At a1dafcc/7b0a8bc the router's "GRAPH ROUTING (framework)…" notices are also included (`board_block`, diff at `PA:526-538 @a1dafcc`).
4. design.json

Networked step logs do not save prompts. They were verified from `trace.json` (`model_input_messages`):
- v3 `repeat_001/mdo_f25_networked_iterative_feedback/trace.json` agent_1: integrator text, RECOMMENDATION (to aerodynamics_analyst, VERDICT CONTINUE), starting design with CPACS `…/repeat_000/…/geometry_end.xml`, sessions, then TODO BOARD with 7 pending TODOs.
- ws8 `v4net…/repeat_001/mdo_f25_networked_graph_routed/trace.json` agent_1: the same blocks, then a TODO BOARD showing only MISSION_CONFIG [AVAILABLE], SIMULATION_RUN and RESULTS_REVIEW [BLOCKED], then "GRAPH ROUTING (framework): complexity level SIMPLE…", then design.json.
- N_GR additionally seeds the previous link's discipline results via `BoardGraph.start(carry_over)` → `graph_preinit.prepare` (`board_graph.py:104-106`).

### 5.3 Failed-link rule (`RUNNER:1407-1416, 1477-1540, 1818-1876`)

- **How a link fails:** a link is retried up to 3 attempts from the same start. A `TimeoutError` (480 min) is not retried (B50, `RUNNER:1830-1833`). A link that exhausts its attempts or times out is entered in `stat_progress.json → failed`.
- **What restarts from the last completed link:** `chain_params` (the Aviary variables), `chain_cpacs` (geometry), `chain_start_fuel` and the KB seed are updated **only on success** (`RUNNER:1700-1731`). So link k+1 starts its Aviary session and opens its CPACS from the last completed link. A resumed process rebuilds these from `completed` entries only (`RUNNER:1390-1405`).
- **Exception: the prompt carry-over does follow a failed link** (`RUNNER:1867-1875`):
  - `chain_integrator` is replaced by the failed link's integrator text, if it produced one. A timed-out link produces no `result`, so the previous text is kept.
  - `chain_design_space` is replaced by the failed link's `design.json` design_space, if present.
  - `write_carry_over` always reads `recommendation` and `previous_design` from `repeat_{k-1}`, i.e. the failed folder (`RUNNER:502-512`).

**Real case: N_GR links 6 and 8 failed** (`r005`, `r007`: "TimeoutError: Run exceeded 480m timeout", 1 attempt, `v4net…/stat_progress.json`). The link-7 carry-over (`repeat_006/…/carry_over.json`) mixes sources:
- `starting_design.CPACS_FILE` = `repeat_004/…/geometry_end.xml`, from the last completed link
- `aviary_session_start` = r004's `chain_end_params` (Wing area 82.20, AR 21.05)
- `starting_design.geometry.Wing1` = **r005's** design.json (area 89.97, AR 13.00), not r004's (area 120.22, AR 9.96)
- `recommendation` = r005's recorded one (RETRY → geometry_engineer, "Reduce wing area further…")
- `previous_design` = r005's design.json

So after a failure, the geometry file and the Aviary state come from the last completed link, but the design table, recommendation and (for graph-routed) the seeded discipline results shown or used in link k+1 come from the failed link.

---

# Part B: coupling, mass-mcp, design-variable bounds (questions 2, 3, 4b)

**Scope.** These notes cover the code each run used. They are not taken from a later main branch.

**Code versions**
- MAS-Aviary: v2full = `117b55e` (S_SP, S_IF, S_GR, O_SP, O_IF); v3combos = `a1dafcc` (O_GR, N_IF); v4net = `7b0a8bc` (N_GR).
- MCP servers on aipexws8: the checked-out commits were unchanged from 2026-10-14 to 2026-10-28 (per `git reflog`).
  - tigl-mcp `0a3bfdd`, checked out 2026-10-09.
  - su2-mcp `b9e0be8`, checked out 2026-10-10.
  - mass-mcp `1f25297`, checked out 2026-10-14 08:38:30, one second after MAS-Aviary moved to `117b55e`.
  - pycycle-mcp `cc1a88d` and aviary-mcp `27c7b81`, both checked out 2026-10-09.
- tigl-mcp and mass-mcp are installed editable into `~/Jessica/avion_envs/{tigl-env,mass-tigl}`, pointing at `~/Jessica/Avion/<repo>/src`.
- **Undetermined:**
  - (Resolved by the lead session.) aipexws5 ran the SAME server commits for v3combos: `git reflog` there shows tigl `0a3bfdd`, su2 `b9e0be8`, mass `1f25297`, pycycle `cc1a88d`, aviary `27c7b81`, all checked out at 2026-10-16 14:19:05, one minute before the v3combos launch. So all three datasets ran identical MCP server code.
  - When the server processes were restarted. The current processes started 2026-10-29.

**Abbreviations**
- `DP` = `src/tools/data_plane.py`
- `CC` = `src/tools/coupling_contract.py`
- `CPL` = `src/tools/coupling.py`
- `TC` = `src/tools/type_coercion.py`
- Paths without a repository prefix are in MAS-Aviary.
- `CPL` and `config/mdo_f25_canonical_baseline.yaml` are byte-identical across all three MAS-Aviary commits (`git diff 117b55e 7b0a8bc` is empty). `DP`, `CC` and `TC` differ only where noted.

**Run data**
- `RUN_V2` = aipexws8 `~/Jessica/Avion/MAS-Aviary/logs/stat_results/v2full_qwen_nothink_aipexws8/repeat_000..019/<combo>/`
- `RUN_V3` = the v3combos copy above.
- `RUN_V4` = `.../v4net_graph_qwen_nothink_aipexws8/repeat_000..019/mdo_f25_networked_graph_routed/`
- The knowledge-base (KB) `inputs` are the **resolved** arguments, recorded after the framework's injection (`TC:204,349 @117b55e`). So they show what actually reached each server.

---

## 2. Coupling between tools

### 2.1 Data flow (what feeds what)

Most cross-tool values do not pass through the agents. The **data plane** captures them from tool responses (`DP:intercept_response`, capture list `DP:547-569 @117b55e`). It then writes them into a typed registry (`CPL:37-64`) and injects them into later tool calls (`DP:resolve_request`, `DP:2116-2529`).

| # | Producer → output | Transform (framework) | Consumer → input | Source |
|---|---|---|---|---|
| 1 | tigl `open_cpacs`: link 1 opens the D150 fixture; link k opens link k−1's `geometry_end.xml` | path captured | CPACS path for mass/SU2 | `DP:2290-2296`; KB link 1 `open_cpacs` input (`RUN_V2/repeat_001/mdo_f25_sequential_staged_pipeline/knowledge_base.jsonl` seq 2) |
| 2 | tigl `set_high_level_parameters` (Wing1 `area`/`aspect_ratio`/`sweep`; Fuselage1 `length`/`diameter`) → `geometry_morph.after` | full-wing area/AR, measured c/4 sweep and taper → `geometry_wing`; area/2 → `geom.reference_area`, MAC → `geom.mac_length` | aviary wing/fuselage keys (geometry authority); SU2 `ref_area`/`ref_length` | `DP:1019-1044`, `DP:1720-1744`; tigl `src/tigl_mcp/tools/parameters.py:195-252 @0a3bfdd` |
| 3 | tigl morph → `export_cpacs` file. If the morph was never exported, the framework exports it itself | morphed path overrides any path the agent passed; mass writes to a per-run copy | mass `estimate_mass.cpacs_file_path`; SU2 `configure_from_cpacs` / `create_su2_session.cpacs_file_path` | `DP:2197-2234`, `DP:2277-2288`, `DP:1988-2022` |
| 4 | tigl `generate_volume_mesh` → `mesh_base64` (stored as a ref) | canonical mesh `far_field 10, min 0.33, max 12` injected when the caller omits them; `component_uid` dropped so the whole aircraft is meshed | SU2 `create_su2_session.initial_mesh` (+ `mesh_file_name`) | `DP:2318-2351`, `DP:2386-2403`; yaml `:18-25`. Observed: 304/321 successful meshes used exactly (10, 0.33, 12); 2.2–3.7 M cells typical (all KBs) |
| 5 | mass `estimate_mass` → `components.mWing_kg` | sanity bracket 1,000–30,000 kg → `mass.wing_kg` | aviary `Aircraft.Wing.MASS_SCALER` | `DP:576-631`, `DP:2640-2682` |
| 6 | mass `estimate_mass` → `mTOM_sized_kg` if `sizing_converged` is true, else `mTOM_kg` (the CPACS start value) | bracket 20–200 t → `mass.mtom_kg` | SU2 `target_cl`; pycycle `Fn_DES` | `DP:633-650` |
| 7 | MTOM (mass-mcp, else the CPACS `designMasses/mTOM`) × mission × wing area | `target_cl = m·g/(q·S)`, kept only if 0.05–1.2 | SU2 `target_cl` → `FIXED_CL_MODE=YES`, `TARGET_CL` (SU2 trims AoA to it) | `DP:911-963`, `DP:2241-2244`; `CPL:126-152`; su2-mcp `src/su2_mcp/tools/config_tools.py:163-185 @b9e0be8` |
| 8 | geometry + mission | `aspect_ratio`, `cruise_mach`, `cruise_altitude_ft`, `ref_area`, `ref_length` and the canonical SU2 numerics (as a floor beneath caller overrides) | SU2 session/config | `DP:2405-2486`; yaml `:32-72` |
| 9 | SU2 `run_su2_solver` → `final_coefficients` CL/CD and `drag_assessment.cd_reported` (CD floored at the induced drag CL²/(πARe), Raymer eq. 12.48 e). `read_history_csv` is a fallback only, ignored once a solver report exists (B153) | → `aero.cl_cruise`, `aero.cd_cruise` | aviary `Mission.Design.LIFT_COEFFICIENT`, `Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR`; pycycle `Fn_DES` (via L/D) | `DP:1381-1532`; su2-mcp `src/su2_mcp/drag_model.py:31-75 @b9e0be8` |
| 10 | MTOM + SU2 CL/CD | `Fn_DES` (formula in 2.2); mission cruise point into `fc.alt`/`fc.MN` if absent | pycycle `set_inputs.values` | `DP:2523-2527`, `DP:2549-2637` |
| 11 | pycycle `run_cycle`/`get_outputs` → `perf.Fn`, `perf.TSFC` (captured only if converged) | `perf.Fn` → `prop.fn_lbf`; `perf.TSFC` → `prop.sfc_cruise` (**recorded only**) | aviary `Aircraft.Engine.SCALE_FACTOR` | `DP:1535-1579`, `DP:3062-3090` |
| 12 | canonical mission (2500 nmi, 239 pax, M0.78, 33,000 ft) | pinned (an override is replaced and reported) | aviary `configure_mission`; mass `estimate_mass` mission args, `wing_mass_method`, `material`, `design_load_factor` | `DP:1320-1355`, `DP:2260-2275` |
| 13 | aviary `run_simulation` → `fuel_burned_kg`, `gtow_kg`, `wing_mass_kg` | — (the objective) | scoring; next link's start | e.g. `RUN_V2/repeat_001/mdo_f25_sequential_staged_pipeline/result.json` |

**Structural consequences** a Methods section should state:
- **Lift is set by weight, not solved for.**
  - SU2 is trimmed to the cruise CL implied by mass-mcp's MTOM (rows 6–7), and that CL is passed to aviary.
  - In the sequential combinations, SU2 usually runs before mass-mcp (first solve before the first `estimate_mass` in 19/20 S_SP links, 19/20 S_IF, 20/20 O_SP). The trim mass is then the CPACS `mTOM` (`DP:928-935`, B146).
- **MTOM is often the starting value.**
  - When mass-mcp's internal Aviary sizing does not converge, the captured MTOM is the CPACS start value, 78,126 kg.
  - Count of end-state `design.json` files holding exactly 78,126 kg: S_IF 19/20, S_SP 8, O_IF 8, N_IF 6, O_SP 4, S_GR 3, O_GR 1, N_GR 1.
  - In those links neither `target_cl` nor `Fn_DES` responds to the design's mass.
- **pyCycle's thermodynamics do not reach the mission.**
  - The design-point HBTF balance drives `perf.Fn` to equal `Fn_DES` (pycycle-mcp `src/pycycle_mcp/cycles/high_bypass_turbofan.py:109-113 @cc1a88d`). So the only propulsion quantity aviary receives, `SCALE_FACTOR = perf.Fn/5936`, is fixed by MTOM and SU2 L/D through the framework's `Fn_DES`.
  - Agent choices of BPR, fan/LPC/HPC pressure ratio and T4 change only TSFC, which is recorded but not coupled (B3).
  - Verified in the runs: `prop.fn_lbf` equals the last `set_inputs` `Fn_DES` exactly in every link of S_SP, S_IF, S_GR, O_SP, O_GR and N_GR.

### 2.2 What agents set vs what the framework writes

**Agent-set (direct):**
- **tigl morph inputs:** Wing1 `area`, `aspect_ratio`, `sweep`; Fuselage1 `length`, `diameter`. Tails are also settable but were never moved. `taper_ratio` is not a morph input, and tigl reports it as ignored (tigl `parameters.py:50-56,226-240 @0a3bfdd`; BUGS.md B222).
- **pycycle `set_inputs`:** `splitter.BPR`, `fan.PR`, `lpc.PR`, `hpc.PR`, `T4_MAX`, component efficiencies. `fc.alt`/`fc.MN` are filled by the framework only if the agent omits them; an explicit value wins (`DP:2549-2577`).
- **Discipline call sequence and session management.**
- **Recommendations / coordination.**

**Framework-written:** the framework's value replaces the agent's (B143) and the response reports the override (`DP:1259-1297`).

| aviary / pycycle key | Formula | Clamp / gate | Source |
|---|---|---|---|
| `Mission.Design.LIFT_COEFFICIENT` | = SU2 trimmed CL (`aero.cl_cruise`) | Not injected if outside aviary's declared [0.05, 1.0]; `aero_coupling_status = AERO_OUT_OF_BOUNDS` | `DP:2994-3018`; `DP:1688-1698` |
| `Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR` | f = [CD_SU2 + C_f·(S_wet/S_ref)·1.25] / [0.022 + CL²/(π·max(AR,8)·0.85)], with C_f = 0.455/(log₁₀Re)^2.58 (Schlichting). Re from M0.78 / 33,000 ft ISA and MAC (default 4.0 m). S_wet/S_ref = (wing + fuselage wetted)/ref area if 3–9, else 6.0 | **clamped to [0.5, 2.0]** | `CPL:114-123,155-175`; `DP:2979-2986,3041-3059` |
| `Aircraft.Wing.MASS_SCALER` | m_wing,mass-mcp / 5998 kg (aviary's FLOPS wing for its bench geometry) | clamped [0.5, 2.0] | `CPL:72,178-180`; `DP:2640-2682` |
| pycycle `Fn_DES` (lbf/engine) | MTOM·9.81 / (CL/CD_total) / 2 / 4.448 × 1.25, with CD_total = SU2 CD + friction (B170). With no SU2 result yet: MTOM × 0.0811, and an agent's explicit `Fn_DES` is then kept | clamped [2,000, 25,000] | `CPL:183-213`; `DP:2580-2637` |
| `Aircraft.Engine.SCALE_FACTOR` | `prop.fn_lbf` / 5936 lbf (bench deck max cruise thrust at M0.78/33 kft) | **no clamp**. Bounded only by the `Fn_DES` clamp (max 25,000/5936 = 4.21). aviary's [0.8, 1.5] is advisory (warning only) | `CPL:195-218`; `DP:3062-3090`; aviary-mcp `aviary_mcp_server.py:282-287 @27c7b81` |
| aviary wing `AREA`, `ASPECT_RATIO`, `SWEEP` (measured c/4), `TAPER_RATIO`; fuselage `LENGTH`, `MAX_HEIGHT`, `MAX_WIDTH` | taken from the tigl geometry ("geometry authority", default `AVION_DESIGN_AUTHORITY=geometry`) | An agent value that differs by >0.5% is replaced; a key not measured this link is dropped. Geometry is recorded only if 20 ≤ area ≤ 600 m² and 3 ≤ AR ≤ 25 | `DP:843-848,866-868,884-885,1110,1227-1256` |
| SU2 `target_cl`, `ref_area` (2 × semi-span registry value), `ref_length`, `aspect_ratio`, flight state, numerics | see 2.1 rows 7–8 | protected SU2 keys (`FIXED_CL_MODE`, `TARGET_CL`, convergence, Mach, AoA, `REF_AREA`, …) cannot be changed via `update_config_entries` | `DP:855-863,2488-2498` |

In practice this leaves **no free aviary knob** for the agents: every settable aviary parameter is either geometry-authoritative or framework-coupled (`DP:2518-2522`).

**Recorded values (end-state `design.json` per link; 20 links per combination)**

| Combo | Drag factor range (n at 2.0) | MASS_SCALER range | Engine SCALE_FACTOR range | Links at engine scale 0.994 (unsized pyCycle) | Links with Fn at 25,000 clamp |
|---|---|---|---|---|---|
| S_SP | 1.12–1.68 (0) | 1.35–1.51 | 1.38–2.08 | 0 | 0 |
| S_IF | 1.05–1.63 (0) | 1.42–1.50 | 1.16–1.82 | 0 | 0 |
| S_GR | 1.12–2.00 (0) | 1.47–1.59 | 0.99–2.06 | **9** | 0 |
| O_SP | 0.92–2.00 (1) | 1.45–1.49 | 1.03–2.21 | 0 | 0 |
| O_IF | 0.97–2.00 (1) | 1.41–1.53 | 1.26–3.19 | 0 | 0 |
| O_GR | 0.95–1.92 (0) | 1.46–1.54 | 1.22–2.39 | 0 | 0 |
| N_IF | 1.13–2.00 (4) | 1.44–1.50 | 0.99–1.83 | **5** | 0 |
| N_GR | 0.99–2.00 (**18**) | 1.06–1.51 | 1.81–**4.21** | 0 | **15** |

Notes on the table:
- **Method.** Computed read-only on aipexws8 from `design.json` `aero.injected_into_aviary` and `mission_applied`. The 0.994 test is `|scale − 5900/5936| < 1e-3`. The Fn test is `propulsion.fn_lbf ≥ 24,999`.
- **Wing scaler.** No link reached the 2.0 cap.
- **N_GR's 25,000 lbf.** BUGS.md B218 attributes this to agents. The KB shows the agents passing `Fn_DES = 24999.99999997028` (e.g. `RUN_V4/repeat_005/.../step_log_attempt1.jsonl`), a value back-computed from the framework's clamped thrust. It is consistent with the `CPL:213` clamp firing when SU2 CD 0.16–0.45 (B219) makes L/D ≈ 2.

### 2.3 Order the coupling contract enforces before a tool runs

Every MCP call passes through `wrap_tool_with_middleware` (`TC:237-359 @117b55e`), in this order:

1. Type coercion. Then `resolve_request`, which does all the injection above. Then injected arguments the server does not declare are dropped (`TC:239-251`).
2. `UNRESOLVED_REF`: a reference-shaped argument that does not resolve (`TC:258-260`; `DP:2737-2833`).
3. `SESSION_EXISTS` / `SESSION_ALREADY_OPEN`: creating a replacement session is refused (`TC:263-267`; `DP:266-349`).
4. `ALREADY_DONE`: duplicate work on the same design (`TC:273-278`; `duplicate_guard`).
5. Geometry and aero guards (`TC:292-307`):
   - `STALE_GEOMETRY`: mesh/export of the unmorphed baseline after the design moved.
   - `GEOMETRY_NOT_APPLIED`: `run_simulation` while the mission's wing differs from the geometry's by more than 2% (`geometry_guard.py:137-195`).
   - `MORPH_NOT_EXPORTED`: `estimate_mass` / `configure_from_cpacs` on an unexported morph.
   - `AERO_NONPHYSICAL`: `run_simulation` while SU2 CD ≤ 0 or CL is outside [0.05, 1.0] (`aero_guard.py:14,36-75`).
6. `NO_MESH`: `run_su2_solver` on a mesh-less session (`TC:309-313`; `mesh_guard.py:14-57`).
7. `CLAIMED_BY_ANOTHER_AGENT` / `CLAIM_FIRST`: work claims, networked combinations only (`TC:314-318`).
8. **`MISSING_REQUIRED_PARAMETERS`** (`TC:326-331`; `CC:71,125-128,195-229`):
   - **`run_simulation`** requires SU2 CL, SU2 CD, mass-mcp wing mass and a completed pyCycle run.
   - **`run_cycle`** requires mass-mcp MTOM.
   - Each requirement is `ok`, `missing`, or `stale`. Stale means it was produced for an earlier geometry fingerprint: a SHA-256 of the per-session tigl geometry epoch plus the opened CPACS. The mesh is deliberately excluded (`CC:133-153,195-209`).
   - `set_aircraft_parameters` is deliberately not gated (B85; `CC:116-124`).
   - The refusal names each missing variable and the tool sequence that produces it (`CC:232-265`). The same requirement is appended to the tool's description up front (`CC:278-308`).
9. *(v4net only)* `NO_CHANGE_SINCE_FAILED_RUN`: a `run_simulation` that repeats a failed one with no `set_aircraft_parameters` / `configure_mission` / `create_session` in between (`TC:332-338 @7b0a8bc`; `src/tools/mission_guard.py:1-72 @7b0a8bc`, B188).

All toggles default on: `AVION_REQUIRE_COUPLED_INPUTS`, `AVION_REQUIRE_PHYSICAL_AERO`, `AVION_REQUIRE_GEOMETRY_MATCH`. The logs confirm they were active: every code above occurs in the recorded KBs.

**Refusals observed** (KB `outputs.error_code`; two numbers mean `run_simulation` / `run_cycle`):

| Combo | `MISSING_REQUIRED_PARAMETERS` | `GEOMETRY_NOT_APPLIED` | `AERO_NONPHYSICAL` | `NO_CHANGE_SINCE_FAILED_RUN` |
|---|---|---|---|---|
| S_SP | 1 / 0 | 2 | 0 | — |
| S_IF | 1 / 0 | 0 | 0 | — |
| S_GR | 0 / 0 | 0 | 0 | — |
| O_SP | 2 / 0 | 1 | 0 | — |
| O_IF | 43 / 4 | 1 | 0 | — |
| O_GR | 1 / 0 | 4 | 0 | — |
| N_IF | 74 / 87 | 17 | 0 | — |
| N_GR | 193 / 7 | 28 | 20 | 51 |

**Version differences in the contract:**
- **v4net (`7b0a8bc`, N_GR) adds B187, "engine must be sized on this design":**
  - The pyCycle requirement gains the state `engine_stale`. The cycle result is accepted only if it was sized on the current [MTOM, CL, CD] key (`CC:206-211,242-244 @7b0a8bc`; `DP:2639-2672 @7b0a8bc`).
  - `run_cycle` on an unsized session is first auto-sized through `set_inputs` (`DP:2674-2697,2238-2241 @7b0a8bc`).
  - Outputs of an unsized cycle are not captured (`DP:1553 @7b0a8bc`).
  - `SCALE_FACTOR` is not injected from a non-current engine (`DP:3197 @7b0a8bc`).
  - The design is re-applied to aviary with the current engine before `run_simulation` (`DP:2034-2056,2236-2237 @7b0a8bc`).
  - v4net also adds the B188 guard (step 9).
- **v2full and v3combos lack B187.** A pyCycle run on its defaults (Fn_DES 5,900 lbf) satisfied the contract, and its thrust set `SCALE_FACTOR = 0.994`. The end state shows this in S_GR (9/20 links) and N_IF (5/20). It is consistent with B225.
- **B217 (OPEN) leaves a residual in v4net:** N_GR link 10 still records `prop.fn_lbf` 5,899.99 / scale 0.9939 in `design.json`.
- **B214 (`a1dafcc` and `7b0a8bc` only, `DP:2236-2252 @a1dafcc`):** `export_cpacs` into `tests/fixtures/` is redirected to a per-run file. v2full has no such guard.

### 2.4 Known gaps (state these as limitations)

- **B3, propulsion → mission.**
  - No aviary parameter carries pyCycle SFC into fuel burn. The bench aircraft burns a tabulated engine deck (`turbofan_28k.deck`). The fuel-flow scalers measured Δfuel = 0.0000 kg (`.claude/BUGS.md:93-104`).
  - `CC` records `propulsion_coupled: False` for every run (`CC:55-59,349-350`).
  - Only design-point thrust reaches aviary, through `SCALE_FACTOR`, and that thrust is fixed by the framework (2.1).
- **Drag-factor clamp.**
  - [0.5, 2.0] is applied silently (`CPL:175`).
  - N_GR's end state sits at 2.0 in 18/20 links (19/20 per BUGS.md B219, `.claude/BUGS.md:4048-4050`). O_SP, O_IF and N_IF have 1, 1 and 4 such links.
  - The Euler CD on the ~2.3 M-cell mesh is comparative, not absolute ("~40% under the 11.3M value", yaml `:19-24`). SU2 floors it at the induced drag (su2-mcp `drag_model.py:9-12`); 1 N_GR link used that floor.
- **Engine scale is unclamped** relative to aviary's advisory [0.8, 1.5]. Flown values run from 0.99 (unsized pyCycle) to 4.21 (the Fn_DES cap).
- **The wing mass flown is not mass-mcp's wing mass.**
  - `MASS_SCALER` normalises by a fixed bench wing (5,998 kg). aviary multiplies the scaler onto its own FLOPS wing mass for the current wing and gross mass.
  - Across 133 links where the end-state scaler matches the end-state mass-mcp wing, aviary's flown `wing_mass_kg` was **1.41× mass-mcp's** (median; per-combination medians 1.23–1.58).
  - Example: S_SP link 1, mass-mcp 8,491 kg → scaler 1.416 → aviary 10,613 kg (`RUN_V2/repeat_001/mdo_f25_sequential_staged_pipeline/{design,result}.json`).
- **MTOM is not design-responsive when mass-mcp sizing is unconverged.** mass-mcp caps the sizing at SLSQP `max_iter=50`. See 2.1 for the counts.
- **The contract checks existence, not application.**
  - It verifies that SU2/mass/cycle results exist for the current geometry. Injection happens only on `set_aircraft_parameters` (`DP:2518-2522`).
  - Only the engine scale is re-applied automatically before `run_simulation`, and only in v4net.
  - The wing check (`GEOMETRY_NOT_APPLIED`) does not cover the coupled aero or mass keys.
- **Taper never changes** (0.2462 on every link, B222). The tails never move (`MAS-Aviary-optc/scripts/optimizer_baselines/agent_bounds.json`).
- **Dead advisory code.** `mission_coupling_error` (`UNCOUPLED_MISSION`) and `mass_coupling_hint` remain in `DP:2835-2918`, but the wrapper no longer calls them (`TC:356-358`).
- **B117 is fixed in these runs:** the canonical mesh reached the mesher (row 4).

---

## 3. mass-mcp

**Version used:** `1f25297` on aipexws8. It differs from `82780dc` only in the wording of an error message (`git diff --stat`). Earlier commits in the chain set the mission-aware sizing and the D150 airfoil fixture: `28a283e` (B132), `ab8b827`, `82780dc`.

**Tools** (`src/mass_mcp/app.py:12-27`; README `:16-22`):

| Tool | Does |
|---|---|
| `estimate_mass(cpacs_file_path, wing_mass_method='flops', aviary_mass_method='FLOPS', oas_wing_weight_ratio=2.31, material='aluminum', design_load_factor=2.5, write_back_to_cpacs=True, num_passengers, cruise_mach, range_nmi, cruise_altitude_ft, num_engines)` | Full mass estimate (below) (`tools/estimate.py:167-206`) |
| `validate_cpacs_inputs` | pre-flight check of which CPACS parameters are present, missing or out of range (`tools/validate.py`) |
| `get_cpacs_mass_breakdown` | reads an existing CPACS `massBreakdown` without computing (`tools/read.py`) |

**What `estimate_mass` computes**
1. It parses the CPACS file with tigl3 bindings, or XPath as a fallback. The parsed values are wing area, span, c/4 sweep, taper, t/c, AR, fuselage length/width/height, MTOM, engine count, and the mission if present (README `:11`; `tools/estimate.py:29-32`).
2. **Mission precedence:** a tool argument wins over the CPACS value, which wins over the defaults 150 pax / M0.785 / 3,500 nmi / 35,000 ft / 2 engines (`estimate.py:34-59`).
3. **Every component mass comes from NASA Aviary 0.9.10's FLOPS (or GASP) empirical mass equations**, run through a full `AviaryProblem` (`backends/aviary_backend.py:172-271`):
   - The starting point is Aviary's `aircraft_for_bench_FwFm.csv`, overridden with the CPACS values. Tail areas are 0.24/0.19 × wing area; the fuselage wetted area is π·D·L·0.82 (`aviary_backend.py:42-160`).
   - Default height-energy mission, target range = the design range.
   - SLSQP sizing with `max_iter=50`, `run_driver=True` (`aviary_backend.py:203-249`).
   - Outputs are read from `Aircraft.Wing.MASS`, `Fuselage.MASS`, H/V tail, main+nose gear, `Propulsion.MASS`, the seven system masses, `Furnishings.MASS` and `Design.OPERATING_MASS`, plus `mTOM_sized_kg` (= `Mission.Design.GROSS_MASS`) and `sizing_converged = not driver.fail` (`aviary_backend.py:251-353`).
4. **Optional:** with `wing_mass_method` set to `oas` or `both`, OpenAeroStruct 2.x computes the wing (VLM + beam-FEM wingbox, single cruise point with the load factor applied) and replaces or sits beside the FLOPS wing (`estimate.py:73-117`; README `:13,127`). With `flops` or `gasp`, `material` and `design_load_factor` are not used at all (`estimate.py:73-83`). The canonical yaml says the same (`:76-80`).
5. **Output fields:**
   - `mass_breakdown` with `mTOM_kg` (the input/start mass, echoed), `mTOM_sized_kg`, `sizing_converged`, `mOEM_kg`, `mFuel_kg`, `mPayload_kg` (= pax × 90 kg) and `components` (wing, fuselage, empennage, landing gear, propulsion, systems, furnishings).
   - Optionally `oas_comparison`, plus `sizing_conditions` and `warnings` (`models.py:95-127`; `aviary_backend.py:333-353`).
   - Results are written back into the CPACS file. MAS-Aviary redirects this to a per-run copy (`DP:2246-2288`).

**What the runs used.** The study pins `wing_mass_method="flops"`, `design_load_factor=2.5`, `material="aluminum"` and the F25 mission (`DP:1320-1355,2260-2275 @117b55e`; yaml `:75-80`).
- Every recorded `estimate_mass` call across the eight combinations used `wing_mass_method="flops"`.
- `aviary_mass_method` was `"FLOPS"` or left at the default (FLOPS); one O_GR call passed `"flops"`.
- Every end-state `structures.wing_mass_source` is `flops`.
- **So the wing mass is Aviary's FLOPS regression on the morphed CPACS geometry, and OpenAeroStruct was not used.**

**Citable references as listed in the repository** (README `:166-193`; `pyproject.toml` dependencies `aviary==0.9.10`, `openaerostruct>=2.0`, `openmdao==3.36.0`, `dymos==1.13.1`):
- Aviary Development Team, "Aviary: A Multi-fidelity Aircraft Design Tool," NASA, 2023. https://github.com/OpenMDAO/Aviary. Per the README, it contains "FLOPS and GASP mass estimation equations ported from NASA's legacy Fortran codes".
- J. P. Jasa, J. T. Hwang, J. R. R. A. Martins, "Open-source coupled aerostructural optimization using Python," *Struct. Multidisc. Optim.*, 2018, doi:10.1007/s00158-018-1912-8 (OpenAeroStruct; not exercised in the runs).
- J. S. Gray, J. T. Hwang, J. R. R. A. Martins, K. T. Moore, B. A. Naylor, "OpenMDAO: An open-source framework for multidisciplinary design, analysis, and optimization," *Struct. Multidisc. Optim.*, 2019, doi:10.1007/s00158-019-02211-z.
- CPACS (DLR, cpacs.de), TiGL (DLR-SC), and CEASIOMpy (source of the D150 fixture).
- *Not in the repository:* a primary FLOPS reference (e.g. McCullers' FLOPS user's guide) is not cited anywhere in mass-mcp. The writer should add the original FLOPS citation if the paper names the method.

**Caveats to disclose** (`Outil/docs/MCP_TOOL_INVENTORY.md:1169-1204`, audited at the older `bb99056`; still valid where noted):
- `mTOM_kg` echoes the input. At `1f25297` the sized value is reported separately and only trusted when `sizing_converged` is true (`DP:633-638`).
- `mFuel_kg` is not a fuel burn.
- The 50-iteration sizing often does not converge.
- The `gasp` wing method is only a label.

---

## 4b. Design-variable bounds

Every bound in the table below is **advisory**. No tool rejects an out-of-range design variable:
- tigl `set_high_level_parameters` has no range checks (`tigl-mcp src/tigl_mcp/tools/parameters.py:173-259`, `morph.py @0a3bfdd`).
- pycycle `set_inputs` sets any value (`pycycle-mcp src/pycycle_mcp/tools/variables.py:70-104 @cc1a88d`).
- aviary `set_aircraft_parameters` applies out-of-range values and only appends a warning, `valid` staying true (`aviary_mcp_server.py:282-300 @27c7b81`; bounds in `design_space.py:3-180`).

The only hard limits are the data-plane clamps on the coupled quantities and the data-plane geometry capture filter. The prompt table is the same in every combination: `config/mdo_f25_sequential_agents.yaml:29-71 @117b55e` (sequential and orchestrated), and `networked.md:27-69` (N_IF) and `:405-447` (N_GR) @`a1dafcc`/`7b0a8bc`, which differ from it only in ASCII symbols.

| Variable | Tool key | Range stated in prompt | Enforced? | Observed range in the runs (all 160 links) |
|---|---|---|---|---|
| Wing area | tigl `Wing1.area` | 100–160 m² | No. aviary advisory 100–160 → warning. Data plane records the geometry only if 20–600 m² (`DP:884-885,1110`) | measured 20.8–304.9 m² (both extremes N_GR link 20) |
| Wing aspect ratio | tigl `Wing1.aspect_ratio` | 7–17 (reliable 9–11.5) | No. aviary advisory 7–17 / reliable 9–11.5 → warning. Capture filter AR 3–25 | measured 2.5–36.3 (N_GR); requested 8.4–22 (N_GR), ≤13 elsewhere |
| Wing sweep | tigl `Wing1.sweep` (positioning angle; aviary gets the measured c/4 sweep) | 15–40° | No. aviary `SWEEP` advisory 15–40 → warning | measured c/4 −77.1 to 23.6°; requested down to −37.5° (N_GR) |
| Wing taper | none (tigl ignores `taper_ratio`) | "follows geometry" | — | 0.2462 on every link (B222) |
| H/V tail area, AR, sweep | tigl `Wing2H.*`, `Wing3V.*` | "not set" | No | never changed |
| Fuselage length | tigl `Fuselage1.length` | 28–50 m | No. aviary advisory 28–50 | 37.2–38.1 m |
| Fuselage diameter | tigl `Fuselage1.diameter` | 3–5.5 m | No. aviary `MAX_HEIGHT`/`MAX_WIDTH` advisory 3–5.5 | 3.65–4.64 m |
| Bypass ratio | pycycle `splitter.BPR` | up to 10 | No (the HBTF's BPR balance 2–10 is off-design only, `high_bypass_turbofan.py:166`) | 3.5–10 |
| Turbine inlet temp. | pycycle `T4_MAX` | ≥ 2857 °R | No | 1500–5532 °R |
| Fan pressure ratio | pycycle `fan.PR` | 1.4–1.7 | No | 1.2–1.8 |
| LPC pressure ratio | pycycle `lpc.PR` | "not set" | No | 1.5–2.8 |
| HPC pressure ratio | pycycle `hpc.PR` | up to 30 | No | 1.8–14 |
| Component efficiencies | pycycle `*.eff` | "fixed" | No (stated only) | — |
| Design thrust | pycycle `Fn_DES` | "set by framework" | **Yes**, framework value replaces the agent's once SU2 aero exists; clamp 2,000–25,000 lbf (`CPL:213`) | 5,900 (unsized) to 25,000 |
| Engine scale | aviary `Aircraft.Engine.SCALE_FACTOR` | "set by framework" | **Framework-set**, unclamped (aviary advisory 0.8–1.5, warning only) | 0.99–4.21 |
| Drag factor | aviary `SUBSONIC_DRAG_COEFF_FACTOR` | "set by framework" | **Yes**, clamp [0.5, 2.0] | 0.92–2.00 |
| Wing mass scaler | aviary `Wing.MASS_SCALER` | "set by framework" | **Yes**, clamp [0.5, 2.0] | 1.06–1.59 |
| Design CL | aviary `Mission.Design.LIFT_COEFFICIENT` | "set by framework" (~0.56) | **Yes**: not injected outside [0.05, 1.0]; `run_simulation` refused (`AERO_NONPHYSICAL`); target CL kept only in 0.05–1.2 | 0.155–0.907 (N_GR); 0.48–0.66 elsewhere |
| Wing span | aviary `Aircraft.Wing.SPAN` | "follows area and AR" | **Yes**, read-only (`READ_ONLY_PARAMETER`, `design_space.py:39-50`) | — |

Sources for the "observed" column:
- Measured geometry and set pyCycle values: `MAS-Aviary-optc/scripts/optimizer_baselines/agent_bounds.json @64e54be` (160 links; min/max over every successful morph, every `set_inputs` and every end-state `design.json`).
- Requested morph values, clamped quantities and CL: my read-only pass over the KBs and `design.json` on aipexws8.
- The extreme N_GR geometries (e.g. 20.8 m², AR 36) fall outside the data-plane capture filter. For those, aviary kept the last recorded wing.

**Fixed mission.** This is an enforced control: requests to change it are overridden.
- **Values:** range 2,500 nmi, 239 passengers, cruise Mach 0.78, cruise altitude 33,000 ft (FL330) (`config/mdo_f25_canonical_baseline.yaml:96-103`).
- **Where it is pinned:**
  - `configure_mission` and `estimate_mass` are pinned to these values (`DP:1320-1355,2260-2275`).
  - The same point is injected into SU2: Mach plus ISA freestream 26,436 Pa / 222.8 K (`DP:2413-2423`; yaml `:35-40`).
  - It is also the pyCycle design point `fc.alt`/`fc.MN` (`DP:2549-2577`).
  - It reaches aviary's solve at `27c7b81` (`aviary_runner.py:148-170,265-288`; commit `32cd7a3`). The tool inventory's "Mach/altitude/pax inert" (`MCP_TOOL_INVENTORY.md:995`) is outdated for these runs.
- **Observed:** every recorded `configure_mission` call across all eight combinations has exactly (2500, 239, 0.78, 33000). The end-state `design.json` `mission` block matches.
- **Stated in the prompt, not enforced by any tool:** objective fuel burn ≤ 15,000 kg; constraint GTOW ≤ 90,000 kg (`config/mdo_f25_sequential_agents.yaml:22-26`; `networked.md:22-24,400-402`).
- **Engines:** two. The prompt table lists the count as "fixed". mass-mcp's default is 2 (`estimate.py:37-38`), and `num_engines=2` appears in the KB inputs (e.g. S_SP link 1). The data plane does not pin it.

---

# Part C: handlers, model settings, timeout and retry, hardware (questions 4c-4h)

Paths are relative to `MAS-Aviary/` unless marked otherwise. `@117b55e` = v2full (S_SP, S_IF, S_GR, O_SP, O_IF); `@a1dafcc` = v3combos (O_GR, N_IF); `@7b0a8bc` = v4net (N_GR).
**Version scope.** Between a1dafcc and 7b0a8bc only `graph_preinit.py`, `data_plane.py`, `mission_guard.py`, `coupling_contract.py`, `type_coercion.py` and `best_practices.md` change (`git diff --stat a1dafcc 7b0a8bc -- scripts src config`). None of them touch handlers, budgets, model settings or the runner. Between 117b55e and a1dafcc the changes that matter here are the O_GR/N_GR router wiring, the N_IF handler fix (B207), and the networked board graph (details below). The following are identical in all three commits: `scripts/stat_batch_runner.py`, every `config/*.yaml`, `src/llm/*`, `src/config/loader.py`, `graph_router.py` and `staged_pipeline_handler.py`.

---

## 4c. Staged pipeline

**Seven stages**, always in the same discipline order. The same names are used in both variants.

| # | Stage (agent) | MCP / what it does | Completion criterion (observational) |
|---|---|---|---|
| 1 | `geometry_engineer` | TiGL: open CPACS, morph planform, export CFD volume mesh | `tool_called open_cpacs` |
| 2 | `aerodynamics_analyst` | SU2: session, mesh, trimmed RANS/Euler solve, CL/CD from history | `tool_called run_simulation` (see note) |
| 3 | `structures_analyst` | mass-mcp (FLOPS): OEM and component masses | `tool_called estimate_mass` |
| 4 | `propulsion_analyst` | pyCycle HBTF: design-point cycle, SFC/thrust | `tool_called run_cycle` |
| 5 | `mission_architect` | Aviary: `set_aircraft_parameters` (inline validation) | `tool_called set_aircraft_parameters` |
| 6 | `simulation_executor` | Aviary: `run_simulation` (SLSQP mission) | `tool_called run_simulation` |
| 7 | `mdo_integrator` | `check_constraints`, VERDICT COMPLETE/CONTINUE/RETRY, recommendation | `output_contains verdict_present` |

Sources: `config/mdo_f25_staged_pipeline_sequential.yaml:24-84 @117b55e` (sequential); `config/mdo_f25_staged_pipeline.yaml:27-302 @117b55e` (orchestrated). Role texts: `config/mdo_f25_sequential_agents.yaml:81,135,193,235,286,325,369 @117b55e`.

**Handler rules, shared by both variants** (`src/coordination/staged_pipeline_handler.py @117b55e`):
- Each stage runs once. Completion criteria are "observational only — they never block advancement" (`:1-10`, `:452-459`). There is no per-stage retry loop (`src/runners/batch_runner.py:212-219 @117b55e`).
- The pipeline ends early if a stage's output contains `TASK_COMPLETE` (`:491-493`). It is also aborted when a stage called `set_aircraft_parameters` and never got `valid=true` (`abort_on_validation_exhaustion`, default True, `:138-141`, `:499-501`).
- Context mode is `all_stages`. Verdict patterns are VERDICT, TASK_COMPLETE, GEOMETRY_SET, SOLVER_CONVERGED, OEM_KG, SFC_CRUISE, CONVERGED, COMPLETE, CONTINUE, RETRY (`src/runners/batch_runner.py:106-137 @117b55e`).

| | S_SP (sequential) | O_SP (orchestrated) |
|---|---|---|
| Combo config | `strategy_config={"pipeline_template":"mdo_f25"}`, `pipeline_path=…_sequential.yaml` (`batch_runner.py:208-222 @117b55e`) | `lifecycle_mode="per_stage"`, `pipeline_path=mdo_f25_staged_pipeline.yaml`, `worker_input="as_given"` (`batch_runner.py:121-127,223-241 @117b55e`) |
| Who runs each stage | The fixed sequential agent named after the stage. Its prompt is `base_instructions` + its role + the previous DESIGN_STATE, verbatim (`stage_prompt: ""`, `mdo_f25_sequential_agents.yaml:5-10`; `staged_pipeline_handler.py:411-415`) | The orchestrator creates or reuses one worker per stage, and between stages it is asked again whether to retry, advance or skip (`strategies/orchestrated.py:1092-1133 @117b55e`). The worker's message is the orchestrator's task (`as_given`, `staged_pipeline_handler.py:409-410`), so the long `stage_prompt`s in `mdo_f25_staged_pipeline.yaml` **do not reach the worker** |
| Order | Fixed | The runner reorders assignments to the stage names (`batch_runner.py:1297-1308 @117b55e`), but the orchestrator picks the next stage. A stage can be re-run or skipped |
| Step limits | `stage_max_steps: 12` (`config/aviary_mdo_f25_sequential.yaml:7`); `termination.max_turns: 50` (`:13`) | Worker `worker_max_steps: 20`; orchestrator `max_steps: 20`; `max_orchestrator_turns: 8` per delegation round (reset after every worker run, `orchestrated.py:1129-1133`); `stall_threshold: 2`; `max_turns: 30` (`config/orchestrated.yaml:25-46`; `config/mdo_f25_orchestrated_agents.yaml:47`) |

Note: the stage-2 criterion names `run_simulation` (the Aviary tool), not `run_su2_solver`. "Completion met" for the aero stage therefore never measures the SU2 solve. It is observational only, so the run's behaviour is unaffected.

---

## 4d. Graph-routed

**Levels, entry nodes and budgets** are the same for all three GR combos (`src/coordination/graph_router.py @117b55e = @a1dafcc = @7b0a8bc`).

| Level | Nodes that may (re)run | Entry node | Role-run budget per link |
|---|---|---|---|
| SIMPLE | mission_architect, simulation_executor, mdo_integrator | mission_architect | **8** |
| MODERATE | + aerodynamics, structures, propulsion (same geometry) | aerodynamics_analyst | **15** |
| COMPLEX | + geometry_engineer (geometry changes, everything downstream re-runs) | geometry_engineer | **25** |

Sources: `graph_router.py:11-12,22-29` (`LEVEL_OF_NODE`, `ENTRY_OF_LEVEL`, `BUDGET = {simple: 8, moderate: 15, complex: 25}`). The same numbers appear as legacy `resource_budgets.max_passes` in `config/mdo_f25_graph.yaml:517-534`.

**Common mechanics:**
- **Pre-init.** Before the first role of every link, the framework prepares the TiGL session, the default volume mesh, the SU2 session, the pyCycle session, and the aero, mass and engine results. Link 1 uses a cached baseline; later links use the previous design.json (`src/coordination/graph_preinit.py:1-11`). Because of this, a link can start at SIMPLE.
- **Every link starts at SIMPLE** (`conditional_graph_handler.py:198-203 @a1dafcc`; `board_graph.py:6-8,99-114 @7b0a8bc`).
- **Escalation.** The level rises when either of these happens:
  - An agent calls the `request_complexity` tool (B180), or writes `COMPLEXITY: <level>` in its output (fallback; `graph_router.py:57,77-80,186-193`).
  - A rule fires. Rules 2/5/8 fire on structured error codes such as NO_MESH, STALE_GEOMETRY, GEOMETRY_NOT_APPLIED, AERO_NONPHYSICAL → geometry/COMPLEX; MISSING_REQUIRED_PARAMETERS or READ_ONLY_PARAMETER → the producing discipline (`:37-43,107-128`). After a failed simulation, rules 4/6/7/9 apply: aero not trimmed (|CL−target| > 10 %) or engine scale > 2.0 → AERO/MODERATE; thrust < 0.95 × needed → PROPULSION; mass-residual violation → MASS; otherwise MISSION (`:131-161`).
  - The integrator's recommendation is routed to the node that produces the named variable, using a keyword table that checks geometry words first. The level becomes at least that node's level (rules 10–13, `:44-54,197-234`).
- Levels never go down within a link (`higher()`, `:69-70`).
- **Retry rule.** A node may be re-entered at most `RETRIES_PER_VISIT = 3` times before the sweep moves on (rule "1-cap", `:30,182-185`).
- **Re-entry is a Gauss–Seidel sweep.** Entering a node re-runs everything downstream of it (`:8-9,73-74`).

**How the three combos differ:**

| | S_GR (117b55e) | O_GR (a1dafcc) | N_GR (7b0a8bc) |
|---|---|---|---|
| Wiring | `router: conditional` → `ConditionalGraphHandler` (`batch_runner.py:270-289 @117b55e`; `_build_handler` `:1427-1441 @a1dafcc`) | `router: conditional, decider: orchestrator, worker_input: as_given`; `lifecycle_mode: setup_only` (`batch_runner.py:290-315 @a1dafcc`). **At 117b55e O_GR still used the old state-machine handler** (no `router`, `batch_runner.py:290-312 @117b55e`), but O_GR ran at a1dafcc | `networked.graph_router: "board"`, `selection_mode: concurrent_blackboard`, `workflow_phases: []` (`batch_runner.py:317-345 @a1dafcc/@7b0a8bc`). The board router does not exist at 117b55e |
| Who decides the next role | The rule table routes directly (`conditional_graph_handler.py:243-256`) | The rule table proposes. The **orchestrator** sets the level with `set_complexity`: at link start (default SIMPLE), whenever a rule or agent would raise the level, and after REVIEW, when it `assign_task`s the recommendation. Without a call the level stays, and a step needing a higher level is replaced by the current level's entry (`conditional_graph_handler.py:14-19,74-153,245-256`) | **The peers decide on the TODO board.** Any peer may `request_complexity`, which seeds the new level's TODOs and reopens downstream ones. A recorded recommendation reopens its producer's TODO and everything after it. The rule table only posts an *advisory* suggestion (`board_graph.py:1-16,78-96,124-148,177-211`) |
| Budget counted as | Role executions in the link (including retries) vs `BUDGET[current level]` (`conditional_graph_handler.py:210-213`) | Same as S_GR | TODO completions on the board vs `BUDGET[level]`, enforced as a claim limit (`board_graph.py:116-122,205-207`) |
| Link ends when | Integrator verdict COMPLETE / no recommendation, or budget spent (`graph_router.py:207-227`; handler `:210-217`) | Same as S_GR | Integrator records COMPLETE; budget spent; all TODOs done; no claimable TODO; **30 board cycles dispatched** (`networked.py:308` sets `max_concurrent_runs = max(2, graph_max_cycles=30)`; cap check `:1362-1366 @7b0a8bc`); or `max_turns: 60` / 3 consecutive errors (`config/aviary_mdo_f25_networked.yaml:116-119`; `networked.py:532-537`) |
| Per-role step limit | `stage_max_steps 12` | worker 20 / orchestrator 20 | `agent_max_steps 15`, 3 peers (`aviary_mdo_f25_networked.yaml:10,22`); each cycle fires `min(#available TODOs, #peers)` peers (`networked.py:1373`) |

**What the links recorded** (stop reason as written by the handler; counts per link):

| Combo | Stop reasons |
|---|---|
| S_GR (`v2full…/repeat_*/mdo_f25_sequential_graph_routed/result.json`, `graph_stop_reason`) | budget spent at complex 9, moderate 10, simple 1; "complete" **0** |
| O_GR (`/data/avion_runs/v3combos/stat_results/repeat_*/mdo_f25_orchestrated_graph_routed/result.json`) | 15 at moderate 11, 25 at complex 6, complete 3 |
| N_GR (`v4net…/repeat_*/mdo_f25_networked_graph_routed/design.json`, `graph_route.stop_reason`, 16 links with a value) | "30 board cycles dispatched" **13**; 25 at complex 3 |

---

## 4e. Iterative feedback

Handler: `src/coordination/iterative_feedback_handler.py`. Its defaults are used because the runner replaces the coordinator's handler with `_build_handler(combo.handler, combo.handler_config)` (`batch_runner.py:1277-1281 @117b55e`). The handler configs pass no retry or stuck keys: S_IF none, O_IF only `worker_input: as_given`, N_IF none (`batch_runner.py:202-207,242-269 @117b55e`). `config/iterative_feedback.yaml` is not loaded by this path.

| Setting | Value | Source (@117b55e; @a1dafcc line in brackets) |
|---|---|---|
| Max attempts per agent visit | `max_retries = min(cfg.get("max_retries", 20), 20)` → **20** | `:92,126` [`:92,130`] |
| Aspiration (acceptance) | `aspiration_mode = "tool_success"`: accept when **no tool call in the attempt failed** (`not has_tool_errors`) | `:129,664-685` [`:133,684`] |
| What counts as a failed tool call | Any of: `tc.error`; `"success": false`; `"success": true` with `"valid": false` (set_aircraft_parameters inline validation); `return_code != 0`. ALREADY_DONE / SESSION_EXISTS refusals count as success | `src/coordination/feedback_extraction.py:44,89-107,161` |
| Stuck detection | `stuck_threshold = 3`: stop when the last **3** attempts have an identical signature. The signature is the sorted set of error types of failed calls (regex `…Error\|Exception\|Warning\|Fault`, else "unknown") | `:141,255-282,427-429,482-484` [`:145,270,442,497`] |
| Other exits | Asserted `TASK_COMPLETE`; final attempt; toolless agents run once (`retry_toolless_agents: False`) | `:363-367,469-491` |
| Retry context | Last 5 attempts' feedback (`feedback_window 5`). A warning on the second-to-last attempt. A forced "summarise what you tried" message on the final attempt | `:127,615-635` (as_given `:639-662`) |
| Orchestration | O_IF: `lifecycle_mode: setup_only` (one delegation cycle, `batch_runner.py:242-256`). N_IF: 3 peers, `max_concurrent_runs: 2` cycles (`aviary_mdo_f25_networked.yaml:10,54`; `networked.py:430-441 @a1dafcc`). Each peer's turn gets its own retry loop via `IterativeFeedbackHandler.for_peer` (B207, `coordinator.py:523-540 @a1dafcc`) |

**Version difference.** At 117b55e the networked parallel path **bypassed** the IF handler: "the combination ran exactly like networked without it" (B207 comment, `coordinator.py:523-524 @a1dafcc`). N_IF ran at a1dafcc, so it has the fix. S_IF and O_IF are unaffected.

**Observed.** No IF agent visit exceeded **3 attempts** (`attempt_number` max 2; `total_attempts` max 3) in S_IF, O_IF or N_IF. The step logs record `stuck_detected` (grep of `repeat_*/…iterative_feedback/*.json`). In practice the 3-identical-signature stuck rule, not the 20 cap, bounds the loop: failures with no recognisable error type all share the signature `("unknown",)`.

Caveat (code reading). Tool outputs are cut to 2,000 characters before JSON parsing (`iterative_feedback_handler.py:58`). An output that does not parse as JSON (including a truncated one) falls through to `success = True` (`feedback_extraction.py:53-58,106-107`).

---

## 4f. Model settings

| Item | Value | Source |
|---|---|---|
| Agent model | `unsloth/Qwen3-32B-bnb-4bit`: bitsandbytes 4-bit **NF4**, double quant, bf16 compute (from the repo's embedded quantization config) | `config/mdo_f25_run_qwen32b_2gpu.yaml:44,64-65`; ws8 HF cache `models--unsloth--Qwen3-32B-bnb-4bit/…/config.json` |
| Run config used | `config/mdo_f25_run_qwen32b_2gpu.yaml`. Not printed in the logs; inferred (see note) | |
| Backend | transformers via `ThinkingModel(TransformersModel)`, `device_map: balanced`, `torch_dtype: auto` | yaml `:63,69-70`; `src/llm/model_loader.py:229-251` |
| max_new_tokens | **4096** | yaml `:74` |
| Temperature / top-p | yaml sets `temperature: 0.3` (`:75`), but **the transformers path never passes it**: kwargs are only model_id, device_map, dtype, max_new_tokens, reliability (`model_loader.py:231-247`; smolagents 1.24 `TransformersModel` forwards only `max_new_tokens`). So generation uses the model's `generation_config.json`: **do_sample=True, temperature 0.6, top_p 0.95, top_k 20** | ws8 `~/Jessica/avion_hf/hub/models--unsloth--Qwen3-32B-bnb-4bit/snapshots/7f721e7…/generation_config.json` |
| Seed | **No LLM RNG seed** anywhere (no `manual_seed`/`set_seed` in `src`, `scripts/stat_batch_runner.py`). `--base-seed 42` (`stat_batch_runner.py:2114-2118`) only labels the chain anchor. Anchor mode `baseline` = the measured geometry baseline (`/data/avion_runs/v3combos/stat_results/chain_anchor.json`) | |
| Parse retries | `reliability.max_retries: 3` → up to 4 generations per step. A truncated output (unclosed `<think>`) retries with thinking off | yaml `:76-77`; `src/llm/thinking_model.py:456-499` |
| Context limit | Hard limit **40,960** tokens (Qwen3-32B native max; an over-limit prompt raises). Trim budget **30,000** tokens: before each generation, if the prompt is over budget, the oldest whole steps are replaced by one knowledge-base summary block. If that is still over, the largest message is halved head+tail | `src/llm/context_budget.py:1-23,99-140`; `src/tools/knowledge_base.py:41`; `thinking_model.py:597-610` |
| Strategy history window (separate) | sequential/networked `max_recent_messages 15, max_context_tokens 4000`; orchestrated 10 / 3000 | `aviary_mdo_f25_sequential.yaml:16-18`; `aviary_mdo_f25_networked.yaml:121-123`; `orchestrated.yaml:49-51` |

**Thinking.**
- Default `llm.enable_thinking = True`. The env var `MAS_AVIARY_ENABLE_THINKING=0` turns it off (`src/config/loader.py:22-25,161-163`). The yaml sets nothing. The documented ws8 launch is `MAS_AVIARY_ENABLE_THINKING=0 MAS_STEP_LOG=1 CUDA_VISIBLE_DEVICES=1 bash scripts/launch_sweep.sh … --config config/mdo_f25_run_qwen32b_2gpu.yaml … --timeout 480` (`Avion/UI/HANDOFF_UI_SESSION.md:137-139`). The per-generation `thinking_enabled` flags in the step logs confirm the setting:

| Combo | `thinking_enabled` true / false (occurrences in `step_log_attempt*.jsonl`) |
|---|---|
| S_SP, S_IF, S_GR (v2full) | 0 / 1,896; 0 / 2,143; 0 / 5,257 |
| N_IF (v3combos), N_GR (v4net) | 0 / 5,251; 0 / 8,040 |
| O_SP, O_IF (v2full); O_GR (v3combos) | 1,437 / 833; 3,311 / 5,812; 2,153 / 1,034 |

- **Orchestrated override (B216), the same at all three commits.** `strategies/orchestrated.py:415-420 @117b55e/@a1dafcc/@7b0a8bc`: `self._model.thinking_enabled = not is_orchestrator`, set on every `next_step`. This **overrides the env setting**: the orchestrator is OFF and every worker is ON. During message turns (`message_orchestrator`) the orchestrator is forced OFF and then restored (`:1057-1074`).
- Per-agent breakdown confirms it. The orchestrator is mostly false (O_SP 26/566, O_IF 740/5,071, O_GR 78/808) and every worker is mostly true. The workers' "false" entries are truncation retries (`thinking_model.py:489-499`).
- B216 (`Avion/.claude/BUGS.md:4033-4036`) reports O_SP 1,213/1,891, O_IF 2,085/4,431, O_GR 2,153/3,187, i.e. 67–86 % of orchestrated output tokens. My raw grep counts differ for O_SP and O_IF, probably because orchestrator message turns are nested in worker steps (B224). Use B216's figures.

**Summary model.**

| Item | Value | Source |
|---|---|---|
| Model | `unsloth/Qwen3-4B-Instruct-2507-bnb-4bit` (non-thinking instruct, 4-bit) | yaml `:52-54` |
| Device | `cuda:0`. That is the 2gpu config's only change from `mdo_f25_run_qwen32b.yaml`, which uses `cuda:2` | yaml `:1-3,54`; `model_loader.py:142-147` |
| Physical card, ws8 | With `CUDA_VISIBLE_DEVICES=1`, cuda:0 is the **GB300**, the same card as the 32B | |
| Physical card, ws5 | RTX 5090 #0, which also holds part of the "balanced" 32B | |
| Decoding | Greedy (`do_sample=False`), max 400 new tokens | `knowledge_base.py:40,594-596` |
| Input | The newest knowledge-base records up to 9,000 characters, plus a deterministic digest. Asked for "DONE / STILL MISSING / conflicts" in under 300 words | `:529-546` |
| Never the 32B | A ThinkingModel is refused | `:587-593` |
| If unavailable | Deterministic digest | `:554-555` |
| Handoff mode | `MAS_KB_HANDOFF` default `summary`; `recent` bypasses the model (`:354-356,503-504`). The logs show summary mode ran: `[B82] … summaries on unsloth/Qwen3-4B-Instruct-2507-bnb-4bit Nx` in `logs/v2full_qwen_nothink_20261014_083911.log` and in `/data/avion_runs/v3combos/logs/*.log` (up to 18 calls per link) | |

**When the summary model is called** (verbatim prompt mode, used by all three org structures, skips the per-handoff KB prefix: `batch_runner.py:699-706 @117b55e`; `prompt_assembly: verbatim` in `mdo_f25_sequential_agents.yaml:10`, `mdo_f25_orchestrated_agents.yaml:38`, `mdo_f25_networked_agents.yaml:9`):
1. A prompt exceeds the 30k budget and is trimmed (`context_budget.py:118-120`).
2. At the end of each link, to write `kb_link_summary`, which seeds the next link (`stat_batch_runner.py:858-866,1726-1727`).
3. An agent calls `read_design_knowledge(mode='summary')` (`src/tools/knowledge_base_tool.py:14-45`).

Summaries are cached per state signature (`knowledge_base.py:505-512`).

**Note on the inferred run config.** Inferred for every run, because the summaries ran on the 4B model. The non-2gpu config's `cuda:2` does not exist on either 2-GPU machine and would have fallen back to the digest (`mdo_f25_run_qwen32b_2gpu.yaml:1-3`). Also matches the documented launch commands and the live ws8 job's cmdline.

---

## 4g. Per-iteration timeout and runner retry rule

**Timeout:**
- The runner's default is **20 min** (`_DEFAULT_TIMEOUT_MINUTES = 20`, `stat_batch_runner.py:784`; `--timeout`, `:2132-2137`). The study used **480 min (8 h)**:
  - **v2full:** the log shows `TimeoutError: Run exceeded 480m timeout` once (`aipexws8 logs/v2full_qwen_nothink_20261014_083911.log`).
  - **v4net:** same message twice (`…/v4net_graph_qwen_nothink_20261021_133822.log`); `Avion/.claude/CHANGELOG.md:59`.
  - **v3combos:** no timeout fired, so the value is not in the log. Links ran up to 12,653 s (> 20 min; `/data/avion_runs/v3combos/stat_results/stat_progress.json`), and the documented ws5 launch is `--timeout 480` (`Avion/.claude/HANDOFF_NETWORKED_ORCHESTRATED_PROMPTS.md:163-166`).
- Each link runs in a spawned subprocess that is terminated on timeout, so GPU memory is reclaimed (`stat_batch_runner.py:1048-1097`).
- **A timeout is not retried** (B50): the link is marked failed after 1 attempt (`:1822-1834`).
- An Aviary-session heartbeat (every 20 min) keeps the runner's session alive during long links (B113, `:318-331`).

**Zero-fuel retry:**

| Item | Rule | Source (`stat_batch_runner.py @117b55e`, identical at a1dafcc and 7b0a8bc) |
|---|---|---|
| Max attempts | `--max-retries` default **3** = 3 total attempts per link (also used for crash retries) | `:1229,2108-2113,1482` |
| Floor | `fuel_burned_kg <= ZERO_FUEL_FLOOR_KG = 100 kg` counts as zero fuel. Smallest real burn seen: 9,020 kg; artefacts were ≈1e-5 to 1e-3 kg | `:1131-1135,1149-1159` |
| What is retried | Only zero fuel where **no mission was flown** (no simulation output). If the data plane flagged "mission flown, design did not fly" (`_simulation_flown_infeasible`), the link is **kept as an infeasible result** and the integrator's assessment goes to the next link | `:1506-1519` |
| Carries over between attempts | The same deterministic start design (params not regenerated), the same carried CPACS and `carry_over.json` (previous link's integrator assessment), the same `prior_feedback`, and the same chain KB seed (previous link's summary) | `:1524-1536`; `kb_context` `:1491-1503` |
| Reset between attempts | Fresh data plane (`reset_design_state`), new Aviary session (`setup_session_with_params`), new subprocess (agents' memory gone), GPU cleanup. The failed attempt's `design.json` is moved aside to `design_attempt<N>.json` | `:1520-1536`; `:470-478` |

Observed retry outcomes:

| Run | Zero-fuel retries | Flown-infeasible (kept) | Failures |
|---|---|---|---|
| v2full | 12 at attempt 1, 4 at attempt 2, 1 at attempt 3 | 4 | 1 link failed after 3/3 zero-fuel attempts; 1 timeout |
| v3combos | 7 | 18 | 0 |
| v4net | 0 | 2 | 2 timeouts (links 6 and 8) |

Sources: grep of the three run logs.

---

## 4h. Hardware

| | aipexws8 (v2full: S_SP S_IF S_GR O_SP O_IF; v4net: N_GR) | aipexws5 (v3combos: O_GR, N_IF) |
|---|---|---|
| System | "Super AI Station GB300", aarch64 | Lambda Vector, x86_64 |
| GPUs | GPU0 RTX PRO 4000 Blackwell SFF 24 GB; **GPU1 NVIDIA GB300 256,703 MiB**; driver 595.58.03, CUDA 13.2 | **2× GeForce RTX 5090, 32,607 MiB each**; driver 570.207, CUDA 12.8 |
| GPU used by the LLMs | GB300 only (`CUDA_VISIBLE_DEVICES=1`): 32B and 4B both on it. Documented launch: `Avion/UI/HANDOFF_UI_SESSION.md:137`; the live ws8 job's environ has `CUDA_VISIBLE_DEVICES=1`. Not recorded in the v2full/v4net logs themselves | 32B `balanced` over both 5090s; 4B on cuda:0. `MAS_PEER_BATCH_MAX=1` (peers' generations one per forward pass; `coordinator.py:597-602 @a1dafcc`), from the memory note `project_avion_sequential_prompts_v2.md` (10-16 entry), not the log |
| CPU | ARM **Neoverse-V2 (Grace), 72 cores**, 1 thread/core | **AMD Threadripper 7960X, 24 cores / 48 threads** |
| RAM | 743 GiB (MemTotal 779,713,536 kB) | 125 GiB (MemTotal 131,383,776 kB) |
| Software (as installed now) | Avion/.venv: torch 2.7.0+cu128, transformers 4.57.6, smolagents 1.24.0, bitsandbytes 0.50.0 | Same four versions |

Sources: `nvidia-smi -L`, `nvidia-smi --query-gpu`, `lscpu`, `/proc/meminfo`, DMI product name, venv site-packages (read-only, 2026-10-29). MCP servers run CPU-only (`CUDA_VISIBLE_DEVICES=""`; `Avion/.claude/HANDOFF_SETUP_AIPEXWS8.md:160`).

**SU2 parallelism.** su2-mcp runs `SU2_CFD` via `mpirun` with `SU2_MPI_RANKS=auto` (`Avion/setup/start_servers.sh:42-45`; the live ws8 su2-mcp cmdline). The load-aware rule is ranks = max(1, min(cap, ⌊physical cores − 1-min load avg⌋ − reserve 4)), with cap = physical cores (`Avion/su2-mcp/src/su2_mcp/mpi.py:13-19,132-144 @5b019c8`; deployed su2-mcp HEAD b9e0be8 on both machines). Recorded `mpi_ranks` values:

| Machine | Typical ranks | Range |
|---|---|---|
| ws8 (72 cores) | 66–67, e.g. "72 physical cores, load 1.0, reserve 4, cap 72 -> 67 rank(s)" (v2full log l.390-392) | 6–67 in N_GR (concurrent load) |
| ws5 (24 cores) | **18** (65×) or 17 (15×) | 8–18 (`/data/avion_runs/v3combos/logs/*.log`) |

Per-solve limits: the agent-facing `max_runtime_seconds` defaults to 600 s in su2-mcp (`run_tools.py:136 @b9e0be8`). The framework enforces a floor of 3,600 s (`SU2_MIN_RUNTIME_SECONDS`, `src/tools/data_plane.py:849-853`) and a call watchdog of 3,600 s for `run_su2_solver`.

**Other per-run step and time limits** (all identical across commits):

| Limit | Value | Source |
|---|---|---|
| Tool-call watchdog, per tool (s) | default 900; run_su2_solver 3600; run_simulation 1800; estimate_mass 1800; generate_volume_mesh 1800; export_component_mesh 1800; run_cycle 1200 | `src/tools/call_watchdog.py:46-54` |
| Sequential | stage_max_steps 12; max_turns 50; max_consecutive_errors 3 | `config/aviary_mdo_f25_sequential.yaml:7,13-14` |
| Orchestrated | worker_max_steps 20; orchestrator max_steps 20; max_orchestrator_turns 8; stall_threshold 2; max_agents 8; max_turns 30; orchestrator message turn ≤ 6 steps (`MESSAGE_TURN_STEPS`) | `config/orchestrated.yaml:25-47`; `config/mdo_f25_orchestrated_agents.yaml:47`; `strategies/orchestrated.py:48` |
| Networked | 3 initial peers (max 7); agent_max_steps 15; max_concurrent_runs (cycles) 2 for N_IF, 30 for N_GR; max_turns 60 | `config/aviary_mdo_f25_networked.yaml:10,17,22,54,118`; `networked.py:308` |
| Graph-routed | role-run budget 8/15/25 per link; ≤3 re-entries of one node | `graph_router.py:29-30` |
| Iterative feedback | ≤20 attempts per agent visit; stuck after 3 identical | see 4e |
| LLM | ≤4 generations per step (parse retries) | `thinking_model.py:456-464` |
| Runner | 480 min per link, 3 attempts | see 4g |

**Can't be determined from the run files:**
- The exact command line and environment of v2full, v3combos and v4net. The logs start with W&B output, not the command; the values above come from the documented launch pattern plus log evidence.
- Whether `MAS_PEER_BATCH_MAX` was set for v4net.
- CPU or GPU time per run: not recorded (`MAS-Aviary-analysis/analysis/link20/reports/00_README.md:84`).

---

# Part D: baseline and recommendation scoring (questions 6, 7)

Path conventions:
- `MAS-A/` = `/home/aipexws3/Jessica/Avion/MAS-Aviary-analysis` (branch `analysis/20link-metrics`).
- `ws8:` = aipexws8. `ws8:BL/` = `~/Jessica/avion_analysis/baseline/`.
- `ws8:AVM/` = `~/Jessica/Avion/aviary-mcp`. This is the code dir the baseline server ran from. HEAD is detached at `27c7b81` ("feat: a run that does not converge says why (Avion B147)"), with no local changes to the 3 source files, and the files are dated 2026-10-09.
- `ws8:SP/` = `~/Jessica/avion_envs/aviary/lib/python3.11/site-packages/`. This is the aviary env: aviary 0.9.10, openmdao 3.36.0, dymos 1.13.1, scipy 1.17.1.

Note: the aviary-mcp checkout on ws3 (`a74a1c5`, branch `feat/solver-owned-vars-disclosure`) differs from ws8's (md5 of `aviary_runner.py`, `aviary_mcp_server.py` and `session_manager.py` all differ). All aviary-mcp line numbers below are from the **ws8** copy.

---

## 6. Baseline

### What the baseline is
The baseline is the shared link-1 starting design (the "chain anchor"), flown once, unchanged, with no agents involved. It is the same pre-hook the runner performs before agents act on link 1.

### Calls (confirmed from `baseline_mission.py` and `baseline_run1.json`)
The script is `MAS-A/analysis/link20/baseline_mission.py`. It uses a direct FastMCP client and no LLM. It makes four calls on one fresh session (`baseline_mission.py:66-72`; the same sequence for sweeps is in `fly()`, `:32-41`):
1. `create_session(initial_parameters=<chain_anchor.anchor_params>)`.
2. `configure_mission(session_id, range_nmi=2500, num_passengers=239, cruise_mach=0.78, cruise_altitude_ft=33000, optimizer_max_iter=200)`. These values are hard-coded as `CANONICAL_MISSION` (`:21-23`) and copied from `config/mdo_f25_canonical_baseline.yaml` → `mission:` (`MAS-A/config/mdo_f25_canonical_baseline.yaml:96-104`).
3. `run_simulation(session_id, timeout_seconds=300)` (default `--timeout 300`, `:82`).
4. `get_results(session_id)`.

`ws8:BL/baseline_run1.json` records exactly these four responses:
- **`create_session`**: `success: true`, aircraft `"Single-Aisle Transport (737/A320 class) -- aircraft_for_bench_FwFm.csv"`, `created_at 2026-10-22T11:19:55Z`. All 8 anchor parameters were applied, plus a derived `Aircraft.Wing.SPAN = 33.9183 m`.
- **`configure_mission`**: `mission_summary` = 2500 nmi, 239 pax, payload 21,677.3 kg, M 0.78, 33,000 ft, `optimizer_max_iter 200`.
- **`run_simulation`**: `success: true`, `converged: false`, `exit_code: 1`, `iterations: 44`, `runtime_seconds: 7.15`.
- **`get_results`**: the same fuel, GTOW and wing mass as `run_simulation`, with `converged: false`.

Where it ran: a **separate** aviary-mcp server on ws8 port 8601 (`ws8:BL/aviary_8601.log`: "Starting Aviary MCP Server on 127.0.0.1:8601", FastMCP 3.2.0, 2026-10-22 07:17 EDT). The study's own server on port 8600 was not touched. The 8601 server was stopped afterwards by process group (`/home/aipexws3/Jessica/Avion/.claude/CHANGELOG.md:228-232`).

**Second run.** `ws8:BL/baseline_run2.json` (`created_at 2026-10-22T11:20:09Z`) differs from run 1 only in session_id, created_at and runtime (7.21 s vs 7.15 s). This was checked with `diff`. Every result field is byte-identical.

### Anchor design (`ws8:BL/chain_anchor.json`)
The file records `base_seed 42`, `n_links 20`, `mode cumulative_chain`, `anchor baseline`.

| Parameter | Value |
|---|---|
| Aircraft.Wing.AREA | 122.78 m² |
| Aircraft.Wing.ASPECT_RATIO | 9.37 |
| Aircraft.Wing.SWEEP | 21.65 deg |
| Aircraft.Wing.TAPER_RATIO | 0.246 |
| Aircraft.Fuselage.LENGTH | 37.68 m |
| Aircraft.Fuselage.MAX_HEIGHT | 4.15 m |
| Aircraft.Fuselage.MAX_WIDTH | 3.96 m |
| Aircraft.Engine.SCALE_FACTOR | 1.0 |

These values equal the runner's `BASELINE_PARAMS` (`MAS-A/scripts/stat_batch_runner.py:125-134`), which the runner returns when `anchor == "baseline"` (`:138-141`). On ws8 I checked the `anchor_params` in the `chain_anchor.json` of all three study datasets (v2full, v3combos, v4net). All three are identical to the file used for the baseline.

### Result (`baseline_run1.json`, `run_simulation.summary` / `get_results`)
- **Fuel burned:** 18,197.031637 kg (reported as 18,197.0 kg).
- **GTOW:** 91,660.594689 kg.
- **Wing mass:** 8,815.204568 kg.
- Other `get_results` fields:
  - reserve fuel 1,360.777 kg
  - zero-fuel weight 72,102.786 kg
  - cruise CL avg 0.5701, cruise CD avg 0.03370
  - cruise Mach avg 0.78, cruise altitude avg 10,064.5 m
  - cruise SFC avg 0.56335 lb/hr/lbf

### Optimizer and problem settings actually used (aviary-mcp source on ws8 plus installed aviary)
- **Aircraft deck:** `models/test_aircraft/aircraft_for_bench_FwFm.csv` (`ws8:AVM/aviary_runner.py:57`). It sets height-energy equations of motion, FLOPS aero and FLOPS mass (`ws8:SP/aviary/models/test_aircraft/aircraft_for_bench_FwFm.csv:155-157`).
- **Problem type:** SIZING. Aviary hard-sets it in `process_input_decks.py:81`.
  - The sizing design variables are `Mission.Design.GROSS_MASS` and `Mission.Summary.GROSS_MASS`, plus an equality constraint `GTOW` that ties them together (`ws8:SP/aviary/interface/methods_for_level2.py:1331-1360`). The range residual constraint is added if required.
  - The objective is `Mission.Objectives.FUEL`, ref 1 (`methods_for_level2.py:1552-1553`).
  - The phase-trajectory design variables are aviary defaults (`add_design_variables`, `aviary_runner.py:303`).
- **No extra design variables.** `CUSTOM_DESIGN_VARS = {}` (`aviary_runner.py:84-117`, decision B52). SLSQP therefore does not re-choose wing area or engine scale. It only closes the gross-mass sizing and the trajectory.
- **Driver:** `prob.add_driver("SLSQP", max_iter=max_iter)` (`aviary_runner.py:300`). `max_iter` comes from the session's mission config (`:241`), set to 200 by `configure_mission`. The aviary-mcp default is also 200 (`aviary_runner.py:60-66`; `session_manager.py:33-39`; tool docstring `aviary_mcp_server.py:423`).
  - Aviary turns this into OpenMDAO `ScipyOptimizeDriver` with `optimizer='SLSQP'`, `tol = 1e-9` and `maxiter = max_iter` (`methods_for_level2.py:1178, 1238-1239`). Aviary's own default `max_iter=50` (`:1129`) is overridden.
  - The OpenMDAO default `tol=1e-6` (`ws8:SP/openmdao/drivers/scipy_optimizer.py:176`) is overridden by aviary to 1e-9.
  - Total-derivative coloring is declared because the scheme is not shooting (`methods_for_level2.py:1173-1174, 1183-1186`).
- **Run call:** `prob.run_aviary_problem(suppress_solver_print=True, run_driver=True, simulate=False, make_plots=False)` in a thread with a 300 s wall-clock timeout (`aviary_runner.py:352-370`).
- **Mission → phases.** Range, Mach and altitude are written into `phase_info` (`aviary_runner.py:147-179`):
  - The default height-energy phase shape is kept: climb, cruise and descent.
  - Cruise is a 2,000 ft cruise-climb centred on 33,000 ft (`CRUISE_CLIMB_FT`, `:139`).
  - The Mach band is ±0.02 (`:140`).
  - The target range is 2,500 nmi.
  - Passengers are seated 11 first class plus 228 tourist (`_apply_passengers`, `:182-190`).
  - Range, Mach and altitude are also written to `aviary_inputs` (`:265-288`).
- **Convergence flag:** `converged = (exit_code == 0) and not timed_out`, where `exit_code = 0 if not prob.driver.fail else 1` (`aviary_runner.py:376-380`; helper later in the file).
- **"iterations" is not the SLSQP major-iteration count.** It is OpenMDAO `driver.iter_count` (`aviary_runner.py:841-850`). That counter goes up once for the initial run and once per objective evaluation, line-search evaluations included (`scipy_optimizer.py:269, 630`). This is why 44 "iterations" can be reported under caps of 24-50.

### Why the run is unconverged
Source: `run_simulation.failure` in `baseline_run1.json`. The failure block comes from `failure_details()`, `aviary_runner.py:436-481`.
- SciPy SLSQP stopped with `optimizer_message: "Positive directional derivative for linesearch"`, `optimizer_status: 8`, `timed_out: false`. The server log prints "Optimization FAILED. Positive directional derivative for linesearch" (`ws8:BL/aviary_8601.log`).
- `violated_constraint_count: 8` (tolerance 1e-6 on the unscaled gap, `aviary_runner.py:473`). Only the 5 largest are listed (`top=5`, `:436, :480`):

| Constraint | Worst value (equals 0) | Points violated |
|---|---|---|
| traj.climb.collocation_constraint.defects:mass | 298.16 | 15/15 |
| mission:constraints:mass_residual | −22.32 | 1/1 |
| traj.cruise.collocation_constraint.defects:mass | −19.31 | 15/15 |
| traj.descent.collocation_constraint.defects:distance | 16.95 | 15/15 |
| traj.descent.collocation_constraint.defects:mass | 16.40 | 15/15 |

What this means:
- The line search failed before the trajectory collocation defects (mass and distance) and the mission mass residual were closed.
- The returned fuel is the value at the last iterate. It is **not** a feasible, converged optimum.
- The 3 remaining violated constraints are not recorded in the response.

### Iteration-cap sweep (`ws8:BL/maxiter_requested.jsonl`, `ws8:BL/maxiter_fine.jsonl`)
How the sweep was run:
- `baseline_mission.py --sweep` (`:52-65`), with one fresh session per `optimizer_max_iter`.
- Otherwise the same anchor and mission. The same 8601 server log is in `aviary_8601_sweep.log`.
- `maxiter_fine.log` is byte-identical to `maxiter_fine.jsonl`.
- Plots and CSV: `ws8:~/Jessica/avion_analysis/out_20261028/plots/8_optimizer_iterations/` (built by `MAS-A/analysis/link20/maxiter_plot.py`).

| Cap | Reported iterations | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Optimizer message |
|---|---|---|---|---|---|
| 1 | 3 | 18,465.073 | 91,953.13 | 8,837.52 | Iteration limit reached |
| 2 | 4 | 18,042.393 | 91,492.38 | 8,802.37 | Iteration limit reached |
| 3 | 5 | 18,234.537 | 91,701.43 | 8,818.32 | Iteration limit reached |
| 4 | 6 | 18,200.414 | 91,664.28 | 8,815.49 | Iteration limit reached |
| 5 | 7 | 18,196.959 | 91,660.52 | 8,815.20 | Iteration limit reached |
| 6-23 | 8-37 | 18,197.032 | 91,660.59 | 8,815.20 | Iteration limit reached |
| 24-28 | 44 | 18,197.032 | 91,660.59 | 8,815.20 | Iteration limit reached |
| 29-50 | 44 | 18,197.032 | 91,660.59 | 8,815.20 | Positive directional derivative for linesearch |
| 200 | 44 | 18,197.031637 | 91,660.594689 | 8,815.204568 | Positive directional derivative for linesearch |
| 1000 | 44 | 18,197.031637 | 91,660.594689 | 8,815.204568 | Positive directional derivative for linesearch |

- Exact values in `maxiter_requested.jsonl`:
  - cap 5: 18,196.959058
  - cap 10: 18,197.031653
  - cap 15: 18,197.031644
  - cap 20: 18,197.031642
- `converged` is false and `exit_code` is 1 for **every** cap.

Reading:
- From cap 6 on, the fuel is fixed at 18,197.03 kg (to 3 decimals). The CHANGELOG states "cap ≥ 5: 18,197.0 kg within 0.08 kg" (`.claude/CHANGELOG.md:205-208`).
- SLSQP stops on its own from cap 29 on. The message changes between caps 28 and 29.
- Raising the cap to 200 or 1000 changes nothing. The baseline is therefore not a budget artefact: SLSQP stalls in its line search at a point that is still infeasible.
- Only caps 1-4 give different fuel (18,042-18,465 kg).

### Same mission setup as the agents' link 1?
**Yes for the pre-hook.** The runner's `setup_session_with_params` (`MAS-A/scripts/stat_batch_runner.py:370-410`):
- calls `create_session(initial_parameters=settable)` (`:389-391`), then
- calls `configure_mission` with the canonical mission from `load_canonical()["mission"]` (2500 / 239 / 0.78 / 33000 / 200; `:397-410`).
- The chain is reset to the anchor at the start of each combination (`:1376`), and the pre-hook runs at each link (`:1443`).

Across all link-ends, every successful agent `configure_mission` call used the canonical mission (`ws8:~/Jessica/avion_analysis/reports_20261028/design_params/mission_settings.csv`: `calls_differing_from_baseline_mission = 0`, e.g. N_GR 163 calls, N_IF 18).

Differences the writer should state:
1. The baseline is the pre-hook **only**: no agent action, no SU2/mass/pycycle coupling. In the study, the data-plane middleware writes SU2-derived values into aviary before agent missions fly (`Mission.Design.LIFT_COEFFICIENT`, `Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR`: `MAS-A/src/tools/data_plane.py:3043-3137`; wing mass: `:2762`; cycle conditions: `:2608`). So no agent link flies "the baseline plus nothing", even at link 1. The CHANGELOG also records that no link-1 mission flew the unchanged anchor (`.claude/CHANGELOG.md:254-255`).
2. The baseline was flown 2026-10-22, after the study runs, on ws8's aviary-mcp `27c7b81`. **Not verified here:** that every dataset's server ran that same commit. v3combos was copied from ws3 (`CHANGELOG.md:244-245`), and ws3's aviary-mcp checkout is currently a different commit.
3. Like the study's own missions, the baseline is reported as not converged (`CHANGELOG.md:231-232`).

Do not confuse the 18,197 kg baseline with the 6,975 / 7,001 kg "SLSQP reference" in aviary-mcp (`run_reference_benchmark.py`). That reference lets SLSQP choose wing area and engine scale, and it is a different problem (`aviary_runner.py:111-113`).

---

## 7. Recommendation scoring

### Common input (identical in both reports)
- **Items.** The 116 items in `ws8:~/Jessica/avion_analysis/out_20261028/recommended_changes.csv` come from `plots.recommendation_items` (`MAS-A/analysis/link20/plots.py:354-372`). For each link k = 1..19, the **last** recorded integrator recommendation (the one carried to k+1) is parsed by `rec_params.parse` into one item per clause that names exactly one of the 16 plotted parameters.
- **Item kinds:** `value`, `range`, `bound`, `pct` or `direction`. The kind counts in the CSV are value 65, direction 41, range 5, bound 5. No pct item occurs.
- **Where an item sits.** It is placed at the receiving link (`link = k+1`, `plots.py:367`).
- **current** = the parameter at the end of link k (`plots.py:365`).
- **target** = `rp.recommended_value`: the value, the range midpoint, the bound value, or current × (1 ± p %) (`rec_params.py:155-160`).
- **Parser direction:** from the verb ("increase/reduce…"). For value/range items with no verb, it is the sign of (target − the "from" value *written in the text*) (`rec_params.py:105-108, 130-132`).
- **Actual change.** Both reports measure it as end of link k → end of link k+1 (`links.csv`). The reconciliation script confirmed that `recommended_changes.current` equals report 05's `value_k` for all items.

### Report 02: `design_params.rec_vs_actual` (`MAS-A/analysis/link20/reports/scripts/design_params.py:279-308`)
- **Recommended direction.** If the item has a target and a current value: `rdir = sgn(current, target)` (`:288-290`). `sgn` uses REL_TOL = 0.1 % relative to max(|a|,|b|) (`:63, 72-83`). A target within 0.1 % of the value the design already had gets `rdir = 0`. Otherwise (direction-only items) `rdir` = the parser direction (`:292`).
- **Actual direction:** `adir = sgn(prev, cur)` with the same 0.1 % tolerance.
- **Outcomes, in order** (`:294-301`):
  1. `actual not recorded` if either end-state is missing.
  2. `no direction` if rdir = 0.
  3. `did not move` if adir = 0.
  4. Otherwise `same direction` / `opposite direction`.
- **Headline denominators** (`:422-427`):
  - "same direction of items that moved" = same / (same + opposite) = **57 / 65**.
  - "of items with a direction and a recorded actual" = same / (same + opposite + did not move) = **57 / 93**.
- **Target reached:** within **2 %** of [lo, hi]. This applies to value/range items only, with bounds excluded (`:303-304`). It is computed whatever the outcome, including no-direction items: 37 yes, 32 no.

### Report 05: `recommendations_report.py` main(), section 6 (`MAS-A/analysis/link20/reports/scripts/recommendations_report.py:302-348`)
- **Wanted direction:** the **parser direction** if non-zero (`:310`). Only if that is 0 is it `sign(target − value_k)`, with **no tolerance** (`:311-312`).
- **Status** (`:315-325`):
  - `not measurable` if either end-state is missing or want = 0.
  - Otherwise the relative change (v1 − v0)/|v0| gives `unchanged` if |rel| < UNCHANGED_REL = 0.1 % (`:46`).
  - Otherwise `followed` if the sign equals want, else `opposite`.
- **Denominator:** "measurable" = followed + unchanged + opposite (`:341`) = **110**. Followed = **66** (60.0 %).
- **Reached:** within REACH_REL = **1 %** (`:47`) of [lo, hi] for value/range items; on the right side of the bound (±1 %) for bound items (`:326-331`). It is only computed for measurable items: 36 of 73.
- The per-link rollup (54 followed / 3 partly / 34 not, `:388-403`) and the fuel comparison are built on this item status.

### Item-by-item reconciliation (computed, not re-implemented)
- **Script:** `MAS-A/analysis/link20/reports/scripts/rec_scoring_reconcile.py`, copied to and run from `ws8:~/Jessica/avion_analysis/code/`.
- **Outputs:** `ws8:~/Jessica/avion_analysis/reports_20261028/methods_q7/` (`run.log`, `rec_scoring_items.csv`, `crosstab_02_vs_05.csv`, `disagreements_02_vs_05.csv`, `counts_{02,05,proposed}.csv`).
- **How the two rules were run:**
  - Report 02's function was **imported** unchanged.
  - Report 05's block is inline in `main()`, so it was copied **verbatim** into `follow_through_05()` (`rec_scoring_reconcile.py:41-78`).
- **Check:** both reproduce the CSVs behind the reports exactly: "02 reproduces rec_vs_actual.csv: True | 05 reproduces follow_through_items.csv: True" (`run.log`).

Cross-tab of report 02 outcome (rows) against report 05 status (columns), 116 items:

| 02 \ 05 | followed | unchanged | opposite | not measurable | total |
|---|---|---|---|---|---|
| same direction | 57 | 0 | 0 | 0 | 57 |
| opposite direction | 0 | 0 | 8 | 0 | 8 |
| did not move | 0 | 28 | 0 | 0 | 28 |
| no direction (target = current) | **9** | **5** | **3** | 0 | 17 |
| actual not recorded | 0 | 0 | 0 | 6 | 6 |
| total | 66 | 33 | 11 | 6 | 116 |

**The whole difference is the 17 items that report 02 calls "no direction".** On every other item the two rules agree exactly. The small difference in "unchanged" tolerance (relative to max(|a|,|b|) vs |v0|) changed no item, and no item has opposite non-zero directions under the two rules.

What the 17 items are:
- All are **value** items whose target is within 0.0521 % (the maximum) of the value the design already had at the end of link k.
- All 17 have a non-zero parser direction, which report 05 uses (17 of 17).
- By combination: N_GR 11; S_IF 3 (design CL 0.560 when 0.560024); S_GR 2; N_IF 1.
- In these cases the integrator recorded what had *already been done* (e.g. N_GR link 14 input: "area 70" when area was 69.9999). Report 05 then scores the next link's move in the verb's direction:
  - **Followed (9):** S_GR 20 AR; N_GR 13 AR, 14 area, 14 AR, 15 area, 15 AR, 17 area, 17 AR, 18 area.
  - **Opposite (3):** N_GR 16 area, 16 AR, 18 AR.
  - **Unchanged (5):** S_IF 9, 10, 11 CL; S_GR 3 area; N_IF 12 area.
- Example of why this matters: N_GR link 14→15 "followed" a request for "area 70" by moving 69.9999 → 64.98, *away* from the stated target.

Where the reported numbers come from:

| Number | Formula | Value |
|---|---|---|
| Report 02 "57 of 65" | same / (same + opposite) | 57 / 65 |
| Report 05 "66 of 110" | (57 + 9) / (57 + 8 + 28 + 17) | 66 / 110 |
| Report 02 secondary "57 of 93" | same / (same + opposite + did not move) | 57 / 93 |

The 02 secondary "57 of 93" is the like-for-like counterpart of 05's "66 of 110".

The two "reached" figures are not comparable:
- 02: 2 % band, bounds excluded, all outcomes, 69 items.
- 05: 1 % band, bounds included, measurable items, 73 items.

### Proposed single definition for the paper
**Item.** One parameter change parsed from the recommendation that link k carried forward (its last recorded recommendation), scored on the change from the end of link k (v0, the design link k+1 received) to the end of link k+1 (v1).

**Wanted direction:**
- **direction / pct items:** the stated up/down.
- **value items:** sign(target − v0).
- **range items:** towards [lo, hi].
- **bound items:** towards the stated side of the bound.

**Already met (excluded).** An item is excluded if v0 already satisfies it:
- value: |target − v0| ≤ 0.1 % of max(|target|, |v0|);
- range: v0 inside [lo, hi] widened by 0.1 %;
- bound: v0 already on the requested side, ±0.1 %.

**Scored items.** Both v0 and v1 are recorded and the item is not already met.
- **Moved:** |v1 − v0| / max(|v0|, |v1|) > 0.1 %.
- **Followed:** moved in the wanted direction.
- **Opposite:** moved against it.
- **Unchanged:** did not move.
- **Headline:** followed / scored.
- **Secondary figures:** followed / moved; and "target reached" for value/range/bound items, meaning v1 within the target widened by 1 %.

Why this definition:
- It is report 02's direction rule, using the actual design value and not the model's own "from" number. It adds an explicit "already met" class covering range and bound items as well.
- It uses report 05's labels, the "unchanged-in-denominator" headline and the 1 % reach band.
- It differs from report 02 on **one** item only: O_GR link 5 fan PR, range 1.3-1.4 with v0 = 1.4, which 02 counted as "did not move" and the proposal counts as already met.
- It is implemented as `proposed()` (`rec_scoring_reconcile.py:81-133`).

Counts (`counts_proposed.csv`, `run.log`):

| Combination | Followed | Unchanged | Opposite | Scored | Followed % of scored | Followed of moved | Already met (excl.) | No recorded actual (excl.) | Target reached ≤1 % |
|---|---|---|---|---|---|---|---|---|---|
| S_SP | 20 | 1 | 0 | 21 | 95.2 % | 20/20 | 0 | 0 | 11 of 11 |
| S_IF | 7 | 0 | 0 | 7 | 100.0 % | 7/7 | 3 | 0 | 7 of 7 |
| S_GR | 2 | 4 | 0 | 6 | 33.3 % | 2/2 | 2 | 0 | 0 of 0 |
| O_SP | 6 | 7 | 2 | 15 | 40.0 % | 6/8 | 0 | 0 | 4 of 14 |
| O_IF | 3 | 5 | 2 | 10 | 30.0 % | 3/5 | 0 | 2 | 2 of 8 |
| O_GR | 4 | 5 | 3 | 12 | 33.3 % | 4/7 | 1 | 0 | 2 of 12 |
| N_IF | 7 | 3 | 0 | 10 | 70.0 % | 7/7 | 1 | 0 | 2 of 3 |
| N_GR | 8 | 2 | 1 | 11 | 72.7 % | 8/9 | 11 | 4 | 0 of 0 |
| **All** | **57** | **27** | **8** | **92** | **62.0 %** | **57/65 (87.7 %)** | **18** | **6** | **28 of 55** |

Total check: 92 + 18 + 6 = 116.

By parameter (followed / unchanged / opposite, scored items only):
- Wing AR: 29 / 2 / 0
- Wing area: 21 / 3 / 1
- Engine scale factor: 5 / 8 / 5
- Taper: 0 / 6 / 0
- BPR: 1 / 3 / 0
- Wing mass scaler: 1 / 2 / 0
- HPC PR: 0 / 2 / 0
- Fan PR: 0 / 1 / 0
- T4: 0 / 0 / 1
- Sweep: 0 / 0 / 1
- Design CL: all 3 items already met.

Caveats to state alongside the definition (from the reports):
- **End-state comparison only.** A parameter can move for other reasons. Report 05's control: a recommended parameter changed in 77 of 110 recommended transitions, against 551 of 2,232 not-recommended transitions (`05_recommendations.md:238-241`).
- **Parser limits.** The parser is a literal regex. Only 116 items come from 152 carried texts. Unit labels are taken as written.
- **Tolerances.** The 0.1 % and 1 % tolerances are analyst choices.
- **Rollups not yet recomputed.** If the paper adopts this definition, report 05's per-link rollup and its follow-vs-fuel table, which currently include the 17 restating items, should be recomputed. They have not been recomputed here.

---

### Surprises / flags
1. **Report 05's N_GR follow-through is inflated by restating items.** 9 of 05's 66 "followed" are items whose target the design already met. 8 of those 9 are N_GR, whose "16/22 followed" becomes 8/11. Report 05 says follow-through is "highest in … N_GR (16/22)"; under the proposed rule N_GR is 72.7 % on n = 11, with 11 of its 26 items excluded.
2. **"iterations: 44" is OpenMDAO `iter_count` (model evaluations), not SLSQP iterations.** The papers should not write "SLSQP ran 44 iterations".
3. **The baseline is infeasible, not only "unconverged".** It ends with mass/distance collocation defects of up to 298 and a mission mass residual of −22.3. The response lists only 5 of the 8 violated constraints.
4. **aviary-mcp versions differ.** The checkouts on ws3 and ws8 differ. Whether all three study datasets ran the same aviary-mcp commit as the baseline (`27c7b81`) was not verified.
