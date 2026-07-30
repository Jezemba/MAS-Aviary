# Aero-coupling: full analysis & design

**Date:** 2026-07-28 · **Branch:** `feat/enforce-aero-coupling` (off `feat/paper-experiment-design`)
**Status:** analysis complete; typed-coupling foundation built (`src/tools/coupling.py`); wiring in progress.
**Why this exists:** the paper sweep was paused after combo 1 to analyze results. That analysis
uncovered a coupling-reliability problem that would have silently corrupted the fuel metric.
This document preserves the full finding + the architecture map + the fix design so it is not lost.

---

## 1. The finding — fuel is deterministic but BIMODAL on aero coupling

Aviary `fuel_burned_kg` is fully reproducible (the pipeline is deterministic — `full_pipeline_probe.py`
run1 == run2 byte-for-byte). But it is **bimodal**, governed entirely by whether SU2's drag was
injected into the aviary mission solve:

| condition | flown `cruise_cd_avg` | fuel |
|---|---|---|
| SU2 aero injected (coupled) | ~0.014 | ~9,000–10,000 kg |
| NOT injected (aviary FLOPS default drag) | ~0.0208 | ~12,738 kg |

Combo-1 5-link chain (`logs/stat_results/1785266239`): links 0/3/4 morphed the design + reran SU2
→ injected → ~9,100/9,473/10,133 kg; links 1/2 left the design unchanged + skipped SU2 → not
injected → 12,738 kg. So the fuel spread was NOT noise and NOT non-determinism — it was
coupling-success. (An earlier "non-reproducible" reading was a false alarm from an aviary-ONLY
probe that skipped the injections; see `reference_avion_pipeline_probe_debugging` memory.)

**Decision (Jessica):** the paper run must be a **fully-coupled MDO** — a design change reruns SU2
and aviary must use those results, every time. And the coupling must be a **typed data-flow**, not
regex-scraping + a hidden formula + prompt hope.

---

## 2. Architecture — TWO transport layers, ONE shared state object

There are two independent ways data moves in a run:

1. **Coordination layer (text/messages).** Each strategy/handler passes the *previous agent's text
   output* forward as prose. This is lossy — the code's own comment: the LLM prompt path
   "consistently fails to plumb the SU2 cruise CL/CD into aviary" (`src/tools/data_plane.py:279-285`).
2. **Data plane (typed, in-memory, tool-boundary middleware).** Captures typed values from tool
   *responses* and injects them into downstream tool *requests* — **independent of org structure or
   handler**. Hooks: `intercept_response()` (`data_plane.py:176`) and `resolve_request()`
   (`data_plane.py:479`) run on every MCP tool call.

**CL/CD rides the data plane, NOT the coordination layer.**
- Capture: `_capture_aero_coefficients()` (`data_plane.py:274-323`) — on `read_history_csv`, grabs the
  last row's CL/CD → `data_store["aero_cl_cruise"]` / `["aero_cd_cruise"]`.
- Inject: `_inject_phase_h_aero()` (`data_plane.py:~695`) — on `set_aircraft_parameters`, merges them
  into `parameters` as `Mission.Design.LIFT_COEFFICIENT` + `Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR`.

### One shared DesignState (not two)
There is a single class `src/coordination/design_state.py::DesignState` (fields: `sessions`, `results`,
`constraints`, `data_store`, `iteration`, `history`). The data plane just holds a **global singleton of
that same class** (`src/tools/tool_loader.py:84-93` builds one and calls `init_data_plane(ds, …)`;
`data_plane.py:160,170` expose it as `_design_state`). So the "coordination DesignState" and the
"data-plane DesignState" are the **same object** — the one in-memory place all agents read/write typed
discipline outputs across a run, for all 8 combos. Separate, text-only structures:
- `SharedHistory` — append-only `AgentMessage` log (`coordinator.py:104`).
- `Blackboard` — key/value + TODO board, **networked only** (`blackboard.py`).
- Per-handler `state_dict` / `previous_outputs` — ephemeral routing state (strings).

### The ONLY cross-combo variable is ORDERING
Injection is a no-op unless the aero tool (`read_history_csv`) ran **before** the mission tool
(`set_aircraft_parameters`) in the same run. So per org/handler the real question is: *does this
structure guarantee aero-before-mission ordering?*

---

## 3. Per ORG STRUCTURE (all run through `Coordinator.run()`, `coordinator.py:277-362`)

Agents never call each other; the coordinator runs one agent per step and appends its `AgentMessage`
to `SharedHistory`.

### Sequential (`strategies/sequential.py`)
Linear stage pipeline; each stage is a distinct agent (`_create_stage_agent:337`). Next stage's input is
built from the previous stage's **text** (`_build_stage_context:438-470`), plus a "SHARED STATE" header
that for `DESIGN_STATE` reads the live data-plane DesignState (`sequential.py:392-399`), not regex.
CL/CD reaches mission via the **data plane** (aero stage runs first by construction). **Ordering: low risk.**

