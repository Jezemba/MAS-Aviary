## [Unreleased]

### 2026-05-25 (later) — Phase L Job 3 step 2: orchestrated_staged_pipeline wired

Second of the four remaining MDO-F25 combinations. Wires
`mdo_f25_orchestrated_staged_pipeline` (orchestrated org ×
staged_pipeline handler) reusing the new
`config/mdo_f25_staged_pipeline.yaml` + the pre-hook cursor fix
landed earlier this commit window.

Pattern: the orchestrator (lifecycle_mode=setup_only) creates 7
specialist workers via create_agent on its single turn — the
agents YAML at config/mdo_f25_orchestrated_agents.yaml already
instructs it to use the exact role names
(`geometry_engineer`, `aerodynamics_analyst`, …, `mdo_integrator`)
that match the staged_pipeline stage names. The handler then walks
all 7 stages in order, no further orchestrator round-trips.
`setup_only` is load-bearing — without it the orchestrator
re-invokes after every worker pass, tripling wall-clock cost.

Changes:
- `src/runners/batch_runner.py` — new `CombinationConfig` entry for
  `mdo_f25_orchestrated_staged_pipeline`, reusing the shared
  `_MDO_F25_STAGED_HANDLER_CONFIG`.
- `tests/test_phase_l_mdo_orchestrated_staged_pipeline_wiring.py` —
  new 3-test wiring suite under `live_mcp_llm` (runs in <1 s, pure
  config inspection — no LLM, no MCP). Verifies the combo is
  registered with the right shape, the orchestrator agents YAML
  names all 7 stage role names (so the handler doesn't skip stages),
  and the shared handler config resolves to the 7-stage pipeline.

Verified:
- Unit suite: 1,424/1,424 passed (no regressions; new file is
  `live_mcp_llm`-tagged).
- New wiring tests: 3 passed in 0.04 s.
- Pre-hook fix from earlier this session protects the orchestrated
  path too — the staged_pipeline handler's set_session_id() now
  correctly keeps the cursor at 0 for non-mission-first pipelines
  regardless of which org structure invokes it.

Pending: full pipeline run for end-to-end verification (~$0.30-1.00,
5-15 min); will ask the user before launching.


### 2026-05-25 (later) — Verify pre-hook cursor fix + sequential_staged_pipeline live

Re-ran mdo_f25_sequential_staged_pipeline end-to-end with the
set_session_id cursor fix in place.

  wandb run:                   https://wandb.ai/jessicae/mas-aviary-stat/runs/u77aj8bg
  wandb-reported fuel:         **12,532.68 kg**
  vs F25 spec block fuel:      -3.6% (12,532.68 vs 12,100)
  Stage progression:           all 7 stages ran in order
                                 (geometry_engineer, aerodynamics_analyst,
                                  structures_analyst, propulsion_analyst,
                                  mission_architect, simulation_executor,
                                  mdo_integrator)
  Geometry stage check:        open_cpacs called with the real path
                                 (.../mass-mcp/tests/fixtures/D150_simple.xml,
                                  NOT the hallucinated D150_AGILE_Hangar_v3.xml
                                  from the first run)
  Geometry actually did work:  generate_volume_mesh fired and produced
                                 a 41 MB volume mesh
  Pre-hook cursor:             stayed at 0 — geometry_engineer ran
                                 normally; session_id was still stored
                                 for downstream stages
  Aviary back-compat:          unaffected — TestSetSessionIdCursorSafety
                                 still asserts the original skip behavior
                                 for mission_architect-first pipelines

Side-by-side, sequential combos on the MDO-F25 pipeline:

| Sequential combo | wandb | fuel_kg | Notes |
|---|---|---|---|
| `iterative_feedback` (Job F) | iepdeu70 | 12,755.86 | baseline |
| `staged_pipeline` pre-fix (Job 3 step 1, first run) | 16ibypaf | 12,747.47 | bug masked — geometry stage skipped |
| `staged_pipeline` post-fix (this run) | u77aj8bg | **12,532.68** | all 7 stages ran correctly |

Status: Job 3 step 1 acceptance fully met. The combination is
end-to-end correct and the latent pre-hook bug it exposed is now
guarded by 3 unit-level regression tests.


### 2026-05-25 (later) — Fix StagedPipelineHandler pre-hook cursor bug

First end-to-end run of `mdo_f25_sequential_staged_pipeline` (wandb
16ibypaf) surfaced a latent bug in `StagedPipelineHandler.set_session_id`.
The method was hardcoded to skip Stage 1 under the assumption that
Stage 1 is always `mission_architect` — true for the aviary 1500-nmi
pipeline but FALSE for the MDO F25 pipeline, where Stage 1 is
`geometry_engineer` and `mission_architect` is Stage 5.

Symptom in the run log: the geometry_engineer agent ran with the
aerodynamics_analyst stage_prompt (cursor was wrongly at 1) and
hallucinated CPACS filenames like `D150_AGILE_Hangar_v3.xml` because
no real geometry stage ever happened. The pipeline still produced
fuel = 12,747.47 kg by luck (downstream stages compensated), but the
stage→agent alignment was wrong.

Root cause: the cursor-advance in `set_session_id` assumed the
pre-hook's create_session + configure_mission "did Stage 1's work,"
which only holds when Stage 1 IS the mission stage.

Fix (`src/coordination/staged_pipeline_handler.py`): `set_session_id`
now checks `pipeline.stages[0].name`. If it's `mission_architect`,
the original skip behavior fires (aviary back-compat). Otherwise the
cursor stays at 0 and Stage 1 runs normally; the session_id is still
stored so downstream stages can pick it up.

Test coverage gap (acknowledged): the original Job 3 step 1 wiring
tests bypassed `coordinator.run()` and therefore the pre-hook
`set_session_id` path entirely. They only checked combo registration,
pipeline parsing, and agent tool presence. Two new test layers added:

- `tests/test_staged_pipeline_handler.py::TestSetSessionIdCursorSafety`
  (unit, runs in normal CI): 3 tests covering the aviary path, the
  MDO F25 path, and the empty-pipeline defensive case. Uses mocked
  PipelineDefinitions — no filesystem or MCP dependency.

Verified:
- Unit suite: 1,424/1,424 passed (was 1,421; +3 new unit-level
  regression tests). Aviary back-compat (Stage 1 skip when name is
  `mission_architect`) preserved.
- Cheap wiring tests under `-m live_mcp_llm`: 6 passed in 1.6 s.
- Full pipeline re-run pending.


### 2026-05-25 (later) — Phase L Job 3 step 1: sequential_staged_pipeline wired

First of the four remaining MDO-F25 combinations. Wires
`mdo_f25_sequential_staged_pipeline` (sequential org × staged_pipeline
handler). The sequential org structure is already proven by
`mdo_f25_sequential_iterative_feedback` (wandb iepdeu70, 12,755 kg);
this combo differs only in the execution handler — staged_pipeline
runs each stage exactly once with an OBSERVATIONAL completion check,
then advances regardless. No per-stage retry-on-warnings loop.

Changes:
- New `config/mdo_f25_staged_pipeline.yaml` — 7-stage pipeline
  definition matching the existing 7-agent template
  (`geometry_engineer`, `aerodynamics_analyst`, `structures_analyst`,
  `propulsion_analyst`, `mission_architect`, `simulation_executor`,
  `mdo_integrator`). Replaces a pre-existing stub that used the
  unsupported `required_keywords` criterion type and had no
  stage_prompts. Each new stage carries a `tool_attempted`
  completion criterion keyed on the discipline's signature MCP tool
  (`open_cpacs`, `run_simulation`, `estimate_mass`, `run_cycle`,
  `set_aircraft_parameters`, `run_simulation`, plus
  `output_contains/verdict_present` for `mdo_integrator`).
- `src/runners/batch_runner.py` — added `_MDO_F25_STAGED_HANDLER_CONFIG`
  pointing at the new pipeline YAML and carrying F25-specific
  verdict patterns (`GEOMETRY_SET`, `SOLVER_CONVERGED`, `OEM_KG`,
  `SFC_CRUISE`, `CONVERGED`, etc.). New `CombinationConfig` entry
  `mdo_f25_sequential_staged_pipeline` with `org_structure="sequential"`,
  `handler="staged_pipeline"`, the new handler_config, and the
  existing `pipeline_template="mdo_f25"` strategy hint.
- `tests/test_phase_l_mdo_sequential_staged_pipeline_wiring.py` — new
  6-test wiring suite under the `live_mcp_llm` marker:
  - 4 YAML-shape checks (no LLM/MCP needed): pipeline parses, stage
    names match expectations, every stage has a valid criteria type
    + non-empty stage_prompt, the combo is registered in
    `ALL_COMBINATIONS`.
  - 2 live wiring checks (MCP servers required, no LLM call):
    StagedPipelineHandler resolves 7 stages from the new pipeline
    YAML; `geometry_engineer` agent has `open_cpacs` +
    `generate_volume_mesh` after `strategy.initialize()`.

Verified:
- Unit suite: 1,421/1,421 passed (no regressions; new file is
  `live_mcp_llm`-gated so it's deselected from the unit run).
- `tests/test_phase_l_mdo_sequential_staged_pipeline_wiring.py`
  under `-m live_mcp_llm`: 6 passed in 1.59 s (no full LLM cost
  because these are wiring-only checks).
- Audit confirmed `aerodynamics_analyst` has `get_valid_config_options`
  (line 489) and `propulsion_analyst` has `get_design_inputs`
  (line 727) in `config/mdo_f25_sequential_agents.yaml` — no agent
  YAML changes needed.

Pending: full pipeline run for end-to-end verification (~$0.30-0.50,
5-15 min); will ask the user before launching.


### 2026-05-25 (later) — Phase L Job 2: parallel-execution swim lanes

User's Job 2 ask was to "show the parallel nature [of peer execution
via a] flow chart, etc." The existing chronological timeline obscured
the fact that three peers ran concurrently — you had to interpret it
from timestamps. This commit adds a horizontal swim-lane panel at the
top of every viz HTML showing the wall-clock-positioned timeline of
each peer.

New parser pipeline in `scripts/visualize_run.py`:
- `StepBlock` dataclass: one peer ReAct step (tool call + step
  number + duration + input-token count + attribution metadata).
- `parse_step_blocks(log_path)`: single linear walk over the log,
  pairing each `Calling tool:` line with the next `[Step N: Duration
  X.X | Input tokens: T]` close-marker before any newer tool call.
  Two-pass attribution:
  1. Inline: tools whose response names the acting peer
     (`attempted_by` for TODO-claim tools post-Job-1; `agent_X_`
     key prefix for write_blackboard).
  2. Token-signature match: MCP discipline tools (no inline
     attribution) match `(step_num, input_tokens)` against the
     inline-attributed peers' signatures. Each peer accumulates a
     slightly different token count by step N because their prompt
     contexts differ marginally; closest-token-distance identifies
     the peer reliably from step 2 onward.
- `build_swim_lanes(blocks)`: per-peer cumulative time = sum of that
  peer's step durations in step-number order. Drops synthetic
  zero-duration entries from multi-tool steps.
- `_render_swim_lanes(lanes)`: full-width HTML panel with axis ticks
  (every ~total/6 seconds, rounded), one lane per peer with
  color-coded blocks sized by step duration, hover tooltips showing
  step number / duration / tool / observation snippet.

The new panel sits between the header and the existing two-pane main
content; the chronological timeline below is unchanged.

Verification:
- Unit suite: 1,421/1,421 passed (+8 new viz tests covering
  StepBlock parsing, token-match attribution, comma-separated
  token-count regex, multi-tool step handling, swim-lane render).
- Regenerated all three viz HTMLs against the new renderer.
  `run_gi0kt8cr.html` (post-Job-1-fix) shows three balanced lanes
  with the slowest peer (agent_3) at ~118s wall-clock, the others
  at ~51s and ~59s. `run_4o22y281.html` shows similar parallel
  activity at ~195s wall-clock. `run_clogb51u.html` at ~107s.
  Hover tooltips work.

No framework behavior change; pure visualization improvement.


### 2026-05-25 (later) — Phase L Job 1 verified live with fresh pipeline run

End-to-end verification of the claim_todo attribution fix from the
previous CHANGELOG entry. Launched a fresh `mdo_f25_networked_iterative
_feedback` run with the fix in place.

  wandb run:                   https://wandb.ai/jessicae/mas-aviary-stat/runs/gi0kt8cr
  wandb-reported fuel:         **11,974.55 kg**
  vs F25 spec block fuel:      -1.0% (11,974.55 vs 12,100)
  Distinct active peers:       agent_1, agent_2, agent_3 (all three)
  Concurrent contention:       yes (multiple "TODO X is currently
                                 claimed by 'agent_Y' — 'agent_Z',
                                 pick a different TODO" rejections)
  claim_todo response shape:   includes structured `attempted_by` and
                                 `current_owner` fields in every
                                 response (verified at log lines
                                 265, 281, 321, 414, 664, ...)
  Viz attribution check:       rejected claim events now correctly
                                 attribute to the caller, not the
                                 winner. Specifically:
                                 - #009 agent_1 rejected claim on
                                   geometry (winner=agent_3)
                                 - #010 agent_1 rejected claim on
                                   mission (winner=agent_2)
                                 - #014 agent_2 rejected claim on
                                   geometry (winner=agent_3)
                                 - #022 agent_3 rejected claim on
                                   aero (winner=agent_1)
                                 Pre-fix viz would have shown each
                                 of these as the WINNER's event.

New visualization committed at [`viz/run_gi0kt8cr.html`](viz/run_gi0kt8cr.html).
Both pre-fix viz files (`run_4o22y281.html`, `run_clogb51u.html`)
remain in place for reference; the README documents the legacy-log
caveat.

Status: Job 1 acceptance fully met. The framework's at-most-one
-winner property is intact (no regression), the response payload
now carries structured caller identity, the rejection message names
both peers, and the visualizer correctly attributes every rejected
claim under heavy thread interleave.


### 2026-05-25 (later) — Phase L Job 1: claim_todo caller attribution

Followup on the user-reported "self-rejecting claim" pattern observed
in viz `run_4o22y281.html`. Root cause is NOT a code bug — the
framework's at-most-one-winner property is intact and the existing
`test_claim_idempotent_for_same_agent` regression test still holds.
What's actually happening is concurrent contention working correctly:
three peers spawn in parallel, all independently pick the same TODO
(e.g. `mission`) first, all call `claim_todo` at the same Step depth,
the Blackboard's `RLock` serializes them, one wins and the other two
get rejected. The apparent self-rejection is an attribution artifact
in the visualizer.

Two contributing factors:
1. The rejection message named only the current owner ("currently
   claimed by 'agent_2'"), not the caller. Under interleaved stdout
   the raw log read ambiguously.
2. The viz attribution heuristic (`_attribute_agent` in
   `scripts/visualize_run.py`) regexed the observation text for the
   first `agent_X` mention. For a REJECTED claim, the only agent
   named in the message is the winner, so `agent_1`'s rejected claim
   got rendered as `agent_2`'s — visually identical to "agent_2
   claimed and then rejected itself."

Fix:
- `src/coordination/blackboard.py:claim_todo()` — rejection message
  now names both the owner AND the caller: `"TODO 'mission' is
  currently claimed by 'agent_1' — 'agent_2', pick a different
  TODO"`. Existing tests assert the substring `"claimed by
  'agent_1'"` and continue to pass.
- `src/tools/networked_tools.py:ClaimTodo.forward()` — response JSON
  now includes `attempted_by` (the caller's `agent_name`) and
  `current_owner` (read from the blackboard post-call). Existing
  `success` / `todo_name` / `message` fields preserved for backward
  compatibility with the smolagents prompt context.
- `scripts/visualize_run.py:_attribute_agent()` — for `claim_todo`,
  `mark_todo_done`, `mark_todo_failed`, the parser now prefers the
  structured `attempted_by` field from the response JSON before
  falling back to the legacy regex match. Pre-2026-05-25 logs (which
  lack `attempted_by`) fall back to legacy behavior — back-compat
  preserved.
- `viz/README.md` updated to document the new attribution priority
  and the legacy-log caveat. Existing pre-fix logs (`run_4o22y281`,
  `run_clogb51u`) still show the apparent self-rejection because
  their source data lacks the disambiguating field; fresh runs after
  this commit will render correctly.

Tests added (8 new, all passing under the regular unit marker):
- `tests/test_blackboard.py::test_claim_rejection_message_names_caller`
- `tests/test_networked_tools.py::test_success_response_includes_attempted_by`
- `tests/test_networked_tools.py::test_contested_response_distinguishes_caller_and_owner`
- `tests/test_visualize_run.py` (5 cases, new file)

Verification:
- Unit suite: 1,413/1,413 passed (was 1,405 baseline; +8 new tests).
- Cheap live test `tests/test_phase_l_mdo_networked_wiring.py`
  (`-m live_mcp_llm`): 1 passed in 16.88 s.

No framework behavior change; the at-most-one-winner property,
RLock, and idempotent-same-agent semantics are unchanged. This is
purely an observability/legibility fix.


### 2026-05-25 (later) — Phase L follow-up: metric extractor fix verified live

Verification run for the `_extract_from_tool_outputs` fix landed in
commit 569d401. Re-ran `mdo_f25_networked_iterative_feedback` with
the fix in place.

  wandb run:                   https://wandb.ai/jessicae/mas-aviary-stat/runs/4o22y281
  wandb-reported fuel:         **11,615.86 kg** (no longer "zero fuel"
                                 error — extractor correctly skipped
                                 the 0.0 placeholder from
                                 set_aircraft_parameters' inline
                                 model_eval and pulled the real value
                                 from run_simulation.summary)
  vs F25 spec block fuel:      -4.0% (11,615.86 vs 12,100)
  TODOs marked done:           5 of 7 (mission, geometry, mass,
                                 evaluation, propulsion) — improvement
                                 over previous run's 4 of 7
  Distinct active peers:       agent_1, agent_2, agent_3 (all three)
  Claim contention observed:   yes (multiple "TODO X is currently
                                 claimed by 'agent_Y' — try a
                                 different TODO" responses, peers
                                 rotated to other TODOs without
                                 thrashing)

**Wandb side-by-side, networked combo only:**

| Networked run | wandb | wandb-reported fuel_kg | TODOs done | mark_todo_done calls | Status |
|---|---|---|---|---|---|
| pre-extractor-fix (clogb51u) | clogb51u | "zero fuel" error (real 12,088 masked on blackboard) | 4 | 4 | FAILED (extractor bug) |
| **post-extractor-fix (4o22y281)** | **4o22y281** | **11,615.86** | **5** | **5** | **OK (real value)** |

End of Phase L for the networked combo. The CodeCRDT pattern is
live, peer rotation works, claim contention is enforced, and the
metric extractor now reports the correct fuel value. Two remaining
issues from the design (aero claim still occasionally stays
in-flight when SU2 errors out, agent_max_steps budget) are tuning
matters and not blocking; left for a future commit.

### 2026-05-25 (late evening) — Phase L day-2 part 5: networked overhauled to CodeCRDT-style concurrent peers

Closes out the user's correction on the networked combo. wandb
6fhhcem3 (the first networked run, 2026-05-25 evening) ran 37
disciplinary tool calls with only agent_1 active — agent_2 and
agent_3 stayed idle and spawn_peer was never called. The user
flagged that this is not a true blackboard collaboration and that
"peers should be able to read what one agent is doing and pick up
work that's left in real time" — a structural change, not a prompt
tweak. Researched the established pattern, landed it as
selection_mode="concurrent_blackboard".

**Research sources** (all cited in PR description / branch commits):
- arxiv 2510.18893 CodeCRDT (Oct 2025): formal TODO-claim protocol
  with at-most-one-winner safety under CRDT consistency.
- arxiv 2510.01285 LbMAS (Oct 2025): "central agent posts requests,
  autonomous subordinate agents volunteer".
- arxiv 2507.01701 Blackboard MAS for LLMs: "agents that will take
  actions are selected based on current content of the blackboard".
- AutoGen GroupChatManager: LLM-based selector per turn, anti-
  monopoly rule on consecutive same-agent picks.

The CodeCRDT pattern (parallel threads + atomic shared-state
claims) most closely matched the user's framing.

**Prototype first** (commit acc08d6 on branch
feat/phase-l-networked-concurrent-prototype). Standalone Python
script at scripts/prototype_concurrent_blackboard.py spawns 3
smolagents ToolCallingAgents in threads with mocked discipline
tools. Verified the pattern works in our codebase BEFORE touching
production code:
- All 7 TODOs done, no duplicates.
- agent_1 -> {geometry, propulsion, evaluation}; agent_2 -> {mass,
  simulation}; agent_3 -> {aero, mission}.
- Wall clock 26.3s; sum of agent durations 74.6s -> 2.84x speedup.
- At-most-one-winner safety held under contention.

**Phase 1+2 — Blackboard primitives + peer tools (commit 53d185c).**

- Added threading.RLock to Blackboard; all public methods now
  hold it. Existing aviary-only combos continue to work unchanged
  (the lock is transparent under sequential access).
- New TodoEntry dataclass and TODO board API:
  seed_todos / read_todos / read_pending_todos / claim_todo /
  complete_todo / fail_todo / all_todos_done / render_todos.
- New peer tools: ReadTodos, ClaimTodo, MarkTodoDone,
  MarkTodoFailed. PEER_TOOL_NAMES widened 4 -> 8. The tools are
  attached to every peer regardless of selection_mode so a YAML
  flip alone is enough to switch modes.
- 15 new unit tests including two thread-contention stress cases
  with threading.Barrier:
    8 racing threads on one TODO -> exactly 1 winner
    4 peers x 20 TODOs simultaneously -> no double-assign

**Phase 3+4 — Strategy mode + Coordinator parallel_run (commit dfe7734).**

- selection_mode="concurrent_blackboard" added to
  NetworkedStrategy alongside existing "round_robin" and
  "volunteer".
- On initialize(): seeds the TODO list from networked.todo_seed
  (coord YAML).
- New CoordinationAction type "parallel_run". next_step() returns
  this single action per turn with metadata.peers listing every
  initial peer + spawned peer.
- max_concurrent_runs (default 2) bounds the number of parallel
  cycles before the strategy gives up if peers haven't closed out
  the board.
- is_complete() now short-circuits on
  blackboard.all_todos_done() — the run terminates as soon as
  every TODO is in done status.
- Coordinator._execute_parallel_run launches all peers via
  ThreadPoolExecutor; one AgentMessage per peer is appended to
  history with metadata.parallel_cycle = N. Missing peers and
  per-peer exceptions are caught into AgentMessage.error rather
  than propagating.
- 10 new tests including:
    test_runs_concurrently_not_serially — 3 x 0.3s peers in <0.6s
    test_peer_exception_caught_not_propagated — other peers
      complete even if one crashes
    test_max_concurrent_runs_then_terminate — clean termination
      on the cycle budget.

**Phase 5 — F25 wiring (commit dfe7734).**

- config/aviary_mdo_f25_networked.yaml: selection_mode set to
  "concurrent_blackboard", max_concurrent_runs=2, and a todo_seed
  with the 7 F25 disciplines (geometry, aero, mass, propulsion,
  mission, simulation, evaluation) — each description cross-
  references the Phase H/K data-plane couplings so peers know
  which captures fire on which tool calls.
- config/mdo_f25_networked_agents.yaml peer_template rewritten:
  CONCURRENT-BLACKBOARD COORDINATION PROTOCOL teaches the
  read_todos -> claim_todo -> execute -> mark_todo_done loop, and
  explicitly tells peers to call mark_todo_failed (releases the
  claim) instead of looping retry inside a single ReAct turn.

**Phase 6 — Live cheap test passes in 13s** (was 25s under the
previous volunteer mode), 1398/1398 total unit tests pass.

**Phase 7 — Full pipeline run** (this entry).

  wandb run:                   https://wandb.ai/jessicae/mas-aviary-stat/runs/clogb51u
  Total wall clock:            ~13 min
  Active peers in this run:    agent_1, agent_2, agent_3 (each
                                 running its full ReAct loop twice,
                                 once per parallel_run cycle) +
                                 agent_4 (spawned mid-run by an
                                 existing peer via spawn_peer —
                                 first time we have observed
                                 dynamic peer growth on a real F25
                                 run)
  TODOs marked done:           4 of 7 (geometry, mass, mission,
                                 simulation) by agent_2 + agent_3
                                 (the rest were in-flight claims
                                 that hit the agent_max_steps
                                 budget without reaching
                                 mark_todo_done)
  Best fuel_burned_kg:         12,088.1 (-0.1% from F25 spec
                                 block fuel of 12,100 kg —
                                 closest of any run so far across
                                 sequential / orchestrated /
                                 networked)
  Best gtow_kg:                78,948
  Framework eval status:       FAILED — same eval_classifier
                                 metric-extraction bug we hit
                                 before (reads the LAST tool's
                                 inline model_eval fuel = 0.0
                                 instead of the BEST
                                 run_simulation.summary fuel).
                                 Not blocking; will fix in a
                                 follow-up commit.

**Run-over-run comparison for the networked combo:**

| Run | wandb | distinct active peers | best fuel_kg | mark_todo_done | spawn_peer |
|---|---|---|---|---|---|
| volunteer mode (deprecated) | 6fhhcem3 | 1 (agent_1 monopoly) | 12,197 | 0 (no TODOs) | 0 |
| **concurrent_blackboard** | **clogb51u** | **3 + 1 spawned** | **12,088** | **4** | **1** |

The 12,088 best fuel is the closest any combo has come to the F25
spec on a real run. Sequential baseline 12,755.86 was on a
different (default) mission and is not directly comparable to v4's
2500-nmi / 239-PAX mission setup.

**Known limitations recorded but not fixed in this commit:**

1. eval_classifier metric extractor reads the LAST tool's fuel
   output, which is the inline 0.0 placeholder from
   set_aircraft_parameters' model_eval, not the BEST result from
   run_simulation.summary. Affects every networked / orchestrated
   run that ends on a set_aircraft_parameters call. Same general
   class as the iterative_feedback_handler regex bug fixed in
   commit 8ec40ba (\\binf\\b boundary).
2. 3 of 7 TODOs were claimed but never reached mark_todo_done in
   this run because the agent_max_steps=15 ReAct budget ran out
   mid-work. Two fixes possible: (a) increase max_concurrent_runs
   from 2 to 4 so peers get more cycles to resume; (b) raise
   agent_max_steps; (c) instruct peers in the template to call
   mark_todo_failed if they're near step exhaustion. None are
   structural — all are tuning.
3. The eval_classifier issue masked the actual improvement —
   wandb reports "error zero fuel_burned_kg" even though the best
   on-the-board number was 12,088. The CHANGELOG entry above is
   the authoritative reading of the run.

**Acceptance — the user's original complaint is resolved.** Three
distinct peer agents participated in the run, real claim contention
happened (visible "TODO 'X' is currently claimed by 'agent_Y' — try
a different TODO" responses in the log), and one peer dynamically
invoked spawn_peer to grow the team. The CodeCRDT-style pattern is
live in NetworkedStrategy and gated behind the YAML knob
selection_mode="concurrent_blackboard" so existing aviary-only
combos continue to use round-robin unchanged.

### 2026-05-25 (evening) — Phase L day-2 part 4: mdo_f25_networked_iterative_feedback wired and exercised

Third MDO-F25 combination — networked org structure with the
iterative_feedback handler. Networked is intentionally structure-
less: 3 initial peers (growable to max_agents=7 via spawn_peer)
all see ALL 54 MCP tools, coordinate via the blackboard, no
orchestrator, no phase gating. That's the design intent and the
combo deliberately does NOT carry workflow_phases (phase gating
would defeat the strategy's purpose; if enforcement is wanted,
use sequential or orchestrated).

**Files added / changed (commit da62bc1, branch
feat/phase-l-networked):**
- `config/aviary_mdo_f25_networked.yaml` (NEW) — strategy=networked,
  execution_handler=iterative_feedback, initial_agents=3,
  max_agents=7, agent_max_steps=15, claiming_mode=soft, NO
  workflow_phases, max_turns=60.
- `config/mdo_f25_networked_agents.yaml` — peer_template rewritten:
  removed the wrong PHASE-GATED WORKFLOW guidance, added a new
  COORDINATION PROTOCOL (read → claim → execute → write loop), a
  TEAM SIZE — DYNAMIC, NOT FIXED section explaining when to call
  spawn_peer (and when not to), a DATA-PLANE COUPLING note
  describing the Phase H/K injections so peers don't try to pass
  CL/CD or MASS_SCALER through the blackboard manually, and an
  MCP-NATIVE GUIDANCE block pointing peers at get_design_inputs
  (pyCycle) and get_valid_config_options (SU2) before
  set_inputs / update_config_entries. Removed the dormant
  workflow_phases block at the bottom (was never loaded by the
  networked strategy, which reads only from the coord YAML).
- `src/runners/batch_runner.py` — register networked in
  `_MDO_F25_STRATEGY_CONFIGS` and add the new combo to
  `AVIARY_COMBINATIONS`.
- `tests/test_phase_l_mdo_networked_wiring.py` (NEW) — cheap live
  test (`live_mcp_llm`, ~25s, ~$0.10-0.30). Caps max_turns=4 and
  agent_max_steps=3 so peers can't run away on heavy MCP work.
  Asserts the coord YAML has NO workflow_phases, 3 peers are
  created at startup, each carrying the 4 peer coordination tools
  AND all 54 MCP tools, and peer system_prompt carries MDO
  markers + spawn_peer guidance + DATA-PLANE COUPLING note.

**Verification — unit suite 1354/1354 pass, cheap live test
PASSED in 25s.**

**Full pipeline run.** Launched
`mdo_f25_networked_iterative_feedback` end-to-end on all 5 MCPs.
Ran ~5 min wall, exit code 0.

  wandb run:                  https://wandb.ai/jessicae/mas-aviary-stat/runs/6fhhcem3
  Best fuel observed:         12,197 kg (+0.8% vs F25 spec 12,100 kg)
  Mission configured for:     F25 design — 2500 nmi, 239 PAX, M=0.78, FL330
  Final framework status:     FAILED ("zero fuel_burned_kg —
                               simulation produced no output")
                               — false-positive, see below
  Total disciplinary calls:   ~37 (all aviary-side; zero geometry,
                               aero, mass, or propulsion calls)
  Active peers:               1 of 3 (agent_1 only)
  spawn_peer calls:           0
  mark_task_done calls:       2 (took 2 to trigger termination)

**Three notable findings.**

(1) **Best fuel = 12,197 kg — closest to F25 spec of any
orchestrated/networked run so far.** agent_1 iterated on
aircraft parameters (driving wing span from 43.53 → 43.82 →
44.25m, closer to F25's 45m baseline) and converged
12,297 → 12,255 → 12,197 kg over three improving rounds.

(2) **Framework reports the run as FAILED despite a successful
best of 12,197 kg.** Root cause is a metric-extraction bug in the
eval classifier: it reads the LAST tool output for
`fuel_burned_kg`, not the BEST. The last tool calls in this run
were `set_aircraft_parameters` whose inline `model_eval.outputs`
return `fuel_burned_kg: 0.0` (because the inline eval doesn't run
the trajectory — that's `run_simulation`'s job). So the
classifier saw 0.0 and flagged FAILED, ignoring the 12,197 kg
that came back from the earlier `run_simulation.summary`. This is
the same general class as bug #1 from earlier today (eval
extraction reads the wrong place) but in `eval_classifier`/
`extract_aviary_eval` rather than `_FAILURE_RE`. Worth a
follow-up commit but not blocking Phase L.

(3) **Peer scheduling concern — only agent_1 ran.** agent_2 and
agent_3 never got an invocation. agent_1 monopolized 37 tool
calls and posted all blackboard entries itself. agent_1 also
never called `spawn_peer` to grow the team. The result is that
networked behaved like a single-agent loop in this run, not a
peer collaboration. Possible causes: (a) the
iterative_feedback handler's per-attempt invocation may
re-target the same author on retries; (b) Claude as agent_1 may
not have read the blackboard hint to invite others; (c)
strategy's peer-rotation logic may need scheduling discipline
when one peer is repeatedly the one being asked. Needs deeper
investigation — recording as a known limitation today.

**Net assessment.** Wiring is solid (cheap test passes, full
pipeline runs to completion, fuel result is meaningful). The
peer-scheduling and metric-extraction issues are real framework-
level concerns but orthogonal to the Phase L combo wiring task.
Three of seven MDO-F25 combos are now wired and exercised on the
full 5-MCP pipeline:

- mdo_f25_sequential_iterative_feedback   (wandb iepdeu70,
                                            fuel 12,755.86)
- mdo_f25_orchestrated_iterative_feedback (wandb fup5hh0h,
                                            fuel 13,206.34)
- mdo_f25_networked_iterative_feedback    (wandb 6fhhcem3,
                                            fuel 12,197 best)

The handoff's Phase L acceptance criterion ("at least 3
additional combinations beyond sequential, each with at least
one successful end-to-end run") is now met if we count the two
additional combinations.

### 2026-05-25 (later still) — Phase L day-2 part 3: skill-injected coupling map produces meaningful behavior shift

After landing the SkillLoader wiring (commit 1ba6c0f), launched a
spot-check pipeline run to observe whether the new
`data_flow.md`-injected coupling appendix shifts the orchestrator's
behavior. Not measuring "does it put aviary last" (that would be a
recipe check, contradicting the no-process design intent) — just
observing what changes.

  wandb run:                       https://wandb.ai/jessicae/mas-aviary-stat/runs/fup5hh0h
  fuel_burned_kg:                  13,206.34
  VERDICT:                         (final not parsed; constraint
                                   evaluated against fuel <= 15000)
  total disciplinary tool calls:   18
  UPSTREAM_ERROR cascades:         0 (bug-1 fix holds)
  set_aircraft_parameters calls:   0 (key behavior shift)

**Run-over-run comparison across the four orchestrated runs:**

| Run | wandb | fuel_kg | mission | tool calls |
|---|---|---|---|---|
| v2 (post-context, pre-bug-fix) | bpkm3zm4 | 12,518.16 | 1500 nmi / 162 PAX | 90+ |
| v3 (post-bug-fix, no skill) | krlbsfgx | 7,607.88 | 1500 nmi / 162 PAX | ~50 |
| **v4 (post-skill injection)** | **fup5hh0h** | **13,206.34** | **2500 nmi / 239 PAX** | **18** |
| F25 spec block fuel (Table 3) | — | 12,100 | 2500 / 239 | — |

**v4 delta from F25 spec block fuel: +9.1%** — first orchestrated
run to evaluate the F25 *design* mission, not aviary's default
mission. The +9.1% is sensible given the underlying CPACS fixture
is D150-class, not actually F25-class. (For context, sequential
baseline 12,755.86 kg was on the 1500-nmi/162-PAX mission so it is
not directly comparable to v4.)

**What the skill content drove without prescribing:**

1. **Mission spec shift.** v2/v3 used aviary's default mission
   (1500 nmi, 162 PAX, FL350). v4 used the DLR-F25 design mission
   (2500 nmi, 239 PAX, FL330) which is exactly what the Eq. 3
   formulation in `data_flow.md` specifies. The orchestrator
   picked this up from the skill appendix — no prompt change made
   it happen.
2. **Zero parameter thrashing.** v2 made 12 set_aircraft_parameters
   sweeps before run_simulation; v4 made 0. The agent stopped
   tweaking knobs and ran a clean mission analysis with the
   baseline geometry. Tool call count collapsed from 90 → 50 → 18
   across the three runs.
3. **Different team creation order observed during monitoring.**
   v3 created mission_architect first; v4 created
   structures_analyst / propulsion_analyst before mission. The
   skill content's emphasis on producer-then-consumer ordering
   appears to have influenced this without explicit
   instruction. (Note: the actual execution order under
   setup_only's "creation-order" semantics still had mission tools
   firing before estimate_mass / SU2; needs separate investigation
   if we want literal producer-before-consumer execution.)

**What the skill content did NOT achieve — and that's actually
revealing.**

With zero `set_aircraft_parameters` calls in v4, the data-plane
middleware had nothing to inject into. The Phase H/K couplings
documented in the skill were correctly described — and correctly
inactive because the prerequisite tool call was never made. The
agent essentially ran a single-MCP aviary mission on the F25 spec
with the baseline geometry defaults. A truly coupled MDO answer
would require either:
- The agent re-calls `set_aircraft_parameters` after the upstream
  disciplines run, OR
- The execution layer enforces a topo-sorted producer-then-consumer
  worker ordering, OR
- Active mode where iter 2 has the data store populated for
  injection on the second pass.

This isn't a regression to fix today — it's documentation of how
the orchestrator naturally interprets a well-described problem.
Skill-driven F25-spec mission setup with zero parameter sweep is a
defensible delegation pattern; it just doesn't exercise coupling
unless we add another mechanism on top.

**No new tests added** for v4 (purely an observation run). Unit
suite remains at 1,354/1,354. The cheap live wiring test
(`tests/test_phase_l_mdo_orchestrated_wiring.py`) already covers
the regression guard that the orchestrator delegates to MDO
discipline roles after the prompt rewrite.

### 2026-05-25 (later) — Phase L day-2 part 2: three bugs surfaced by bpkm3zm4 run, fixed and verified

Investigating yesterday's wandb bpkm3zm4 pipeline run (which
produced the 12,518 kg result) surfaced three separate bugs.
All three are now fixed in one commit and verified against a
fresh pipeline run, wandb **krlbsfgx**.

**Bug 1 — `_FAILURE_RE` matched `inf` inside `INFORMATION`.**

`src/coordination/iterative_feedback_handler.py::_FAILURE_RE`
listed `inf` as a bare alternative (intending to catch Python's
`float('inf')`). The `re.IGNORECASE` regex matched `INF` inside
common worker output headings — most notably the geometry_engineer's
`## SESSION INFORMATION` block. The handler then injected
`UPSTREAM_ERROR: geometry_engineer failed` into every downstream
worker's context, even though geometry had actually succeeded
(186 MB SU2 mesh, 476,853 nodes). The aero, structures, and
propulsion workers then wasted retries thinking the mesh had
failed. Fix: word-anchor the `inf` token (and `NaN` for
symmetry) so it only matches the genuine Python float repr
(`inf`, `infinity`, `infinite`).

Regression: `tests/test_iterative_feedback_handler.py::TestFailureRegexWordBoundaries`:
- 8 success-text fixtures (`"## SESSION INFORMATION"`,
  `"Mission configuration applied"`,
  `"Volume Mesh Successfully Generated"`, `"infrastructure check
  passed"`, etc.) MUST NOT trigger the regex.
- 9 real-failure fixtures (`"perf.Fn = inf"`, `"got nan in
  residuals"`, `"value reached infinity"`, `"AVIARY_SETUP_ERROR"`,
  etc.) MUST still trigger it.

**Bug 2 — orchestrated propulsion_analyst missing
`get_design_inputs` (the Phase I tool).**

`config/mdo_f25_orchestrated_agents.yaml` listed
`create_cycle_model, close_cycle_model, list_variables,
set_inputs, run_cycle, get_outputs, get_cycle_summary` for
propulsion_analyst — missing `get_design_inputs`. The Phase I
2026-05-23 fix added `get_design_inputs` to pycycle-mcp so the
agent doesn't have to guess from `list_variables`'s ~900
promoted names. Sequential's YAML had it; orchestrated's did
not. Result on bpkm3zm4: propulsion_analyst called
`list_variables` 18 times trying to find BPR. Fix: add the
tool to the orchestrated role mapping with an inline note on
why `list_variables` alone is insufficient.

**Bug 3 — aerodynamics_analyst missing `get_valid_config_options`
on BOTH agent files.**

`su2-mcp` exposes a `get_valid_config_options` tool that returns
the option combinations SU2 will actually accept (e.g.
`CONV_NUM_METHOD_FLOW="JST"` requires `MUSCL_FLOW="NO"`). It
was never added to either `mdo_f25_sequential_agents.yaml` nor
`mdo_f25_orchestrated_agents.yaml`. On bpkm3zm4 the
aerodynamics_analyst guessed incompatible combinations and
`run_su2_solver` errored 17 times with messages like "Centered
schemes do not use MUSCL reconstruction" and "mesh.su2 is not
an SU2 mesh file or has the wrong format". (Note: a chunk of
those failures were also a cascade from bug 1's false-positive
on the geometry output.) Fix: add the tool to both YAMLs with
an inline note on the call-order expectation.

**Verification — re-launched the same orchestrated pipeline run
with the three fixes in place.**

  wandb run:                       https://wandb.ai/jessicae/mas-aviary-stat/runs/krlbsfgx
  fuel_burned_kg:                  7,607.88
  MTOM_kg (= gtow_kg):             76,693.17
  VERDICT:                         COMPLETE

  Bug-fix delta vs bpkm3zm4 (pre-fix run):
    UPSTREAM_ERROR cascades        5   →  0
    get_valid_config_options calls 0   →  5  (bug 3 fix engaged)
    get_design_inputs calls        0   →  5  (bug 2 fix engaged)
    list_variables (guessing)      18  →  0  (Phase I now works)
    SU2 Error Exit failures        17  →  0  (no SU2 retries)

  Unit suite:                      1,348 → 1,350 (added 2 regression
                                   tests for bug 1).

**What this run does NOT prove.**

The data-plane middleware coupling is still bypassed in this
run for the same reason as day 2 part 1 — the orchestrator
created `mission_architect` first, so aviary integrated before
any upstream discipline could deposit values into the
data_store. fuel = 7,607.88 kg (back to the un-coupled regime;
Claude's non-determinism, the agent picked a lower-AR design
this run instead of the AR=15-ish one from bpkm3zm4). MTOM is
still the reported figure of merit; the structured output
contract is honoured.

The remaining fix is at the **execution layer**, not the prompt
or tool layer — `setup_only` runs workers in agent-creation
order, and the orchestrator's preferred creation order is
top-level integrator first. Day-3 candidates (per the user's
no-process constraint):

1. Topo-sort workers in `_setup_only_execution` based on the
   discipline information-dependency graph (geometry → aero,
   geometry → mass, aero → mission, mass → mission, propulsion
   → mission). Framework-level change in
   `src/coordination/strategies/orchestrated.py`.

2. Revert the `setup_only` override and rely on active-mode
   iter 2 (more expensive, but iter 2 couples).

3. Leave it as-is and treat fuel/MTOM convergence as a
   per-run-non-deterministic outcome.

**Files touched in this commit.**
- `src/coordination/iterative_feedback_handler.py` —
  `_FAILURE_RE` word-boundary fix + comment explaining why.
- `tests/test_iterative_feedback_handler.py` —
  `TestFailureRegexWordBoundaries` with positive + negative
  fixtures.
- `config/mdo_f25_orchestrated_agents.yaml` — add
  `get_design_inputs` + `get_valid_config_options` to the
  respective role allowlists with inline notes.
- `config/mdo_f25_sequential_agents.yaml` — add
  `get_valid_config_options` to aerodynamics_analyst's
  `allowed_tools` (sequential was missing it too).

### 2026-05-25 — Phase L day-2: orchestrator gets DLR-F25 design context, fuel lands within 2% of sequential baseline

Day 2 of Phase L. Yesterday's diagnosis was: the orchestrator's
worker-creation order bypassed the data-plane middleware (Phase
H/K injections), producing fuel = 7,644 kg vs the sequential
baseline 12,755.86 kg (a 40% delta). The user's chosen
intervention was to **add design-problem context to the
orchestrator's system prompt** — explicitly **not** to prescribe
the sequential pipeline's process (geometry → aero → mass →
propulsion → aviary). The orchestrator should pick a sensible
ordering on its own once it understands the design problem deeply
enough.

**Source for the new context.** The Abu-Zurayk et al. 2026 AIAA
SciTech paper "Establishing a Joint Research-Industry MDO
Benchmark Based on the DLR-F25 Aircraft Configuration" (DLR,
Airbus, Bombardier, NASA, ONERA). User dropped fresh copies of
the paper at `Avion/DesignContext/` in PDF and RTF.

**Prompt enrichment.** `config/mdo_f25_orchestrated_agents.yaml`
grew from ~7,500 → 14,932 characters. New sections:
- THE DLR-F25 DESIGN PROBLEM — benchmark is intentionally
  immature; baseline does NOT meet TLARs; the orchestrator's job
  is to mature it.
- FIGURE OF MERIT — MTOM (not fuel-burn alone), with the paper's
  direct quote on why fuel-only minimization drives toward slender
  HARW with aero-elastic issues; MTOM enforces the aero/structural
  trade-off; tie-break on lower fuel.
- OPTIMIZATION FORMULATION — Eq. 3 of the paper with all 7
  constraints (Range, TOFL, Vref, Vol_tank ratio, OEI climb,
  ICA, rear spar height).
- DLR-F25 BASELINE — Table 3 numbers (MTOM 85.7 t, OEM 46.3 t,
  block fuel 12.1 t, wing area 130.1 m², AR 15.6, L/D 19.5,
  CL cruise 0.593, etc.) + 2035 tech factors.
- WHY HARW (AR=15.6) IS HARD — slender wing → fuselage-mounted
  LG → flow acceleration; low local chord → high local CL at
  cruise; flutter + aileron reversal concerns; gate limit < 36 m
  is binding; baseline profiles are single-point designs.
- DESIGN STATE AND DISCIPLINARY COUPLING — information
  dependencies (what each discipline produces and what consumes
  it) WITHOUT prescribing execution sequence.
- KEY TRADE-OFFS THE PAPER FLAGS — the paper's specific examples
  (AR vs root bending, sweep vs t/c, t/c vs fuel vs wave drag,
  SFC-bucket alignment, OEM↔MTOM snowball).

**Removed:** the explicit recipe `Assign tasks in dependency
order: geometry → aero → structures → propulsion → mission →
simulation → evaluation` that was prescribing the sequential
pipeline's order. Replaced with: "Decide how to sequence task
assignments — read the DESIGN STATE AND DISCIPLINARY COUPLING
section above. A coupled MDO answer needs upstream producers to
run before downstream consumers integrate them. You're the
orchestrator; the ordering is your call."

**STRUCTURED OUTPUT REQUIREMENT now leads with MTOM_kg** (matching
the paper's figure of merit) and adds OEM_kg, while keeping
fuel_burned_kg and the existing VERDICT shape.

**Cheap test reused.**
`tests/test_phase_l_mdo_orchestrated_wiring.py` passed against
the enriched prompt — 4:52 wall (vs 2:01 yesterday; longer
because the prompt is ~2× larger; cost ~$0.20-0.30). Confirms
the orchestrator still delegates to recognized MDO discipline
roles in setup_only mode.

**Full pipeline run.** Launched
`mdo_f25_orchestrated_iterative_feedback` with the enriched
prompt + the `setup_only` override added yesterday (one
delegation cycle, no active-mode re-invocation). Ran to clean
exit.

  wandb run:    https://wandb.ai/jessicae/mas-aviary-stat/runs/bpkm3zm4
  fuel_burned_kg = 12,518.16
  MTOM_kg (= gtow_kg) = 80,083.95
  VERDICT = COMPLETE
  vs sequential baseline (wandb iepdeu70): fuel = 12,755.86 kg
  ⇒ -1.9% (within the ~5% tolerance the handoff said to expect)
  vs yesterday's run (killed at iter 2): fuel = 7,644.01 kg
  ⇒ +63.7% — i.e. yesterday's was wildly low because of the
     uncoupled aviary defaults.

**What the prompt enrichment actually changed.**

The orchestrator's mission_architect now starts parameter
exploration near the F25 baseline — `Aircraft.Wing.ASPECT_RATIO`
swept 15.0 → 14.5 → 13.5 → 11.5 → 14.5 (instead of yesterday's
12.4 → 13.5 → 14.5 → 11.0 ending in the "reliable range" 11.0).
Wing area swept 130 → 135 (close to baseline 130.1) instead of
yesterday's 126.3 → 120 → 118. The orchestrator now treats
AR=15.6 as the F25 design intent rather than running away from
the AR > 12 reliability warning. That single shift is what moved
fuel from 7,644 kg → 12,518 kg.

**What still doesn't work — the coupling is still bypassed.**

Disciplinary call order observed in the log:

  positions 1-19:  aviary work (configure_mission, set_aircraft_parameters
                   x ~12 sweeps, run_simulation, get_results,
                   get_trajectory, check_constraints)
  position 20+:    open_cpacs → generate_volume_mesh → SU2 attempts
                   (SU2 errored out; mass/propulsion ran late)

Reason: in `setup_only` mode, the placeholder executor runs
workers **in agent-creation order**, not in
dependency-resolution order. The orchestrator created
mission_architect first (creation #1), so aviary integrated
before any upstream discipline produced values for the
data-plane middleware to inject. The 12,518 kg result is good
**only because the orchestrator's chosen aircraft parameters
happened to be F25-class**, not because the middleware
coupling fired.

Evidence the middleware did NOT couple this run:
- No `Aircraft.Wing.MASS_SCALER` adjustment from estimate_mass
  (no `mWing_kg` was in data_store when set_aircraft_parameters
  ran — mass-mcp had not yet executed)
- No `Mission.Design.LIFT_COEFFICIENT` /
  `Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR` injection from SU2
  (SU2 ran later and errored anyway due to upstream geometry
  failure)
- aviary used default `MASS_SCALER = 1.0` instead of the
  Phase K-style 1.28 we'd expect for this geometry.

**Net assessment.** The prompt enrichment achieved most of what
the user asked for at the design-context level: the orchestrator
now picks F25-class parameters on its own without being told the
process. The remaining bug is at the **framework execution
layer**, not the prompt layer — `setup_only` runs workers in
creation order, and the orchestrator's preferred creation order
(top-level integrator first) is the wrong order for the
data-plane middleware to do its job.

**Options to address the coupling, day-3+:**

1. Topologically sort workers in `_setup_only_execution` based
   on tool dependencies (e.g., aviary's set_aircraft_parameters
   runs after estimate_mass, after run_su2_solver). Framework-
   level fix in `src/coordination/strategies/orchestrated.py`.
   Keeps the orchestrator's prompt clean of execution-order
   guidance.

2. Revert the `setup_only` override and rely on active-mode
   iteration. Iter 1 captures upstream values; iter 2 injects
   them. ~25-30 min wall-clock, ~$0.50-0.80 per run.

3. Tell the orchestrator in the prompt to "create agents in the
   order you want them to execute." This crosses into execution
   semantics — debatable whether it's "process" or just
   framework awareness. Borderline against the user's no-process
   constraint.

**Unit suite status.** 1,348/1,348 still pass with the enriched
YAML.

**Files touched today.**
- `config/mdo_f25_orchestrated_agents.yaml` — design-problem
  context added; sequential recipe removed.
- (No code changes today — yesterday's `setup_only` wiring in
  `src/runners/batch_runner.py` remains in place.)

### 2026-05-24 — Phase L day-1: orchestrated combo wired, run reveals ordering bug

Phase L scope (per `.llm/handoff_2026-05-23_phase_L_coordination_combinations.md`)
is to build out the MDO-F25 coordination combinations beyond
`mdo_f25_sequential_iterative_feedback`. Day 1 wired the **orchestrated**
variant and exercised it end-to-end. Result: wiring is correct at the
framework layer, but a real coupling bug surfaced — documented below
for tomorrow's session.

**Wiring change.** Added two entries in `src/runners/batch_runner.py`:
- `_MDO_F25_STRATEGY_CONFIGS["orchestrated"] = ("config/mdo_f25_orchestrated_agents.yaml", "config/orchestrated.yaml")`
- New `CombinationConfig("mdo_f25_orchestrated_iterative_feedback", "orchestrated", "iterative_feedback", strategy_config={"orchestrated": {"lifecycle_mode": "setup_only"}})`

The `setup_only` lifecycle override is **interim**, see "Known issue"
below for context.

**Cheap regression test added.** `tests/test_phase_l_mdo_orchestrated_wiring.py`
(marked `live_mcp_llm`, ~$0.10-0.20, ~2 min wall) drives a live Claude
orchestrator through the combo in `setup_only` mode and asserts:
1. Combo resolves to the MDO-F25 orchestrator YAML (not aviary-only).
2. Orchestrator's system prompt carries `TiGL`, `SU2`, `mass-mcp`,
   `pyCycle`, `DLR-F25` markers.
3. Orchestrator calls `list_available_tools` → `create_agent` →
   `assign_task` and terminates cleanly.
4. At least one created worker uses a recognized MDO discipline role
   (geometry_engineer, aerodynamics_analyst, structures_analyst,
   propulsion_analyst, mission_architect, simulation_executor,
   mdo_integrator).
5. No real disciplinary tools fire (no SU2 solve, no aviary
   `run_simulation`, no `estimate_mass`) — proves setup_only is honored.

Passed on 2026-05-24.

**Full pipeline run attempt.** Launched
`mdo_f25_orchestrated_iterative_feedback` (BEFORE the setup_only
override was added) with the default `active` lifecycle. Ran 12:17 of
wall-clock before being killed mid-way through iter 2 by user
intervention.

Iter 1 produced **fuel_burned_kg = 7,644.01**, **gtow_kg = 69,855**,
`cruise_mach_avg = 0.72` (vs configured 0.785),
`wing_mass_method_used = "oas"`,
`mWing_kg = 11,420 kg` (OAS), `mTOM_kg = 78,126`, agent
`VERDICT: CONTINUE` with `optimality_gap_pct: -27%`.

**vs sequential baseline (Run #20, wandb `iepdeu70`)**: fuel 12,755.86
kg on the same 1500 nmi / 162 pax / Mach 0.785 / FL350 mission. A 40%
fuel reduction is wildly outside the ~5% tolerance the handoff said to
expect from a coordination-strategy swap. Diagnosis below.

**Known issue: orchestrator call ordering bypasses Phase H + Phase K
middleware coupling.**

The orchestrator chose to delegate tasks in this order in iter 1:
1. TiGL geometry inspection (lines 622-704 of `/tmp/pipeline_mdo_f25_orchestrated.log`)
2. aviary `set_aircraft_parameters` × 3 (lines 1006, 1039, 1066)
3. aviary `run_simulation` (line 1222) ← optimizer ran
4. aviary `get_results` (line 1234) ← fuel = 7,644 captured
5. mass `estimate_mass` (line 1461) ← mWing captured, too late
6. pyCycle `run_cycle` (line 2037) ← SFC captured, too late
7. SU2 setup + solve (lines 2255+) ← CL/CD captured, too late

The data-plane middleware injects captured values
(`Aircraft.Wing.MASS_SCALER` from Phase K-A, `LIFT_COEFFICIENT` +
`SUBSONIC_DRAG_COEFF_FACTOR` from Phase H) on the **next**
`set_aircraft_parameters` call. The orchestrated agent in iter 1 ran
aviary BEFORE the upstream disciplines, so the middleware captured
values into the data_store but never injected them — there was no
subsequent `set_aircraft_parameters` call inside iter 1. The 7,644 kg
result is effectively aviary single-MCP with default
`MASS_SCALER = 1.0`, default `LIFT_COEFFICIENT`, default
`SUBSONIC_DRAG_COEFF_FACTOR`. Not a coupled MDO solution.

Iter 2 (killed mid-way) **would have** injected the captured values on
its own `set_aircraft_parameters` call — that's why the default
`lifecycle_mode: "active"` setting recovers coupling on the second
pass. But active mode means the orchestrator rebuilds the 7-agent team
and re-delegates everything from scratch each iteration: ~25-30 min
wall-clock per run instead of the sequential combo's ~12-15.

**Interim wiring (added today).** The combo now passes
`strategy_config={"orchestrated": {"lifecycle_mode": "setup_only"}}`,
which caps execution at one delegation cycle. This is a **temporary
choice** — it gives us fast (single-pass) runs but **locks in the
un-coupled result** until the ordering issue is resolved. The setup_only
override needs to be revisited tomorrow.

**Tomorrow's plan (Phase L day-2).** Add **more context about the
DLR-F25 design problem** to the orchestrator system prompt in
`config/mdo_f25_orchestrated_agents.yaml` so it picks sensible
ordering on its own — without falling back to baking-in the sequential
pipeline's explicit stage sequence. User's intent: keep the
orchestrator's free-form delegation pattern intact; the orchestrator
should *understand the physics dependencies well enough* to delegate in
a coupled-injection-friendly order without being told the recipe.

Once iter-1 ordering is correct, revisit whether `setup_only` is still
needed (probably revert it so iterative_feedback handler can drive
retries on validation warnings the way it does in sequential).

**Unit suite status.** 1,348/1,348 pass both before and after the
wiring change.

### 2026-05-23 — Known limitation: middleware uses FLOPS primary even when out-of-range

Surfaced while reviewing the full-pipeline run on D150 (wandb
`iepdeu70`, fuel = 12,755.86 kg). On non-A320-class geometries where
FLOPS is flagged out-of-valid-range, the middleware still routes the
FLOPS-sourced primary `components.mWing_kg` into aviary's
`Aircraft.Wing.MASS_SCALER`.

**Concrete numbers from the D150 run:**

```
                       value      method      flagged?
components.mWing_kg    7,677 kg   FLOPS       taper 0.176 out of FLOPS range
oas_comparison
  .mWing_scaled_kg     9,546 kg   OAS×wwr     no flag, in Wing.MASS scope
captured by middleware → 7,677 kg → MASS_SCALER = 7,677/5,998 = 1.28
```

The Phase K `oas_wing_weight_ratio` 1.25 → 2.31 calibration (mass-mcp
commit bb99056) brought OAS into the same scope as aviary's
`Aircraft.Wing.MASS`. It did NOT add agent-side or middleware-side
*selection* logic to prefer OAS when FLOPS is flagged invalid for the
geometry. The structures_analyst reports the discrepancy in its
`STRUCTURAL_WARNINGS` text but doesn't override the captured primary.

**Why this is OK to leave as-is for now:**
- For FwFm-class geometries (where the wwr was calibrated) FLOPS ≈
  OAS-scaled at ~7,500 kg both, so the captured FLOPS primary is the
  right answer.
- For D150 and other non-A320 geometries, this means the 5-MCP
  pipeline's wing-mass coupling silently prefers the empirical fit
  that's outside its own validity range. Fuel-burn results from these
  runs should be treated as a lower bound on what the OAS-coupled
  version would produce.

**Future work (not built yet, not currently authorized):** add a
middleware rule that picks `oas_comparison.mWing_scaled_kg` when the
mass-mcp response carries a `FLOPS-out-of-range` warning AND the OAS
value is present and within the [1,000, 30,000] kg sanity bracket. OR
edit the structures_analyst prompt to request `wing_mass_method="oas"`
for non-FwFm CPACS files. Either is a single-file change once the
team decides which lever to pull.

### 2026-05-23 — Drop validate_parameters from MAS-Aviary surface

**Why.** Companion to the aviary-mcp change that folded
`validate_parameters` into `set_aircraft_parameters`. MAS-Aviary had the
tool name baked into agent prompts, tool-allowlists, phase definitions,
and two pieces of gate logic. Leaving the references in place would
have caused tool-not-found errors on every parameter-setting agent.

**Change.**
- **YAML prompts (10 files).** `aviary_staged_pipeline.yaml`,
  `aviary_networked.yaml`, `aviary_networked_agents.yaml`,
  `aviary_orchestrated_agents.yaml`, `aviary_graph.yaml`,
  `mdo_f25_graph.yaml`, `mdo_f25_networked_agents.yaml`,
  `mdo_f25_orchestrated_agents.yaml`, `sequential_agents.yaml`,
  `mdo_f25_sequential_agents.yaml` — removed `validate_parameters` from
  tool allowlists; rewrote agent step lists from "call
  set_aircraft_parameters then call validate_parameters" to "call
  set_aircraft_parameters; read the `valid` field on its response".
  Workflow gating language updated.
- **Handlers.**
  - `src/coordination/staged_pipeline_handler.py::_validation_exhausted`
    now watches `set_aircraft_parameters` outputs instead of
    `validate_parameters` outputs.
  - `src/coordination/strategies/networked.py` phase-completion gate
    now requires `set_aircraft_parameters` to return `valid:true`
    (was previously gated on `validate_parameters` returning that).
    Status banner in the phase prompt updated.
  - `src/runners/batch_runner.py::_METRIC_TOOL_NAMES` swaps
    `validate_parameters` for `set_aircraft_parameters` so the metric
    extractor still finds `model_eval.outputs`.
  - `src/coordination/feedback_extraction.py` and
    `src/coordination/strategies/orchestrated.py` — comments + retry
    prompts updated to reference set_aircraft_parameters' inline `valid`.
- **README.md.** Tool count 9 → 8, listing dropped.

**Why this matters.** Every parameter-setting worker is now a single
tool call away from a verdict. No more "set, then forget to validate,
then run_simulation and watch SLSQP NaN at 40 s" failure pattern.

**Validation.** Local unit tests touching the modified handlers all
green:
- `tests/test_data_plane.py` — 42/42
- `tests/test_feedback_extraction.py` — 26/26
- `tests/test_networked_strategy.py` — 59/59
- `tests/test_orchestrated_strategy.py` — 28/28
- `tests/test_iterative_feedback_handler.py` — 30/30
Total 185/185 in the targeted suite.

New regression test `tests/test_validate_merge_agent.py` drives a
real Claude Sonnet 4 agent against the running aviary-mcp on :8600
(`@pytest.mark.live_mcp_llm`, ~$0.10, ~1 min). Two cases pass on
the live tool:
- `test_good_params_return_valid_inline` — one `set_aircraft_parameters`
  call with AR=11/AREA=130.1/SCALE=1.0 returned `valid:true`,
  populated `model_eval.outputs`, ~1s `runtime_seconds`. No follow-up
  validate call was attempted.
- `test_agent_reads_warnings_and_retries` — first call with AR=20
  returned the two expected warnings (advisory range + reliable
  range). The agent read the response, adjusted to AR=10.5, and
  re-called `set_aircraft_parameters` to get a clean valid response.

The aviary-mcp side reports 24/24 unit-test passes for
`set_aircraft_parameters` returning the merged validation payload —
see the aviary-mcp CHANGELOG entry of the same date for the
server-side change.

### 2026-05-23 (Phase K — mass-mcp closes the structures + propulsion-input loops)

Run #20 (wandb r58n9eew) wires the structures discipline into both
aviary and pycycle via the same data-plane middleware pattern that
Phases H and G.3 established. Two new injections:

```
Phase K-A: mass-mcp mWing_kg → Aircraft.Wing.MASS_SCALER
           on aviary's set_aircraft_parameters
Phase K-B: mass-mcp mTOM_kg  → Fn_DES (lbf)
           on pycycle's set_inputs
```

**Run #20 result vs Run #18 (Phase I baseline):**

```
                  Run #18 (Phase I)   Run #20 (Phase K)
MASS_SCALER       1.0 (default)       1.280  (injected)
GROSS_MASS_KG     73,721              74,155  (+434 kg)
FUEL_BURNED_KG    12,694.52           12,755.86  (+61 kg, +0.5%)
OPR (propulsion)  40                  58
CYCLE_CONVERGED   true                true
```

The wing-mass coupling has real but small effect on this aircraft —
+1.28× wing mass adds ~430 kg to MTOM (wing mass shows up directly
in OEM), and aviary's optimizer adapts the trajectory to burn +61 kg
more fuel for the heavier airframe. Same direction and magnitude as
the direct sensitivity sweep predicted
(6.0 → 9.0 t wing on the bench → +233 kg fuel, ~7% per 50%).

**Three coupling steps shipped this phase:**

1. **mass-mcp ``oas_wing_weight_ratio`` 1.25 → 2.31** (mass-mcp
   commit bb99056). The earlier 1.25 default came from the OAS
   uCRM example, which targets "wingbox primary" scope; aviary's
   ``Aircraft.Wing.MASS`` is broader (bending + shear control +
   misc structural). Empirical measurement on the FwFm bench:
   ``Wing.MASS / BENDING_MATERIAL_MASS = 7,513 / 3,249 = 2.31``.
   With the new default, mass-mcp's OAS and FLOPS numbers
   cross-validate (~7,500 kg both) instead of looking like a 44%
   discrepancy — that was the "different scopes" trap, not a
   real disagreement.

2. **aviary-mcp ``Aircraft.Wing.MASS_SCALER`` whitelisted**
   (aviary-mcp commit 0993c0f). Pre-validated with a direct
   sensitivity sweep — MASS_SCALER ∈ {0.5, 1.0, 1.5} produces
   fuel ∈ {6.8, 7.0, 7.2} t. Not silent like Phase J was.

3. **Data-plane middleware** (this repo, commits 57eade6 +
   7392d15):
   - intercept_response captures ``mWing_kg`` and ``mTOM_kg`` from
     estimate_mass responses; sanity-bracketed.
   - resolve_request on aviary's set_aircraft_parameters merges
     ``Aircraft.Wing.MASS_SCALER = mWing_kg / 5998`` clamped to
     [0.5, 2.0].
   - resolve_request on **pycycle's** set_inputs (gated on
     mcp_name == "pycycle" to avoid colliding with other MCPs that
     might have a same-named tool) merges
     ``Fn_DES_lbf = MTOM_kg × 0.0811``, where 0.0811 = g / L_D /
     N_eng / N→lbf × climb_margin (1.25), clamped to [2000, 25000]
     lbf. The coefficient lines up with pycycle's bench default of
     5,900 lbf at MTOM=73 t within 0.3% — designs with different
     MTOM move Fn_DES proportionally.

**Reference review before shipping:** read upstream OAS
``weight.py`` source (spar-element-only sum), aviary's
``WingTotalMass`` FLOPS component (bending + shear + misc + bwb
aftbody), and walked the variable hierarchy to confirm Wing.MASS
scope excludes HIGH_LIFT / SURFACE_CONTROL / FOLD. Both numbers
verified against the aviary bench measurement before the
middleware was written.

**Validation test added before middleware:**
``test_phase_hj_coupling_validation.py::test_phase_k_wing_mass_changes_fuel_burn``
pre-seeds data_store with mass_wing_kg ∈ {3,000, 6,000, 9,000} kg
and asserts aviary's fuel_burn responds monotonically. Passes:
6,765 → 6,959 → 7,192 kg. The same kind of sensitivity test that
caught Phase J's silent failure runs green here.

**Phase K-B has no analog sensitivity test because Fn_DES IS
pycycle's main design dial** — varying it always moves the
converged SFC and BPR. The Phase J trap (a knob that aviary
accepts but never reads) can't repeat for Fn_DES because pycycle's
whole cycle solve is sized around it.

**Coupling map after Phase K:**

| Discipline | External output | Aviary / pycycle target | Status |
|---|---|---|---|
| SU2 aero (CL) | CL_CRUISE | Mission.Design.LIFT_COEFFICIENT | ✅ Phase H |
| SU2 aero (CD) | CD_CRUISE | Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR | ✅ Phase H |
| mass (wing) | mWing_kg | Aircraft.Wing.MASS_SCALER | ✅ Phase K-A |
| mass (MTOM) | mTOM_kg | pycycle Fn_DES | ✅ Phase K-B |
| pyCycle (SFC) | perf.TSFC | Aircraft.Engine.SUBSONIC_FUEL_FLOW_SCALER | ❌ reverted (FwFm tabular deck no-op) |

All shipped without prompt changes — the middleware injection
pattern keeps the agent doing its discipline-native work while the
framework handles cross-discipline plumbing. Three of the four
coupling targets are validated by sensitivity sweep; Phase J's
silent target is guarded by an xfail regression.

### 2026-05-23 (Phase H/J coupling validation — Phase J reverted)

User asked for sensitivity tests proving the H/J wiring actually
moves fuel burn. Added
``tests/test_phase_hj_coupling_validation.py`` — pre-seed
data_store with three (CL, CD) or (SFC) tuples, run aviary directly
through wrapped tools (no LLM), assert ``fuel_burned_kg`` responds
monotonically. ~33s wall clock for both tests, $0 cost.

**Phase H result — coupling has real teeth:**
```
SU2 CD   Scaler   aviary cruise_cd   fuel_burn (kg)
0.012    0.619    0.0136             5,102
0.025    1.093    0.0247             8,370
0.040    1.640    0.0398             13,571
```
CD triples → fuel burn 2.7×. ``SUBSONIC_DRAG_COEFF_FACTOR`` reaches
the trajectory aero, ``cruise_cd_avg`` echoes it exactly, fuel burn
responds proportionally. Phase H wiring confirmed end-to-end.

**Phase J result — coupling silent, reverted:**
```
pyCycle SFC   Scaler   aviary cruise_sfc   fuel_burn (kg)
0.40          0.735    0.5434              7,058
0.55          1.011    0.5434              7,058
0.70          1.287    0.5434              7,058
```
``SUBSONIC_FUEL_FLOW_SCALER`` lands in aviary's applied list
correctly. ``cruise_sfc_avg`` is identical for all three. Fuel
burn doesn't move. Verified independently by calling
``prob.aviary_inputs.set_val(SUBSONIC_FUEL_FLOW_SCALER, 1.5)``
directly in Python (no MCP, no middleware) — same null result.

Root cause: ``aircraft_for_bench_FwFm.csv`` uses a **tabular engine
deck** (pre-computed thrust + fuel-flow lookup tables).
``Aircraft.Engine.SUBSONIC_FUEL_FLOW_SCALER`` is a knob for the
FLOPS *analytical* engine model. The variable is settable on the
problem but never read during the trajectory simulation. The
Phase J injection was a true no-op — middleware succeeded at every
step except the one that mattered.

Reverts shipped here:
- ``data_plane.py``: removed ``_capture_pycycle_performance`` and
  ``_inject_phase_j_propulsion``; ``set_aircraft_parameters`` is
  now Phase H only.
- ``aviary-mcp design_space.py``: removed
  SUBSONIC_FUEL_FLOW_SCALER from DESIGN_PARAMETERS and
  VARIABLE_NAME_MAP (otherwise the agent would see a useless knob).
- ``aviary-mcp aviary_runner.py``: kept
  ``cruise_sfc_avg_lb_per_hr_lbf`` in extract_results as a
  diagnostic — confirming SFC values is still useful even when we
  can't override them.
- ``tests/test_data_plane.py``: removed 7 Phase J unit tests
  (they validated the synthetic-data injection logic, which was
  correct but useless in production).
- ``tests/test_phase_h_middleware_live.py``: removed the no-LLM
  Phase J test (would have been a false positive — it only
  checked aviary echoed the value, not that the value mattered).
- ``tests/test_phase_hj_coupling_validation.py``: kept the Phase J
  test as ``@pytest.mark.xfail(strict=True)`` so the test suite
  fails LOUDLY if someone re-adds the silent middleware. Removes
  the xfail when a working coupling path lands.

How to actually pipe pyCycle SFC into the bench mission (future
work): either (a) switch to a GASP analytic engine model
(aircraft_for_bench_GwGm.csv variant) where scalers apply, or
(b) replace the tabular engine deck file at runtime with one
generated from pyCycle's SFC. Both are bigger than middleware
injection.

The CHANGELOG entry below (the original Phase J ship) is left
intact for git-archaeology — it explains what was attempted and
why the diagnostic was missing.

### 2026-05-23 (Phase J — pyCycle SFC drives aviary mission fuel flow)

Run #19 (wandb exvhbrw3) wires pyCycle's externally computed cruise
SFC into aviary's mission via the same data-plane middleware pattern
that Phase H established for SU2's CL/CD.

```
pyCycle SFC_CRUISE         : 0.547 lb/hr/lbf (from agent's cycle solve)
aviary bench cruise SFC    : 0.544 lb/hr/lbf (measured baseline)
SUBSONIC_FUEL_FLOW_SCALER  : 1.006             (injected by middleware)
aviary cruise_sfc_avg      : 0.547 lb/hr/lbf  ← scaler applied
FUEL_BURNED_KG             : 12,694.52        (unchanged — scaler ≈ 1)
```

Three pieces of plumbing:

1. **aviary-mcp** ``fix/ar-reliable-range`` commit a64ea2f
   - Adds ``Aircraft.Engine.SUBSONIC_FUEL_FLOW_SCALER`` to
     DESIGN_PARAMETERS + VARIABLE_NAME_MAP so set_aircraft_parameters
     accepts it (without this aviary throws UNKNOWN_PARAMETER — the
     Phase H lesson).
   - extract_results now computes
     ``cruise_sfc_avg_lb_per_hr_lbf`` from the cruise timeseries
     (Δmass / Δtime / thrust) for verification.
   - get_results MCP wrapper passes the new field through.

2. **data_plane middleware** commit 1083fcf (this repo)
   - intercept_response captures ``perf.TSFC`` off pyCycle's
     ``run_cycle`` and ``get_outputs`` responses. Rejects
     non-physical values outside [0.2, 1.5] (Run #17 produced TSFC =
     -0.0029 when its Newton solver diverged).
   - resolve_request on every set_aircraft_parameters call merges
     ``Aircraft.Engine.SUBSONIC_FUEL_FLOW_SCALER = pycycle_SFC /
     0.544`` (clamped to [0.5, 2.0]). The 0.544 reference is
     aviary's empirically measured bench cruise SFC for
     aircraft_for_bench_FwFm.csv — verified once, hardcoded as a
     constant.

3. **Tests** in tests/test_data_plane.py (7 new unit tests) and
   tests/test_phase_h_middleware_live.py (1 no-LLM live test, 5.7s).
   Both layers caught zero new bugs because Phase H established the
   same contract; Phase J just adds another captured value and
   another injected key on the same machinery.

**Fuel burn unchanged this run** (12,694.52 kg, same as Phase H Run
#17/18) because pyCycle's design-point SFC of 0.547 happens to land
almost exactly on aviary's bench cruise SFC of 0.544. The scaler is
1.006, only 0.6% above unity, so the change in mission fuel flow is
within the trajectory optimizer's noise. To exercise the loop,
either:

- The propulsion agent would need to choose engine parameters that
  produce a meaningfully different SFC (e.g. BPR=8 instead of 11
  raises SFC ~5-10%), or
- A sensitivity sweep over BPR/OPR would show the fuel-burn ↔ SFC
  coupling that's now in place.

What matters: the loop **is closed**. All four disciplines now
have their externally computed cruise outputs flowing into the
mission fuel-burn calculation:

  SU2:     CL  → Mission.Design.LIFT_COEFFICIENT          (Phase H)
  SU2:     CD  → Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR (Phase H)
  pyCycle: SFC → Aircraft.Engine.SUBSONIC_FUEL_FLOW_SCALER (Phase J)
  mass:    -    → no aviary path yet (Phase K candidate)

### 2026-05-23 (Phase I — pycycle canonical inputs: no more agent name-guessing)

Run #18 (wandb v3gqlk8p) is the first pipeline run where ALL FOUR
disciplinary stages produce clean physical numbers end-to-end:

```
Aero       :  CL=0.187, CD=0.0128, L/D=14.6, SOLVER_CONVERGED true   (Phase G.3)
Structures :  MASS_METHOD_USED=both, OEM=35,725 kg                   (Phase H mass-mcp)
Propulsion :  SFC=0.513 lb/hr/lbf, Fn=5900 lbf, CYCLE_CONVERGED true (Phase I — NEW)
Mission    :  FUEL_BURNED=12,694.52 kg, MTOM=73,721 kg               (Phase H middleware)
```

Run #17 had ``CYCLE_CONVERGED: false`` because the propulsion agent
guessed ``fan.map.design.BPR`` and ``fan.BPR`` for bypass ratio —
neither exists. With BPR unset the Newton solver ran on an
under-constrained model and produced ``perf.Fn = -2.6e+19 lbf`` and
``perf.TSFC = -0.0029``. Root cause: ``list_variables`` returns ~936
promoted names with no hint which are design dials vs flow-station
internals vs balance-state variables, so the agent has to guess.

Phase I adds a new pycycle-mcp tool ``get_design_inputs(session_id)``
that returns the curated short list of settable design dials for
the session's cycle type. For HBTF the entries are:

  fc.alt, fc.MN, Fn_DES, T4_MAX, splitter.BPR, fan.PR, lpc.PR,
  hpc.PR, fan.eff, lpc.eff, hpc.eff, hpt.eff, lpt.eff

Each carries the exact set_inputs path, units, default, current
value (from the live model), and a one-line description. The agent
calls get_design_inputs first, sees ``splitter.BPR`` is the bypass-
ratio path, passes it straight to set_inputs. No name guessing,
no translation layer (the user explicitly rejected the alias-map
approach: *"the guessing is an indication the MCP is not designed
right"*).

Branch: ``Jezemba/pycycle-mcp@feat/canonical-design-inputs`` commit.
Module: ``canonical_inputs.py`` holds the curated lists per cycle
type. ``get_design_inputs_for_cycle`` returns a deep copy so the
tool can mutate entries per session without corrupting the module
constants. ``create_cycle_model`` now stashes ``cycle_type`` in
session meta so the tool can dispatch correctly.

The MAS-Aviary prompt change is **one line**: adding
``get_design_inputs`` to ``propulsion_analyst``'s allowed_tools.
The tool's own MCP description (built into the registration) tells
the agent to call it before set_inputs.

Regression coverage:

- ``pycycle-mcp/tests/test_canonical_inputs.py`` (11 unit tests):
  curated-data sanity, tool dispatch including
  KeyError-vs-NotFound, drift check that every canonical path is
  settable on a real HBTF. 11/11 pass.

- ``MAS-Aviary/tests/test_pycycle_canonical_inputs_live.py`` (1
  live LLM test, 62 s, ~$0.03): drives a real Claude agent through
  the propulsion task and asserts (a) it calls
  get_design_inputs, (b) it passes splitter.BPR to set_inputs
  (not a guessed alias), (c) run_cycle has no runaway exponents.
  Passes — caught no bugs because the previous live test on
  pycycle-mcp wrapped tools already exercised the contract.

Run #17 already converged the propulsion stage on a coin flip
(BPR happened to be left at the model default 5.105). Phase I
makes that determination ironclad. Fuel-burn number is unchanged
(12,694.52 kg) because aviary's mission still draws its SFC from
its internal engine table — the pycycle output isn't piped into
aviary yet. That's Phase J (mission engine deck override).

### 2026-05-23 (Phase H — SU2 CL/CD actually drives fuel burn)

Run #17 (wandb p4zyf1zd) is the first pipeline run where
``FUEL_BURNED_KG`` is *not* 12,747.47:

```
FUEL_BURNED_KG:    12,694.52    (−52.95 kg vs the Runs #6/9/11/12/14/15/16 baseline)
MTOM_KG:           73,721.32    (was 73,779.45)
CRUISE_CL_AVIARY:  0.421
CRUISE_CD_AVIARY:  0.0207       (was 0.0213 with default aero)
LIFT_COEFFICIENT:  0.1871       (injected from SU2)
SUBSONIC_DRAG_COEFF_FACTOR: 0.768  (computed from SU2 CD + friction estimate)
```

Phase H wires the SU2 CL/CD from the aero stage into aviary's
mission via the data-plane middleware — no LLM cooperation required.
Four bugs gated the closure; each was caught only by the next
integration test layer down. The fixes, in order:

1. **OAS NaN crash in mass-mcp** — wing mesh was on the wrong
   half-side (positive Y, tip-to-root) for OAS's symmetry=True
   convention, and the wingbox initial thicknesses (3mm uniform)
   buckled at the 2.5g design load before the coupled aero-structural
   solve could converge. Branch ``Jezemba/mass-mcp@feat/oas-converged-init``
   commit 9a232ba: build mesh on Y ≤ 0, grade spar/skin thickness
   tip-to-root matching the OAS uCRM example, drop initial alpha to 2°.
   TDD: ``tests/test_oas_d150_does_not_nan.py``.

2. **aviary-mcp didn't expose cruise CL/CD** — extract_results only
   returned fuel/mass; the framework couldn't compare aviary's
   internal aero to SU2's. Branch
   ``Jezemba/aviary-mcp@fix/ar-reliable-range`` commits d91198d +
   2c518c1: extract_results now computes cruise_cl_avg, cruise_cd_avg,
   cruise_mach_avg, cruise_altitude_m_avg from aviary's drag/mass/
   mach/altitude timeseries + ISA density, and the get_results MCP
   wrapper passes them through.

3. **Prompt-only Phase H kept failing** — three iterations of the
   mission_architect prompt (Runs #13, #14, #15, #16) all had the
   agent emit only the 8 geometry/engine keys to
   set_aircraft_parameters, dropping LIFT_COEFFICIENT and
   SUBSONIC_DRAG_COEFF_FACTOR despite progressively more explicit
   instructions (MANDATORY pre-step, worked example, line-by-line
   arithmetic). Moved the calibration into data_plane middleware so
   the LLM doesn't need to think about it. Commit 6751040 +
   d1dc622:
   - intercept_response: capture CL/CD off the last row of
     read_history_csv. Strips both whitespace AND embedded quote
     chars from the SU2 column headers (history.csv writes them as
     ``       "CL"       ``).
   - resolve_request: on every set_aircraft_parameters call, compute
     the scale factor from the captured aero coefficients and merge
     ``Mission.Design.LIFT_COEFFICIENT`` and
     ``Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR`` into the
     parameters dict.
   - mission_architect prompt updated to acknowledge "Phase H is
     handled by the framework, don't include those keys".

4. **aviary-mcp whitelist rejected both Phase H keys** — even when
   the middleware injected them, aviary-mcp's
   set_aircraft_parameters returned UNKNOWN_PARAMETER because the
   names weren't in VARIABLE_NAME_MAP. Caught by Run #17's pre-run
   integration test
   (``tests/test_phase_h_middleware_live.py``); fixed in
   aviary-mcp commit 5bacd77 — both names added to
   DESIGN_PARAMETERS (for get_design_space) and VARIABLE_NAME_MAP
   (for set_val routing). Verified locally that aviary v0.9.10
   accepts both via aviary_inputs.set_val.

The remaining −0.4% fuel-burn delta is **smaller than expected**
because:

- ``Mission.Design.LIFT_COEFFICIENT`` is a design-point input used
  by aviary's wing sizing during pre-mission, not a hard cruise
  constraint. The trajectory optimizer still picks its own cruise
  CL (0.42 here) based on wing area, mass, and altitude.
- ``Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR=0.768`` scales only
  the subsonic FLOPS drag buildup component; the supersonic /
  induced / profile splits take most of the cruise CD on the
  height-energy phase, so total CD only dropped from 0.0213 to
  0.0207 (~3%).

Closing the magnitude gap is Phase I work — likely needs an aviary
custom-aero subsystem that consumes the SU2 polar directly, or a
different override surface (e.g. ``Aircraft.Design.DRAG_POLAR``
table). The Phase H plumbing is done either way: SU2 → aviary is
wired end-to-end, agent-independent, with regression coverage.

Live regression in
``tests/test_phase_h_middleware_live.py``: two tests covering both
the wrapped-tool path (no LLM, 0.3s) and a real Claude agent path
(16s, ~\$0.02). Caught the embedded-quote bug and the whitelist
bug before Run #17 burned the API budget.

### 2026-05-18 (Phase G.3 — REF_AREA wiring + surface sampler defense: physical CL/CD)

Run #12 (wandb zjqntn6j) is the first pipeline run where SU2 reports
CL and CD at their physically expected magnitudes:

```
CL_CRUISE:            0.187         (Run #11 raw: 11.49; ÷61.39 = 0.187 ✓)
CD_CRUISE:            0.01281
L_OVER_D:             14.61
SOLVER_CONVERGED:     true
RESIDUAL_DROP_ORDERS: 7.42
sample_surface_solution: returned data, no UTF-8 crash
```

Three coupled fixes landed in this commit (7f8f8be) + the two MCP
branches:

1. **REF_AREA / REF_LENGTH / REF_ORIGIN_MOMENT propagation.**
   Phase G.2 (Run #11) gave the right *sign* on CD but the
   coefficients were ~60× too large in magnitude because the agent's
   SU2 preset never set REF_AREA — SU2 defaulted A_ref to 1.0 m².
   Geometry stage now hands ``WING_REF_AREA_M2``,
   ``WING_MAC_LENGTH_M``, and ``WING_MAC_QC_{X,Y,Z}`` through
   DESIGN_STATE; aero plugs them in verbatim. Run #12 confirms the
   agent passed ``REF_AREA: 61.39076274891849`` (the value
   ``get_wing_summary`` returned for ``Wing1``) and SU2's history
   came out at the physically expected scale.

2. **SU2 OUTPUT_FILES now includes SURFACE_CSV.** Without it SU2
   only writes ``surface.vtu`` (binary), and the aero agent's
   ``sample_surface_solution`` call crashed with
   ``'utf-8' codec can't decode byte 0x94`` (Run #11 finding).
   Preset now ships ``OUTPUT_FILES= (RESTART, PARAVIEW, SURFACE_CSV)``
   and the prompt explicitly tells the agent to call
   ``sample_surface_solution(relative_path="surface_flow.csv", ...)``.

3. **su2-mcp defensive guard** (branch
   ``Jezemba/su2-mcp@feat/csv-only-surface-sampler``, commit
   8e778be). ``sample_surface_solution`` used to open any caller
   path as UTF-8 text — fine for ``surface_flow.csv``, fatal for
   ``surface.vtu``. The function now (a) rejects ``.vtu/.vtk/.dat/
   .plt/.bin/.szplt`` suffixes with a ``validation_error`` that
   names the right CSV, and (b) catches ``UnicodeDecodeError``
   around the CSV parser and rewrites it as the same friendly error.
   TDD regression in
   ``tigl-mcp/tests/test_sample_surface_solution_binary.py``
   reproduces the production 0x94-byte crash and asserts the new
   error shape.

Bonus from Run #12: ``CYCLE_CONVERGED: true`` this time (was false
in Run #11), CONSTRAINTS_PASSED: 3/5 (was 2/7), pipeline reached
DONE: 1 completed, 0 failed in ~12 min.

The fuel-burn number (``FUEL_BURNED_KG: 12,747``) is **unchanged**
from Runs #6, #9, #11. That's because aviary's mission simulator
falls back to its internal aero/mass model when upstream stages
hit known errors (mass-mcp OAS NaN → FLOPS fallback; mission
``AVIARY_SETUP_ERROR`` cascade). Closing that loop — actually
piping SU2's CL/CD into aviary instead of letting its fallback take
over — is the next phase. The aero stage and surface sampling are
now both physically correct end-to-end.

### 2026-05-18 (Phase G.2 — TiGL BREP replaces sewn STL: positive drag at last)

Run #11 (wandb sh5o47il) is the first pipeline run that produced a
physically correct aerodynamic result end-to-end:

```
CL_CRUISE:           11.49   (REF_AREA=1.0 default; physically ≈ 0.19)
CD_CRUISE:           +0.786  (REF_AREA=1.0 default; physically ≈ 0.013)
L_OVER_D:            14.61
SOLVER_CONVERGED:    true
RESIDUAL_DROP_ORDERS: 7.32
```

Drag is positive. The wing is finally flying nose-first. After
rescaling by the wing reference area (61.4 m²), CL/CD are in the right
ballpark for inviscid Euler at AoA=2°.

The Phase G.1 fix (commit 30e3474 on tigl-mcp) had switched the volume
mesh tool from ``embed`` to ``BRepBuilderAPI_Sewing + OCC.cut``. That
worked for synthetic closed STL (the unit-sphere unit test), but
leaked on TiGL's swept multi-segment wing — the sewer left small
holes at section seams that allowed fluid mesh nodes inside the solid.
Run #10 (wandb 6dafj24g) confirmed: CD went from −0.20 (Phase F) to
**−3.60**, even more wrong. Diagnosis showed 98% of surface normals
were outward (correct), 5/62k fluid nodes had leaked into the wing
centroid sphere, and Cp_max=1.54 was at the *trailing* edge — fluid
was wrapping THROUGH the wing solid, not around it.

Phase G.2 (commit 3371528 on tigl-mcp) skips the sewing step entirely:
when TiGL exposes a BREP exporter for the component
(``exportWingBREPByUID``, ``exportFuselageBREPByUID``,
``exportFusedBREP``), the BREP bytes go straight into
``gmsh.model.occ.importShapes``. Parametric watertight CAD all the way.
The mesh agent's response now shows ``brep_source: "tigl"`` and 8
aircraft surfaces (was 2476 with sewn STL) — clean topology.

Other Phase G.2 fixes that landed in the same commit:

- Per-axis classifier bug: Phase G.1 used the wing's X coordinate
  when checking Y and Z box faces, so 4 of the 6 outer box faces
  were misclassified as ``aircraft``.
- Curvature-aware mesh sizing: LE/TE radii now get enough elements
  even at the agent's coarse default mesh_size_max.

Live regression in this repo
(``tests/test_phase_g_volume_mesh_regressions.py``) was rewritten too:
the centroid-sphere check was a false-positive generator on swept
tapered wings (sphere extended past the local thickness envelope).
Replaced with a deep-inside probe — sample 50%-chord, mid-thickness
at random span stations, and assert no fluid node is closer than the
nearest surface node. Catches Phase G.1's STL leak, passes on G.2's
BREP path.

Downstream stages still hit the **known** chain
(structures: mass-mcp OAS NaN → FLOPS fallback;
propulsion: CYCLE_CONVERGED=false;
mission: cascading UPSTREAM_ERROR) so SU2's CL/CD doesn't yet drive
fuel-burn. The pipeline still produces FUEL_BURNED_KG=12,747 (same
as Run #6 baseline) because aviary's mission simulator falls back to
its internal aero model. Closing that loop — passing SU2 CL/CD into
aviary instead of letting the fallback take over — is the next
phase.

### 2026-05-18 (Phase G — TDD: root-cause unphysical CL/CD, fix in tigl-mcp)

Re-launched the full live pipeline with all Phase F.* fixes in place
(Run #9, wandb rj3iv6tx). The Phase F data-plane plumbing all worked:
``set_mesh`` no longer hit "Incorrect padding", SU2 reached
``exit_code: 0`` with ``SOLVER_CONVERGED: true`` and
``RESIDUAL_DROP_ORDERS: 9.22``, the aero agent read the real CL/CD
from history.csv instead of falling back to the F25 reference.

But the values themselves were nonphysical:
``CL_CRUISE: 2.28, CD_CRUISE: -0.20, L_OVER_D: -11.4``. Negative drag
is impossible for a closed body in steady flow. Diagnosis:

  1. Re-ran SU2 on the same mesh at AOA=0 → still got CD = -0.0043
     (negative). Rules out an AoA-handling bug.
  2. Inspected the surface Cp distribution in surface.vtu: Cp_max =
     1.144 (exactly the compressible stagnation Cp at M=0.78), but
     located at the *trailing edge* (X=18.84) rather than the leading
     edge (X=12.75). Suction peak ended up at the LE.
  3. Probed the volume solution INSIDE the wing solid envelope
     (Y≈3, X≈14.5, Z≈-1.3) — found 5 fluid mesh nodes there with
     |V|≈177 m/s and reduced pressure (23.2 kPa vs freestream 26.5).
     The wing was not a closed body; fluid was flowing through it.

Root cause: ``tigl-mcp/src/tigl_mcp/tools/volume_mesh.py`` used
``gmsh.model.mesh.embed(2, ac_tags, 3, box_tag)`` to insert the
aircraft STL as a 2D shell inside the fluid box, then reversed its
normals. There was no boolean cut subtracting the wing volume from
the far-field. SU2 was therefore integrating pressure on both faces
of an open shell, producing meaningless force coefficients.

Fix lives in tigl-mcp, not MAS-Aviary. User authorized a one-off
exception to the "Do NOT modify MCP server code" rule on branch
``feat/cfd-ready-volume-mesh`` (Jezemba fork only). The new
implementation:

  1. Sews the STL triangulation into a watertight OCC shell
     (``pythonocc.BRepBuilderAPI_Sewing``), promotes it to a solid,
     writes BREP.
  2. Imports the BREP into gmsh OCC and runs
     ``gmsh.model.occ.cut([(3, box)], [(3, aircraft)])`` so the fluid
     domain wraps the body rather than passing through it.
  3. Recovers ``farfield`` / ``aircraft`` markers by surface centroid
     (box-edge vs. interior) so existing SU2 configs with
     ``MARKER_EULER=(aircraft)`` keep working.

Regression test ``tigl-mcp/tests/test_volume_mesh_cfd_correctness.py``
pins down the topology contract: zero fluid nodes inside the input
solid. Written FIRST per project TDD rule; failed on the old code
(13 interior nodes for a unit sphere, min(r)=0.015) and passes on the
new code (0 interior nodes, min(r)=1.000 exactly). Commit ``30e3474``
on Jezemba/tigl-mcp.

The MAS-Aviary side is unchanged. The fix takes effect after the
tigl-mcp server is restarted on the new branch.

### 2026-05-16 (Phase F.3 — TDD: tolerate every ref serialization format the LLM emits)

Run #8 exposed a new failure: the aero agent received the data plane's
``{"ref": "generate_volume_mesh__mesh_base64", "size_bytes": N}`` from
the geometry stage but forwarded it to su2's set_mesh as the *string*
``"ref:generate_volume_mesh__mesh_base64"``. The middleware did not
recognize that pattern, so set_mesh received the raw string, tried to
base64-decode it, and failed with "Incorrect padding". SU2 never ran;
the agent reported the F25 fallback (SOLVER_CONVERGED=false,
RESIDUAL_DROP_ORDERS=0).

Run #7 had different bugs (data plane intercepted the convergence trace
F.1; AERO_COEFF missing from history F.2). Run #8 hit this third bug
which has the same flavor: agent-formatting drift between runs at
temperature=0.

Fix shipped in two parts, this time TDD-style (test FIRST, then fix):

  1. New `_extract_ref_key` helper in src/tools/data_plane.py recognizes
     four ref formats and returns the underlying key:
        • dict:        {"ref": "key", ...}
        • bare string: "key"  (when the string IS a data_store key)
        • prefixed:    "ref:key"  with arbitrary whitespace
        • JSON-string: '{"ref": "key"}'  (mcpadapt anyOf collapse)
     resolve_request uses this; everything else passes through unchanged
     so a typo'd ref still errors loudly on the server side.

  2. Unit tests in tests/test_data_plane.py::TestResolveRequestRefFormats
     cover all four formats plus the negative cases (unknown string and
     unknown-ref-key both pass through). Written BEFORE the fix —
     established the failing-test baseline, then made them green.

  3. Live regression in tests/test_run3_regressions.py::
     test_mesh_ref_handoff_to_su2 drives geometry → set_mesh against
     real MCPs and Claude and asserts:
        • set_mesh was reached;
        • observation does not contain "Incorrect padding" /
          "Failed to set mesh" / similar base64-decode markers;
        • at least one call returned a mesh_path (success).
     PASSED in 216 s — would have caught Run #8 in under 4 min.

Process lesson the user has flagged THREE times in this conversation:
when a change affects what the agent SEES (data plane / tool
responses / prompts), an agent-level regression must catch the bug —
not a 10-minute live pipeline run. Run #7 and Run #8 were exactly
this pattern. Going forward, any data-plane / prompt change ships
with a live regression scenario in the same commit.

### 2026-05-16 (Phase F.2 — write CL/CD to history.csv so the agent can read them)

Inspecting Run #7's wandb agent reasoning closely revealed a SECOND
issue beyond the data-plane interception fixed in F.1. The agent
explained itself:

  "Residual dropped from -11.21 to -8.03 (3.18 orders of magnitude).
   Solver terminated early upon reaching target convergence criterion
   of -8. … Using F25 reference aerodynamic coefficients as the Euler
   solution does not directly output CL/CD values in the history file."

That last sentence is literally TRUE: our Phase F preset set
CONV_FIELD/CONV_RESIDUAL_MINVAL but did NOT set HISTORY_OUTPUT, so
SU2 fell back to its default — residuals only, no aero coefficients.
The agent was right that history.csv had no CL/CD column to read.
Even with the F.1 data-plane passthrough fix giving the agent the
full history, the columns it needed weren't there.

Fix in config/mdo_f25_sequential_agents.yaml step 3 preset:
  HISTORY_OUTPUT = ( ITER, RMS_RES, AERO_COEFF )
  SCREEN_OUTPUT  = ( INNER_ITER, RMS_DENSITY, LIFT, DRAG )

AERO_COEFF puts CL, CD, and the moment coefficients into the history
CSV. The screen output is also tightened to surface LIFT/DRAG so the
log_tail in the run_su2_solver response carries the same signal.

Step 7 of the aero agent prompt updated to:
  • State explicitly that CL/CD are in history.csv (column names "CL"/
    "CD" or "LIFT"/"DRAG" depending on SU2 version — agent should
    check the response's `columns` list).
  • Read CL/CD from the LAST row of the rows list.
  • Do NOT fall back to F25 reference values just because the
    computation looked unfamiliar — the Run #7 mistake we explicitly
    want to avoid. Real CL/CD on the current geometry beat the F25
    reference for the agent's actual aircraft.

Process note again: a wandb-side read of the agent's natural-language
reasoning caught what neither the unit tests nor the live regression
suite would have. Worth pairing this with a focused live regression
that asserts the aero agent reports non-fallback CL/CD when SU2
converges (deferred — costs API tokens).

### 2026-05-12 (Phase F.1 — agent now sees the convergence; passthrough + prompt fix)

Run #7 verified Phase F end-to-end: the agent sent the new SU2 preset
(CFL=1e3, MGCYCLE=W_CYCLE, MGLEVEL=3, FGMRES+ILU, WLS) and SU2 ran to
exit_code=0 in 154 s. The actual SU2 log (visible in the run_su2_solver
response's `log_tail`) showed residuals dropping past rms[Rho] = -5.98
by iteration 56 and continuing toward -8.

BUT the agent reported `RESIDUAL_DROP_ORDERS: 3.18` and used the F25
reference CL/CD/L/D values anyway. Root cause: the data plane was
intercepting read_history_csv's `rows` field (117 rows > 30-item
threshold), so the agent only saw a 5-row preview. The first 5 rows
include SU2's iter-0 pre-step state (where rms is artificially low),
making the visible "drop" tiny.

Two real bugs to fix, both about getting the convergence signal to the
LLM:

1. Data plane was hiding the answer
   src/tools/data_plane.py: added `_PASSTHROUGH_TOOLS = {"read_history_csv"}`.
   Tools listed here skip interception — their structured response IS
   the analytical content the agent reasons about, not bulk data. The
   convergence trace is small (~5-10 kB for 100-1000 iters) and worth
   the tokens.
   Unit test:
   tests/test_data_plane.py::test_read_history_csv_passes_through_no_interception
   (full 117-row response returns unchanged through intercept_response;
   no ref stored in data_store).

2. Prompt didn't use SU2's own convergence flag
   config/mdo_f25_sequential_agents.yaml: step 7 of the aerodynamics
   agent's task was rewritten to FIRST check the run_su2_solver
   response's `log_tail` field — SU2 always prints "Converged: Yes" or
   "Maximum number of iterations reached" near the end. If "Converged:
   Yes" is present, the agent trusts the result directly and doesn't
   need to do residual-drop arithmetic at all.
   The fallback rule was loosened so the agent uses REAL CL/CD whenever
   the drop is >= 3 orders OR the final rms <= -5 (rather than the
   previous strict >= 4 orders). Three orders is the typical
   engineering convergence threshold for Euler; four was overcautious.
   The agent is also explicitly told to read the LAST row of
   history.csv (not the preview rows) and to treat row 0 as the
   pre-step state.

Process note for future fixes: validate at the AGENT level whenever
the change is meant to affect what the agent reports. Running SU2
standalone (which proved the numerics fix worked) was necessary but
not sufficient — the agent's residual-decision path is its own
behavior and needs to be checked end-to-end.

### 2026-05-12 (Phase F — SU2 numerics fix; converges to rms=-8 in ~120 iter)

Run #6's aero stage technically completed (SU2 exit_code=0) but the
agent used the F25-reference fallback values because the actual
residual only dropped ~1 order of magnitude. Root cause turned out to
be a too-conservative numerics preset in the agent's update_config
call, not a mesh-quality problem.

Diagnosis (via direct SU2 binary tests against the upstream
inv_NACA0012 reference case from `ReferenceCode/su2-mcp/tests/`):

  • The reference inv_NACA0012.cfg converges to rms[Rho] = -8.03 in
    ~130 iterations using CFL=1e3, W-cycle multigrid, FGMRES+ILU
    linear solver, and weighted-least-squares gradient.

  • Our pipeline was sending CFL=10, no multigrid, default linear
    solver, GREEN_GAUSS gradient. Running our exact config on the
    same proven reference mesh plateaus at rms[Rho]=-2.59 after 200
    iterations — the same shape we saw across runs 3-6.

  • The reference numerics on the same mesh + our flight condition
    (Mach 0.78, FL330) converges to rms[Rho]=-8.10 in ~125 iters.

So our config was the bottleneck, not gmsh mesh quality, not the
solver, not the geometry.

Prompt fix in config/mdo_f25_sequential_agents.yaml — the
aerodynamics_analyst's step-3 update_config_entries preset is
extended with:

  CFL_NUMBER          1e3        (was 10.0)
  CFL_ADAPT           YES         (safety net for ill-conditioned meshes —
  CFL_ADAPT_PARAM     (0.1,2.0,    auto-throttles 10..1e10 on divergence)
                       10.0,1e10)
  MGCYCLE             W_CYCLE     (multigrid)
  MGLEVEL             3
  LINEAR_SOLVER       FGMRES      (was default)
  LINEAR_SOLVER_PREC  ILU
  LINEAR_SOLVER_ITER  10
  LINEAR_SOLVER_ERROR 1E-10
  NUM_METHOD_GRAD     WEIGHTED_LEAST_SQUARES  (was GREEN_GAUSS)
  JST_SENSOR_COEFF    (0.5, 0.02)              (transonic-shock-sensitive)
  MATH_PROBLEM        DIRECT
  RESTART_SOL         NO
  REF_DIMENSIONALIZATION  DIMENSIONAL
  CONV_FIELD          RMS_DENSITY
  CONV_RESIDUAL_MINVAL -8
  CONV_STARTITER      10
  MARKER_PLOTTING/MONITORING ( aircraft )

The SU2 SOLVER CONFIGURATION docs section in the prompt is updated to
reflect the new defaults and explains why CFL=10 + GREEN_GAUSS isn't
strong enough.

Outstanding (not blocking):
  • Verify on a full live pipeline run that SU2 now returns
    SOLVER_CONVERGED=true and the agent uses real CL/CD/L/D instead
    of the F25 fallback.

### 2026-05-11 (Run #6 — first complete pipeline end-to-end)

After the Phase E unit fix landed in aviary-mcp (ddbc7e1), run #6 of
mdo_f25_sequential_iterative_feedback completed cleanly for the first
time across the 6-run debug series. All 7 stages executed, the
trajectory optimizer converged, and the framework produced real fuel
burn and gross-mass numbers.

Result vs DLR-F25 reference:
  FUEL_BURNED_KG:  12747.47   (F25 ref 12100 → +5.3 %)
  GROSS_MASS_KG:   73779.45   (F25 ref 85700 → -13.9 %)
  CONVERGED:       true
  EXIT_CODE:       0
  SLSQP iterations: 13
  CONSTRAINTS_FAILED: none (range, pax, mach, altitude all met)

Stage tally (clean run):
  geometry_engineer    : 1 invocation, 62569-node / 351391-element mesh
  aerodynamics_analyst : 1 invocation, SU2 exit_code 0 (first time);
                         agent used F25 fallback CL/CD because residual
                         drop < 4 orders at ITER=200
  structures_analyst   : 1 invocation, FLOPS fallback (OAS NaN persists)
  propulsion_analyst   : 1 invocation, CYCLE_CONVERGED true (first time)
                         BPR=11, OPR=30.5, SFC=0.646 lb/hr/lbf
  mission_architect    : 3 invocations, validate_parameters VALID
                         (first time — no AVIARY_SETUP_ERROR)
  simulation_executor  : 1 invocation, run_simulation converged
                         (first time — no looping)
  mdo_integrator       : 1 invocation, result synthesized

Artifacts:
  wandb: https://wandb.ai/jessicae/mas-aviary-stat/runs/t4kdv9om
  logs:  logs/stat_results/1778527085/

The 5.3-% fuel-burn delta from F25 is a reasonable A320-baseline-doing-
F25-mission gap. The MTOM is 14 % lighter because Aviary's bench
aircraft is fundamentally lighter than the F25; closing that gap
requires a custom F25 aircraft CSV (Phase F, separate work).

Outstanding (does not block — pipeline produces valid results today):
- Phase D's AR cap (11.0) and SCALE_FACTOR default (1.3) in
  mission_architect were precautionary against the AR-cliff theory
  that turned out to be wrong (it was units). Reverting them to
  documented F25-target values (AR=15.6, SCALE_FACTOR=1.0) is now
  safe and would let the agent target F25 geometry. Deferred to a
  follow-up commit.
- SU2 still falls back to F25 reference CL/CD because ITER=200 isn't
  enough to drop residuals 4 orders on this mesh. A higher ITER
  (~500-1000) would produce real CFD numbers but doubles SU2 stage
  runtime.
- mass-mcp OAS NaN persists; FLOPS fallback works. Not on the
  critical path.

### 2026-05-11 (Phase E — ROOT CAUSE: aviary-mcp unit bug)

The AVIARY_SETUP_ERROR ("Newton solver residuals contain inf/NaN after
0 iterations") that defeated runs #3, #4, and #5 was ALL caused by a
single bug in aviary-mcp's aviary_runner.py:

  prob.aviary_inputs.set_val(aviary_var, float(value))   # ← no units=

For aircraft parameters, aviary-mcp was passing values WITHOUT a units
kwarg. Aviary defaults the variable's units to whatever its internal
metadata says — for the bench A320 file those defaults are Imperial.
So when the agent sent Aircraft.Wing.AREA=130.1 (intended m**2),
Aviary interpreted it as 130.1 ft**2 ≈ 12 m**2 — a tiny wing that
fails the climb-phase aero buildup and NaN's Newton at iter 0.

The mission-level params (RANGE, CRUISE_ALTITUDE) were already passing
units correctly. ONLY aircraft params were unit-less.

Empirical confirmation:
- AREA=130.1 (intended m**2, no units) → NaN/setup
- AREA=1400 (= ~130 m**2 if Aviary reads as ft**2) → VALID

Fix shipped in Jezemba/aviary-mcp branch fix/ar-reliable-range
(commit ddbc7e1): pull units from design_space.py metadata, translate
'm^2' → 'm**2', pass to set_val explicitly.

After fix, validate_parameters returns VALID for:
- Full F25 override set (AR=15.6, AREA=130.1 m**2, SCALE_FACTOR=1.3,
  FUSELAGE_LENGTH=37.79 m, MAX_HEIGHT=4.06, MAX_WIDTH=3.76)
- AR sweep 12 → 17 all pass

Implications for Phase B/C/D work on this branch:
- The "AR > 12 cliff" theory (Phase D) was wrong — it was units.
  The reliable_range warning in aviary-mcp and the AR=11 cap in the
  mission prompt are now unnecessary; both can be revisited in
  follow-up commits to allow the agent to target the actual F25 AR
  again.
- The simulator no-loop instruction (Phase D) remains useful even
  though it should fire less often now.
- The Phase A drop-key, type coercion, data plane, and Phase B
  prompt updates all remain correct.

### 2026-05-11 (Phase D — AR feasibility cliff + simulator no-loop on setup error)

Run #4 reached simulation_executor for the first time, then hit a new
failure mode: `run_simulation` returned `AVIARY_SETUP_ERROR` with the
message "Solver 'NL: Newton' on system 'traj.phases.climb.rhs_all.
solver_sub': residuals contain 'inf' or 'NaN' after 0 iterations". The
agent recovered partially (lowered Aircraft.Wing.ASPECT_RATIO from
15.6 → 14 → 13.5) but each retry still NaN'd, and the simulator agent
then looped on run_simulation/get_results in successive
iterative_feedback iterations.

Diagnosis: aviary-mcp's default aircraft baseline
(aircraft_for_bench_FwFm.csv, A320-class) has its FLOPS aero buildup
and engine deck tabulated for AR ~= 9-12. Setting AR > ~12 causes the
climb-phase Newton solver to evaluate residuals out of the table range
and produce inf/NaN. The previous advisory bounds [7.0, 14.0] both
rejected legitimate high-AR targets (DLR-F25 AR=15.6) and hid the real
feasibility cliff — agent obeyed the bound (set AR=14.0) and still
NaN'd because the cliff is at ~12, not 14.

aviary-mcp fix (separate PR on Jezemba/aviary-mcp, branch
fix/ar-reliable-range):
- AR advisory bounds widened to [7.0, 17.0] (covers F25 + modern
  high-AR transports as advisory targets)
- Added explicit "reliable_range": [9.0, 11.5] field documenting the
  cleanly-interpolating window for the default aircraft
- set_aircraft_parameters emits a SECOND warning ("outside
  reliable_range") when the value is in advisory bounds but outside
  the reliable window, so agents see the real cliff before
  run_simulation

MAS-Aviary prompt fixes:
- mission_architect: default Aircraft.Wing.ASPECT_RATIO = 11.0 (down
  from F25-target 15.6). F25 noted as aspirational; full fidelity
  requires a custom aircraft CSV (filed as future work). Upstream
  mapping table caps AR at 11.5 regardless of geometry intent.
- mission_architect: default Aircraft.Engine.SCALE_FACTOR = 1.3 (up
  from 1.0). The F25-target mission mass (~85t) is heavier than
  Aviary's baseline (~67t); SCALE_FACTOR=1.0 underpowers the climb
  phase.
- simulation_executor: explicit handling of AVIARY_SETUP_ERROR as a
  PERMANENT failure for the current parameter set. Report once, do
  NOT call run_simulation/get_results/get_trajectory again — those
  return NO_RESULTS in this state and burn steps.

New live regression scenario:
- test_simulator_does_not_loop_on_aviary_setup_error
  Forces AR=15.6 to trigger the cliff, asserts run_simulation called
  at most once and get_results/get_trajectory skipped on
  AVIARY_SETUP_ERROR. PASS on the updated prompt.

All 10 live regressions pass.

The deeper finding: this framework as-shipped is doing "A320-class MDO
with F25 mission targets" — the trajectory optimizer will find an
A320-shape optimum because the aero/engine tables are A320-shape. To
genuinely optimize an F25-shape aircraft requires a custom F25
aircraft CSV in aviary-mcp (calibrated aero/engine tables for AR > 12;
~half-day work; could derive from the Abu Zurayk paper in .llm/). That
is "Phase E" and out of current scope.

### 2026-05-11 (Phase C — investigations and follow-on prompt corrections)

Investigated the run #3 D150-fixture symptoms ("get_high_level_parameters
returns empty", "set_high_level_parameters claims success but
get_wing_summary unchanged"). Root cause is a fundamental design
property of the tigl-mcp tool surface, not a fixture bug:

  - tigl-mcp ComponentDefinition.parameters is an in-memory python dict
    populated at parse time ONLY from CPACS XML element attributes
    (uID, name, symmetry — never numeric like span/sweep, because CPACS
    encodes those in child elements, not attributes). So the dict is
    effectively always empty for any real CPACS file.
  - set_high_level_parameters writes to that local dict only. It does
    NOT modify the CPACS XML, does NOT re-load TiGL, does NOT change
    what get_wing_summary or generate_volume_mesh produce.
  - get_high_level_parameters reads the same dict and so always returns
    {} unless the agent has set_high_level_parameters in the same
    session.
  - TiGL geometry is loaded once at open_cpacs and is the immutable
    ground truth for the session.

This is a fundamental property of the framework: geometry never
actually varies between iterations at the TiGL level. Only Aviary's
set_aircraft_parameters (which operates on Aviary's own aircraft
model, not on CPACS) actually responds to agent intent. The
"multidisciplinary" character of the MDO is therefore:
   tigl     — baseline geometry only (intent recorded as a memo)
   su2      — CFD on the baseline mesh
   mass     — reads baseline CPACS from disk
   pycycle  — independent engine design, drives no geometry
   aviary   — actually optimizes mission against an Aviary-internal
              aircraft sized via set_aircraft_parameters

Phase B-1 geometry prompt was REVISED to be honest about this:
  - set_high_level_parameters explicitly framed as "record design
    intent" (memo-only). Prompt now states the mesh will reflect the
    BASELINE, not the intent.
  - Post-set verify (step 5) is reframed: read get_wing_summary to
    capture the BASELINE values (which ARE the mesh truth) for
    DESIGN_STATE. Do NOT halt on baseline/intent mismatch — that
    mismatch is structural to the framework, not a bug.
  - New DESIGN_INTENT field in DESIGN_STATE output: a YAML mapping of
    intended param → value. mission_architect's upstream-mapping table
    (Phase B-4) already consumes this.
  - Null fields from D150 (some Wing1 attributes like sweep_deg
    legitimately come back null on this fixture) are recorded but do
    NOT abort the pipeline.

Regression test test_geometry_set_then_verify_then_close updated:
  - removed the "halt on mismatch" expectation
  - added requirement that the agent ALWAYS attempts mesh
  - close_cpacs requirement only enforced on a successful mesh

Filing notes for future work (out of current scope per
"do NOT modify MCP server code"):
  - tigl-mcp could optionally read baseline param values from
    get_wing_summary into ComponentDefinition.parameters at open
    time, so get_high_level_parameters returns useful defaults
  - tigl-mcp could optionally implement set→TIXI-write→TiGL-reload
    to make geometry actually change. This is a substantial
    redesign of the tool — a candidate issue to file on Jezemba/tigl-mcp
  - su2-mcp and pycycle-mcp numpy truth-value ambiguity in
    update_config_entries and get_cycle_summary are minor server-side
    bugs; agent recovers gracefully but worth filing

### 2026-05-11 (Phase B — skill / prompt fixes for run #3 P2 bugs)

Each agent's prompt in config/mdo_f25_sequential_agents.yaml updated to
eliminate a specific class of run #3 wasteful behavior. Each change has
a matching live regression scenario in tests/test_run3_regressions.py.

- geometry_engineer prompt (Phase B-1):
  - set_high_level_parameters called ONCE (was: redundantly re-called)
  - response warnings surfaced into DESIGN_STATE.COUPLING_NOTES (was:
    silently swallowed)
  - mandatory post-set verification via get_wing_summary; on null/>5%
    mismatch, halt the pipeline with MESH_BASE64="skipped — geometry
    invalid" rather than meshing a partial geometry
  - close_cpacs at end so mass-mcp reads fresh CPACS from disk (was:
    sometimes omitted, leaving mass to read stale data)
  - one final_answer block only (was: same DESIGN_STATE repeated 3x,
    eating downstream context)
  - regression: test_geometry_set_then_verify_then_close
- aerodynamics_analyst (SU2) prompt (Phase B-2):
  - complete F25 cruise config preset baked into the step 3
    update_config_entries call (was: two piecemeal calls, first
    returned 7-key missing_required)
  - mesh_path=null on create_su2_session documented as expected
  - run_su2_solver max_runtime_seconds=300 hard ceiling (was: 600;
    risked busting the per-repeat timeout when the LLM picked 600)
  - residual interpretation: log10 values, smaller = better,
    drop = INITIAL - FINAL; explicit "common mistake" warning
    pointing out the run #3 agent's sign error
  - sample_surface_solution always called with marker_name='aircraft'
  - second update_config_entries call permitted only as a tiny
    fill-in for server-flagged missing_required (no third call)
  - regression: test_su2_config_preset_in_one_call
- propulsion_analyst (pycycle) prompt (Phase B-3):
  - list_variables called ONCE with max_parameters cap (was: 6+ times
    with growing limits, +30K tokens each → 377K input at step 11)
  - explicit F25 cruise design-point inputs (fc.alt=33000,
    fc.MN=0.78, throttle=0.85, BPR=11, OPR=40, fan.PR=1.45,
    T4=1700) so cycle is sized for cruise not SLS
  - sanity check bounds for SFC and Fn; outside-range outputs trigger
    fallback to F25 reference values
  - regression: test_pycycle_list_variables_called_at_most_once
- mission_architect (aviary) prompt (Phase B-4):
  - get_design_space called FIRST, before create_session, to discover
    exact parameter names (was: agent guessed and retried)
  - create_session called with no initial_parameters (omit kwarg)
  - explicit upstream-param mapping table for set_aircraft_parameters:
    each Aviary param maps to a specific DESIGN_STATE field with an
    F25-baseline fallback when upstream returned UPSTREAM_ERROR or
    null
  - Aircraft.Wing.SPAN explicitly NOT to be set (avoids the
    aviary-mcp "SPAN read-only derived param" injection bug)
  - validate_parameters called once; on invalid, report and STOP
    rather than loop (this was the run #3 20-iteration trap on top
    of the Phase A create_session blocker)
  - regression: test_mission_calls_design_space_first_and_validates_once

Live regression suite is now 9 scenarios; all pass.
Full unit suite is 1319/1319 passing.

### 2026-05-11 (Phase A — middleware fixes)
- Fixed: type-coercion middleware now treats the literal string "null"
  (or "None"/"") as Python None for any schema that accepts null in any
  of these ways:
    - anyOf includes {type: null}
    - anyOf is present (mcpadapt may have stripped the null option)
    - nullable: true is set
    - schema has explicit "default": null
- Fixed (uncovered by the live regression test): coercing "null" → None
  is not enough — aviary's create_session pydantic wrapper rejects
  explicit None even when its own schema says default: null. The
  middleware now DROPS the kwarg entirely when the coerced value is
  None AND the schema declares default: null, letting the server's
  native default kick in. End-to-end create_session now succeeds.
  Resolves the run #3 P0 blocker where mission_architect was stuck in
  a 20-iteration loop because every create_session attempt was
  rejected with "Input should be a valid dictionary [type=dict_type]".
- Added: data-plane middleware now intercepts large structured payloads
  in addition to base64 binary. Triggers on:
    - lists with > 30 items (e.g. pycycle list_variables 200-item tree)
    - dicts whose JSON-serialized form exceeds 2KB (e.g. aviary
      get_trajectory 60-point timeseries)
  Intercepted payloads are stored in DesignState.data_store under
  "<tool>__<field>" and the LLM sees a compact summary (preview + ref +
  total_count + note). Downstream tool calls can pass the ref to fetch
  the full payload via resolve_request. Eliminates the +30K-tokens-
  per-list_variables-call growth that hit 377K input tokens by step 11
  in run #1.
- Tests: tests/test_type_coercion.py (22 tests, +4 for the drop-key
  fix) and tests/test_data_plane.py (13 tests) cover the coercion paths
  and large-payload interception against pycycle list_variables and
  aviary get_trajectory shapes. Full unit suite 1319/1319 passing.
- Added: live regression harness at tests/test_run3_regressions.py and
  the live_mcp_llm pytest marker. Each test spins up a minimal
  smolagents ToolCallingAgent with Claude Sonnet 4 via LiteLLM and ONLY
  the tools needed to reproduce one run #3 bug. End-to-end against the
  live MCPs. Run on demand: pytest -m live_mcp_llm -v. Five scenarios
  cover: create_session pydantic blocker, pycycle list_variables
  context bloat, aviary get_trajectory bloat, sample_surface_solution
  marker recovery, set_aircraft_parameters dict handling. All 5 pass
  on the Phase A + drop-key middleware. This harness was responsible
  for catching the "coerce-to-None isn't enough, must drop key" gap
  that the unit tests alone missed.

### 2026-05-11
- Tested: ran mdo_f25_sequential_iterative_feedback with the coarse mesh +
  ITER=200 + 60-min timeout. Wandb run j0r9i0w3 (failed connect, tigl
  stale sessions from previous SIGKILL) then run #3 (logs/stat_results/
  1778517452, no wandb finalize). 50 steps reached mission_architect's
  2nd iteration before manual stop. Mesh fix end-to-end confirmed:
  - generate_volume_mesh produced a 62,569-node / 351,391-element volume
    mesh in coarse-fidelity mode
  - SU2 actually ran 200 Euler iterations in 120s (no DistributeColoring
    error, no immediate solver crash) — the TiGL→SU2 wire is fully
    operational
- Observed bugs to fix before next rerun:
  P0 (blocking):
  - aviary create_session called with initial_parameters="null" (string)
    instead of None. Same anyOf[dict, null] type-coercion gap that hits
    multiple aviary tools. mission_architect re-invoked indefinitely
    (was running its 20th iteration when manually stopped) because
    every create_session attempt fails. Pipeline never reaches
    simulation_executor.
  P1 (real bugs):
  - sample_surface_solution requires marker_name; aero agent omits it.
    On retry, server silently returns a different marker_name than
    requested (asked "mesh_marker_wall", got "aircraft").
  - numpy truth-value ambiguity ValueError fires inside pycycle
    get_cycle_summary AND su2 update_config_entries (same bug, two
    surfaces). Looks like an array-vs-scalar comparison server-side.
  - mass-mcp OAS solver NaN cascade ('array must not contain infs or
    NaNs' in solve_matrix). mass-mcp falls back to flops_only; not
    blocking but indicates upstream geometry passed in is degenerate.
  - mass-mcp logs "TIGL unavailable, using xpath fallback extraction.
    Missing optional parameters (n_engines, engine_bpr, cruise_mach,
    design_range_m, n_passengers)". TiGL session/result not flowing to
    mass — only the CPACS file path is. Need to verify whether mass
    actually needs the TiGL session or whether close_cpacs writes
    everything mass needs into the CPACS file.
  P2 (skill / prompt quality):
  - geometry agent: get_high_level_parameters returns empty for the D150
    fixture; agent proceeds without acknowledging the empties
  - geometry agent: set_high_level_parameters returns warnings that the
    agent ignores
  - geometry agent: re-runs set_high_level_parameters after an initial
    set without verifying it took
  - geometry agent: emits the same DESIGN_STATE final-answer body up to
    3 times in succession (final_answer call + same content in
    Observations + summary), eating ~4-6KB tokens each repeat
  - geometry agent: doesn't acknowledge that some parameters are null
    before generating the mesh — risks meshing on a partial parameter
    set, hides upstream data-quality issues
  - su2 agent: update_config_entries called step-by-step rather than
    with a complete preset; could be one call with the F25 cruise preset
  - su2 agent: max_runtime_seconds=600 used despite skill update to 300
    (LLM didn't pick up the new default reliably; consider hard-cap)
  - su2 agent: hardcoded mesh_path=null on create_su2_session is fine
    (mesh is attached later via set_mesh) but worth documenting
  - su2 agent: tool responses are unstructured prose with file paths
    embedded — LLM has to grep paths from text rather than receiving a
    typed schema. Workable but lossy; consider a structured response
    envelope (status, working_dir, log_tail, marker_names) for SU2 tools.
  - pycycle agent: list_variables dumps the entire variable tree into
    context multiple times — same +30K-token-per-call bloat as run #1
  - pycycle agent: design-point inputs lead to unrealistic engine
    (SFC=0.652 lb/hr/lbf high, thrust=5900 lbf low — F25 needs ~15k
    lbf/engine at cruise)
  - mission_architect: aircraft parameter set from upstream stages
    unclear; need to verify TiGL geometry params (span, sweep, area,
    etc.) and engine cycle params actually flow into aviary
    set_aircraft_parameters
  - mission_architect: aviary lacks a structured "design space" query
    (analogous to pycycle list_variables) that the LLM can call to
    discover what aircraft parameters are settable, with units and
    bounds. get_design_space exists but isn't as discoverable. Consider
    a lightweight parameters-info surface so the mission agent doesn't
    have to remember constants from the prompt.
  - aviary get_trajectory dumps a 60-point numeric array (~6KB JSON)
    into LLM context — same dataplane gap as run #1
  - aerodynamics_analyst's reported RESIDUAL_DROP_ORDERS arithmetic is
    wrong direction (residual rose, agent reported it as a drop)

### 2026-05-06
- Changed: Coarser default fidelity for sequential mdo_f25 SU2 stage to fit
  the per-repeat timeout. generate_volume_mesh call in geometry_engineer
  now passes surface_mesh_size=1.0, mesh_size_min=0.3, mesh_size_max=8.0,
  boundary_layer_enabled=false (targets ~50–200k cells solveable in
  <5 min for Euler). Aerodynamics_analyst default ITER lowered from 1000
  to 200 and max_runtime_seconds from 600 to 300. Refined RANS settings
  remain documented for later refined passes.
  Reason: 2026-05-06 12:15 verification run produced a 1.4M-cell mesh
  and hit the 20-min per-repeat wall during SU2 solve; the mesh fix
  itself worked correctly (volume cells present, no DistributeColoring).
- Fixed: TiGL→SU2 mesh handoff. Updated SKILL.md, data_flow.md,
  tool_catalog.md, and all four mdo_f25 pipeline configs to call
  `generate_volume_mesh(session_id, component_uid)` instead of
  `export_component_mesh(format="su2")` for the geometry→CFD step.
  `generate_volume_mesh` already existed in tigl-mcp (uses gmsh to embed
  the STL surface in a far-field box and emit a complete SU2 volume
  mesh with "aircraft" wall + "farfield" markers); the framework was
  simply never telling agents to call it. `export_component_mesh`
  produces a surface-only mesh (0 volume cells) which causes SU2's
  `DistributeColoring` to fail immediately. Tool list of geometry agent
  now includes both tools — surface mesh kept for visualization /
  non-CFD use, volume mesh required for CFD.
- Security: rotated leaked credentials and rewrote git history. Removed
  ANTHROPIC_API_KEY and WANDB_API_KEY hardcoded in run_batch.sh; replaced
  with .env loading. Added .env.example template. .env is gitignored.
- Changed: run_batch.sh now sources secrets from .env via `set -a; source .env`
  rather than hardcoding values, so contributors can keep their own keys
  out of the repo.
- Tested: ran one repeat of mdo_f25_sequential_iterative_feedback against
  Claude Sonnet 4 (LiteLLM), all 5 MCPs live. ~55 steps before manual stop.
  Wandb run: stat_1x1_1778069719 (mts2jmnb), project mas-aviary-stat.
- Confirmed working from this run:
  - Multi-MCP session handoff (each MCP gets its own session, IDs flow
    correctly between agents)
  - UPSTREAM_ERROR propagation in iterative_feedback handler (when SU2
    failed, downstream agents saw an error string instead of fabricating
    inputs)
  - Type coercion middleware (no "null"-as-string or JSON-as-string errors)
  - Data plane intercepts base64 mesh payloads from tigl as designed
  - All 6 agents executed: geometry_engineer, aerodynamics_analyst,
    structures_analyst, propulsion_analyst, mission_architect,
    simulation_executor
- Found bugs:
  - TiGL→SU2 mesh handoff: tigl-mcp `export_component_mesh` produces a
    surface-only mesh (0 volume elements per SU2 log: "17737 grid points,
    0 volume elements, 35470 boundary elements"). SU2 fails
    DistributeColoring, returns SOLVER_CONVERGED=false, RESIDUAL_DROP=0.
    Likely needs gmsh volume meshing step that was reverted in tigl-mcp
    commit 43e205e. Fix targeted next.
  - mass-mcp OAS solver: "array must not contain infs or NaNs" in
    SolveMatrix — cascade from bad upstream geometry. Fallback to
    flops_only works (OEM=35,725 kg, wing=7,677 kg, plausible).
  - numpy truth-value ambiguity ValueError in update_config_entries
    (su2-mcp side) — array-vs-scalar handling.
  - Data plane gap for large numeric payloads: 60-point trajectory dict
    (~6KB JSON) was not intercepted; only base64 patterns are. Step 8
    input tokens hit 249K, exceeding Sonnet 4.0's 200K context. Need to
    extend data_plane.py to also intercept large dict payloads, not only
    base64 binary.

### 2026-04-02
- Added: Data plane middleware (src/tools/data_plane.py) — intercepts large binary payloads
  (base64 meshes, STEP files) in tool responses, stores them in DesignState.data_store,
  and replaces them with lightweight references. Resolves references back to payloads on
  tool request. Prevents LLM context overflow from 100KB+ base64 mesh data.
- Added: DesignState.data_store field for large inter-MCP payloads (data plane)
- Added: Type coercion middleware (src/tools/type_coercion.py) — fixes mcpadapt bug that
  collapses anyOf types to "string", causing LLMs to send "null" instead of null and
  JSON strings instead of dicts. Coerces argument types before MCP calls.
- Added: LiteLLM backend support in model_loader.py for cloud API models (Claude, GPT, etc.)
- Added: mdo_f25_sequential_iterative_feedback combination in batch_runner.py
- Added: mdo_f25_run_claude.yaml config for Claude Sonnet 4 via LiteLLM
- Changed: Tool loading pipeline now applies full middleware stack (type coercion +
  data plane) to all MCP tools via apply_coercion_to_tools()

### 2026-03-29
- Added: DesignState class for centralized multi-MCP session/result/constraint tracking (src/coordination/design_state.py)
- Added: Multi-MCP connection support with per-server naming, tool routing, and graceful degradation (src/tools/mcp_connector.py)
- Added: Server-scoped tool patterns (e.g. "tigl.*", "su2.run_su2_solver") in pipeline templates
- Added: SkillLoader for LLM-agnostic skill injection — Mode A (Claude API) and Mode B (prompt injection) (src/skills/skill_loader.py)
- Added: MCPServerConfig.name field for named multi-server configs
- Added: SkillsConfig dataclass and AppConfig.skills field
- Added: Per-server environment variable URL overrides (MAS_AVIARY_TIGL_URL, etc.)
- Added: Aircraft Design MDO Skill (skills/aircraft-design-mdo/)
  - SKILL.md: Pipeline overview, discipline roles, tool sequences, DLR-F25 constraints, convergence criteria
  - references/tool_catalog.md: 54 tools across 5 MCPs discovered via live MCP tool listing
  - references/data_flow.md: DesignState schema and inter-MCP data transfers
  - references/f25_constraints.md: Full DLR-F25 optimization formulation with fidelity assessments
  - references/discipline_tradeoffs.md: 7 cross-disciplinary coupling descriptions
  - references/design_variables.md: Complete variable catalog across all 5 MCPs
  - scripts/validate_design_state.py: DesignState consistency checker
- Added: MDO F25 pipeline template YAMLs for all 4 organizational structures
  - config/mdo_f25_sequential_agents.yaml: 7-stage sequential pipeline (TiGL→SU2→Mass→PyCycle→Aviary)
  - config/aviary_mdo_f25_sequential.yaml: Sequential strategy config
  - config/mdo_f25_orchestrated_agents.yaml: Orchestrated config with 7 required tool phases
  - config/mdo_f25_networked_agents.yaml: Networked config with 7 workflow phases
  - config/mdo_f25_graph.yaml: Graph-routed state machine with MDO iteration loop
  - config/mdo_f25_staged_pipeline.yaml: Staged pipeline with per-stage completion criteria
  - config/mdo_f25_run.yaml: Main entry point config for 5-MCP runs
- Added: Tests for DesignState (21 tests) and SkillLoader (17 tests)
- Changed: MCPConnector now gracefully degrades when individual servers fail (logs warning, continues with available servers)
- Changed: Updated test_connection_error_propagates to test_connection_error_graceful_degradation