### Orchestrated (`strategies/orchestrated.py`) — the structurally different one
An LLM **orchestrator agent** sits in the middle. Phase 1 "creation" (`_creation_step:490`): the
orchestrator uses `CreateAgent`/`AssignTask` tools (`orchestrated.py:238-252`) to **dynamically build
workers and queue assignments**. Phase 2 "execution" (`_execution_step:570`): workers run from the
queue (`assignment = assignments[self._execution_index]`, `:958`). The orchestrator **decides who runs
and in what order** — not a fixed hand-off. But between workers it still passes only **text**
(`input_context = f"...Context from previous agent:\n{prev}"`, `:993/:1093`); it does NOT re-route the
typed float. Its relevant job for coupling is **ordering aero before mission** (its system prompt loads a
`data_flow.md` reference, `_inject_skill_reference:46-89`). Extra hazard: fresh orchestrator-created
workers don't see the real session UUID, so the middleware overrides hallucinated session_ids
(`data_plane.py:521-537`). **Ordering: MEDIUM risk (orchestrator-dependent).**

### Networked (`strategies/networked.py`)
N equal peers, no orchestrator. Coordination via a shared **`Blackboard`** (`:163`) + `SharedHistory`;
peers use `ReadBlackboard`/`WriteBlackboard`, and outputs auto-post as `result` entries (`:332-343`).
Peer modes (`:83-97`): `round_robin`, `volunteer`, `concurrent_blackboard` (CRDT threads racing to claim
TODOs via `parallel_run` → `coordinator._execute_parallel_run:487`). The blackboard carries **text**, not
the float. **No enforced discipline order** — a peer can call mission before any peer ran aero
(`batch_runner.py:258-262` comment). Phase-gating (`_apply_phase_gate:542`) or a graph DAG forces order.
**Ordering: HIGH risk.**

---

## 4. Per HANDLER (sequencing/routing only — not typed-data transport)

Handlers plug into `_execute_agent` via `execution_handler.execute(...)` (`coordinator.py:395`).

- **iterative_feedback** (`iterative_feedback_handler.py`): runs the agent, inspects tool outcomes, and
  **retries the same agent** with feedback until aspiration or `max_retries` (`:245`, `:530`). Text
  hand-off; records `UPSTREAM_ERROR` and prepends to later stages (`:167`). No routing.
- **staged_pipeline** (`staged_pipeline_handler.py`): deterministic assembly line over a
  `PipelineDefinition` (YAML stages); persistent `_stage_cursor` advances one stage per call (`:288-292`);
  completion criteria are **observational only, never block** (`:432-443`). Reorders assignments to pair
  each stage with its same-named agent (`:262-286`). **Ordering fixed by the stage list.**
- **graph_routed** (`graph_routed_handler.py`): a state machine (`GraphDefinition`). One call runs the
  whole graph (`:384-669`), routing by conditions over a **string** `_state_dict` (regex-extracted from
  agent text, `_update_state_from_output:783`). mdo_f25 graph declares explicit `depends_on` data edges
  (`config/mdo_f25_graph.yaml:156,202` — AERO & MASS `depends_on GEOMETRY_SETUP`), consumed by the
  networked DAG executor (`networked.py:1067 _derive_graph_todos`). **Ordering graph-defined.**

**Org × handler selection** in `batch_runner.py`: `_STRATEGY_CONFIGS`/`_MDO_F25_STRATEGY_CONFIGS`
(`:46-57`) pick agents+coord YAML by `org_structure`; `_build_handler` (`:1292`) attaches the handler by
`handler`; special cross-wiring: orchestrated+staged injects `_pipeline_stage_names` (`:1169`),
orch/net+graph extract graph `roles` and pass `_graph_def` (`:1188-1225`).

**The 8 combos are NOT a full 3×3** — `mdo_f25_networked_staged_pipeline` is absent. Present 8:
sequential×{iterative_feedback, staged_pipeline}, orchestrated×{staged_pipeline, iterative_feedback},
networked×{iterative_feedback}, and all three `*_graph_routed` (`batch_runner.py:198-329`).

---

## 5. The typed-contracts coupling design (universal — no per-combo hardcoding)

Because the typed var rides the shared DesignState at the tool boundary, the coupling is written **once**
and works for all 8 combos. `src/tools/coupling.py` (committed `10d7670`) is the single source of truth:
- **CANONICAL_VARS** — typed schema: `aero.cl_cruise`, `aero.cd_cruise`, `aero.l_over_d`, `mass.wing_kg`,
  `mass.mtom_kg`, `prop.sfc_cruise`, `prop.fn_lbf` (producer/consumers/units).
- **Named, documented transforms** (extracted verbatim from the formerly-hidden middleware formulas,
  behavior-preserving, unit-tested): `aero_cd_to_aviary_drag_factor`, `wing_mass_to_aviary_scaler`,
  `mtom_to_pycycle_fn_des`.
- **Typed registry** on `DesignState.data_store["analysis_vars"]` (`put_var`/`get_var`/`has_vars`).

Wiring plan (data plane, universal):
1. **Capture** SU2's *structured* CL/CD → `put_var("aero.cd_cruise", …)` — kills the CSV regex.
2. **Transform** via the named functions — no hidden formula.
3. **Inject** registry → aviary's real typed inputs on `set_aircraft_parameters`.
4. Regex/prompt/formula become **flagged FALLBACK**, only when a typed var is absent.

---

## 6. Ordering enforcement — the genuinely per-combo part (make coupling MANDATORY)

1. **Non-silent injection** (DONE, `94a9104`): missing aero records `aero_coupling_status=MISSING_no_su2_aero`
   + logs a warning (was a silent no-op).
2. **Per-run coupled/uncoupled flag** (DONE): `design_ledger._aero_coupled` (interim: from `cruise_cd_avg`;
   to be superseded by reading the typed registry).
3. **Ordering guarantee per structure:** sequential/staged/graph already enforce aero-before-mission;
   **networked** needs a phase-gate (aero completes before any `set_aircraft_parameters`); **orchestrated**
   needs its orchestrator contract to require aero-before-mission (tighten the `data_flow.md` system-prompt
   ref + rely on the flag).
4. **Runner rejects/retries uncoupled runs** so the paper only scores fully-coupled results.

---

## 7b. Drag physics — why Euler is fake, and the Option A' fix (2026-07-28)

A no-API faithful-pipeline check found the SU2 drag is a **fake signal**: an Euler (inviscid)
solve physically has no skin friction — the dominant cruise-drag component (CD0 ≈ 0.018–0.022
of a total ≈ 0.025–0.030) — and for our high-AR transport at low cruise CL the inviscid
pressure drag is genuinely ~0 (we measured CD ≈ −6.9e-05 on the morphed wing; D'Alembert +
numerical noise). The old transform's fixed `+0.005` add was ~4× too small, so the drag factor
clamped to its 0.5 floor and fuel came out optimistically low.

SU2's own reference confirms this: it ships `TestCases/rans/oneram6` (RANS + SA turbulence +
`REYNOLDS_NUMBER` + Sutherland viscosity + no-slip `MARKER_HEATFLUX` + a boundary-layer mesh)
for *real* drag; we had copied the *inviscid* `euler/oneram6` case (`MARKER_EULER`, slip walls).

**Option A' (chosen — meaningful physics without RANS):** keep the cheap Euler solve for
pressure/induced/wave drag and add a real flat-plate skin-friction estimate:
`CD_total = cd_inviscid + Cf(Re)·(Swet/Sref)·FF`, `Cf = 0.455/(log10 Re)^2.58` (Schlichting).
Re from the canonical cruise Mach/altitude + MAC (ISA atmosphere + Sutherland); Swet/Sref from
the morphed geometry's wetted/reference areas — so the drag is physical AND design-responsive.
`coupling.py` holds the physics; `data_plane._capture_geometry_ref` feeds the typed `geom.*`
vars. Verified: Re 2.6e7, Cf 0.0026, CD0 0.019 (textbook), morphed drag factor 0.82.

**Option B (RANS) — future:** the tigl mesher exposes `boundary_layer_enabled` so a y+≈1 BL
mesh + `SOLVER=RANS` is feasible, giving CFD-computed viscous drag — but ~5× solve cost + mesh
tuning + revalidation. Worth it only if CFD-fidelity is itself a paper claim.

## 7. References (file:line)
- Data plane hooks: `src/tools/data_plane.py:176` (intercept), `:479` (resolve), `:274-323` (aero capture),
  `:~695` (aero inject), `:521-537` (session-id override), `:726-731` (coupling-missing warning).
- Shared state: `src/coordination/design_state.py`, `src/tools/tool_loader.py:84-93`.
- Strategies: `strategies/sequential.py`, `strategies/orchestrated.py`, `strategies/networked.py`.
- Handlers: `iterative_feedback_handler.py`, `staged_pipeline_handler.py`, `graph_routed_handler.py`.
- Selection: `src/runners/batch_runner.py:46-57, 198-329, 1153, 1169, 1188-1225, 1292`.
- Typed coupling: `src/tools/coupling.py`, `tests/test_coupling.py`.
- Finding data: `logs/stat_results/1785266239` (combo-1 chain).
