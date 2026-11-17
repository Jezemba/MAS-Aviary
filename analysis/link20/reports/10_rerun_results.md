# 10. Rerun results for the JMD manuscript (20-iteration re-run, all 8 combinations)

Every number the manuscript reports, recomputed on the re-run only, with the same definitions and scripts as reports
01-08, `07_methods_answers.md` and `08_iterations_and_ladder.md` unless a section says otherwise. "Iteration" replaces
"link" (iteration k = link k = folder `repeat_<k-1>`). Values that differ from the previous run are `new (old)`.

- **Run:** 8 combinations (S_SP, S_IF, S_GR, O_SP, O_IF, O_GR, N_IF, N_GR) x 20 iterations, launched 2026-11-10 to
  2026-11-11, last iteration finished 2026-11-15 16:20. 155 completed iterations (20 each, except O_IF 19 + 1 failed
  and N_IF 16 + 4 failed); 154 flew, 146 of them converged.
- **Data:** aipexws8 `/data/Jessica/l20_final/` (`runs/` = the run folders, copied unchanged; `logs/` = console logs;
  `out/links.csv` = one row per combination x iteration; `reports/<topic>/` = each script's CSVs and `stdout.txt`).
- **Code:** MAS-Aviary branch `analysis/20link-metrics`, commits bcb1d3e..f4dfbb1 (study `final` selected with
  `LINK20_STUDY=final`; fixes to run the scripts on a second study: per-study baseline b3eda63, design_params with no
  extreme values, tools_reliability logs; new `fuel_panels.py`). Commands: `python -m analysis.link20.extract --study
  final --out out`, then each `reports/scripts/<name>.py` (arguments as in their docstrings), from
  `~/Jessica/avion_analysis/code` on aipexws8.
- **Baseline of this run:** 16,390.5 kg fuel, 89,682.6 kg GTOW, converged (section 1). Every "vs baseline" number uses
  it; the previous run's was 18,197.0 kg / 91,660.6 kg.
- **Not computed** (broken by construction): `early_abort`, `claim_conflicts`, `information_ratio`,
  `orchestrator_overhead_ratio`, recorded `routing_accuracy`, `mean_attempts_to_success`.
- **Marks:** numbers on unconverged missions or n < 5 are marked where they appear.
- **Known-flawed iterations** (framework bugs found during the run, fixed afterwards, kept in every number and marked
  where they matter): O_IF 13 (B270: flew the baseline engine instead of the carried BPR 10 cycle); O_GR 5, 7, 8 and
  N_GR 11 (B271: SU2 solved the previous shape's mesh, so the mission flew the old shape's drag); N_GR 6 (B272: flew the
  previous wing's mass); N_IF 11, 17, 20 (B272: SU2 aero or the new wing mass did not reach the mission). 9 of 155.
- Snippets that computed numbers no script prints are given under the numbers (all run read-only on aipexws8).

## 0. Run setup (for the Methods section)

Sources: the run folders and console logs in aipexws8 `/data/Jessica/l20_final/{runs,logs}` (copied unchanged on
2026-11-17), `Avion/.claude/CHANGELOG.md` (launch records 2026-11-10 to 2026-11-15), the git state of each machine,
and a read-only pass over every counted step log (snippet S0 at the end of this section).

### 0.1 Code versions

Not every combination ran the same MAS-Aviary commit. Fixes found while the study ran were merged between launches;
the networked chains were relaunched after B268/B269 (their first launch is not used).

| Combination(s) | Machine | Launched | MAS-Aviary | su2-mcp | aviary-mcp | tigl-mcp | mass-mcp | pycycle-mcp |
|---|---|---|---|---|---|---|---|---|
| S_GR | ws5 | 2026-11-10 17:51 | aeb67dd | ff53ebd | a5ef294 | 59f866b | 1f25297 | 001456b |
| O_SP, O_IF (one run) | ws6 | 2026-11-10 17:51 | aeb67dd | ff53ebd | a5ef294 | 59f866b | 1f25297 | 001456b |
| O_GR | ws8 | 2026-11-10 17:49 | aeb67dd | ff53ebd | a5ef294 | 59f866b | 1f25297 | 001456b |
| S_IF | ws4 (study folder) | 2026-11-11 23:03 | b985d28 | 1b6b1b1 | a5ef294 | 59f866b | 1f25297 (env) | 001456b |
| S_SP | ws5 | 2026-11-11 23:04 | b985d28 | 1b6b1b1 | a5ef294 | 59f866b | 1f25297 | 001456b |
| N_IF, N_GR | ws8 (`/data/avion_study/code`) | 2026-11-11 22:57 | b985d28 | ff53ebd* | a5ef294 | 59f866b | 1f25297 | 001456b |

\* ws8's su2 servers were started on ff53ebd; from 2026-11-11 ~11:20 every `mpirun` on ws8, ws5 and ws6 ran unbound
via `~/.prte/mca-params.conf` (B269), which is what 1b6b1b1 adds in code. S_GR, O_SP/O_IF and O_GR were already
running then; their solves before ~11:20 on 11-11 ran pinned to cores 0-15.

What differs between the two MAS-Aviary commits: b985d28 = aeb67dd + B268 only (after a FAILED iteration the next
starts from the last completed iteration's design). Of the aeb67dd runs only O_IF had a failed iteration (iteration 20,
the last), so B268 never acted on any counted iteration. su2-mcp ff53ebd vs 1b6b1b1: `--bind-to none` only
(performance; no numerical change). Everything else (prompts, best_practices, tools, middleware, MCP servers) is the
same in all eight. Later fixes (B270, B271, B272: MAS-Aviary 1cc10ad..645101b) were merged after every chain had
started and are NOT in this run; their effects are listed as known-flawed iterations (section 9 of this file).

### 0.2 Command line, config and environment

- Command (per run): `scripts/launch_sweep.sh <tag> --config <slot>/run_config.yaml --repeats 20 --timeout 720
  --output-dir logs/stat_results/<tag> --combinations <combo> [<combo>]` from the MAS-Aviary folder. Tags: l20_seq_graph,
  l20_seq_staged, l20_seq_iter, l20_orch_staged_iter (O_SP and O_IF in one run, iterations interleaved),
  l20_orch_graph, l20c_net_graph, l20c_net_iter.
- Config: one `run_config.yaml` per server slot, generated by `setup/start_slot.sh` from the same template (identical
  apart from ports and paths; checked for slot 0 vs slot 4 on ws8). Main model `unsloth/Qwen3-32B-bnb-4bit`
  (device_map "balanced"), summary model `unsloth/Qwen3-4B-Instruct-2507-bnb-4bit` on cuda:0.
- Environment variables:
  - `MAS_AVIARY_ENABLE_THINKING`: not set in any launch. The code default is off (`src/config/loader.py`,
    `enable_thinking: bool = False`); confirmed off in every generation (0.5).
  - `MAS_PEER_BATCH_MAX`: not set (all dispatched peers share one batch, `src/coordination/coordinator.py`).
  - `CUDA_VISIBLE_DEVICES`: ws8 `1` (B243: the GB300; GPU 0 is a 24 GB card not used), ws6 `0,1`, ws5 `0,1`
    (`~/avion_env.sh`), ws4 `0,1`.
  - `SU2_MPI_RANKS`: 16 (`setup/start_slot.sh` default, not overridden).
  - Also set: `MAS_STEP_LOG=1` (step logs), `TMPDIR` per machine, `HF_HOME` on ws4/ws6, `PYTHONNOUSERSITE=1` on ws4/ws5.

### 0.3 Seed

Model seed 42 is set: `run_config.yaml` `seed: 42`, applied by `src/llm/model_loader.py` (`seed_generation()` ->
`transformers.set_seed(42)`, line ~114/145) and recorded in every generation's `sampling` field (0.4). It can be
overridden with `MAS_AVIARY_LLM_SEED` (`src/config/loader.py`), which was not set.

### 0.4 Sampling

Greedy decoding: `run_config.yaml` `temperature: 0.0` (B245, 2026-11-08: "temperature 0 = greedy decoding (do_sample
False), deterministic"), so temperature, top-p and top-k are not used. Every one of the 17,595 generations recorded
`{"do_sample": false, "enable_thinking": false, "seed": 42}` (S0). Max new tokens: 4,096 (`run_config.yaml`).

### 0.5 Thinking mode

Thinking-enabled generations per combination (S0; counted step logs of completed iterations):

| | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR |
|---|---|---|---|---|---|---|---|---|
| Iterations read | 20 | 20 | 20 | 20 | 19 | 20 | 16 | 20 |
| Generations | 939 | 1,073 | 1,928 | 1,314 | 2,520 | 2,394 | 2,856 | 4,571 |
| Thinking enabled | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

0 everywhere, including the orchestrated workers (the previous run had them thinking ON, B216). One N_GR generation
(iteration 7) has text in its `think` field although `thinking_enabled` is false; the text is a tool call
(`{"name": "mark_todo_done", ...}`), not reasoning: the output was split at the wrong place, not a thinking turn. Failed iterations
(O_IF 20; N_IF 2, 3, 10, 15) are not in the table (their attempts are not counted step logs).

### 0.6 Propulsion coupling

Agents' pyCycle cycle choices now change the mission's fuel burn. The framework turns the designed cycle into the
mission engine (B3): after a converged `run_cycle` it builds an engine deck from that cycle (`generate_engine_deck`:
thrust and fuel flow over Mach, altitude and throttle), attaches it to the aviary session (`set_engine_deck`), and
scales it to the thrust sized from the mass and SU2 drag (`Aircraft.Engine.SCALE_FACTOR`). Aviary flies that deck, so
the cycle's SFC and thrust reach the mission, and engine mass follows the deck's sea-level thrust.

Example (links.csv, consecutive flown iterations with the wing area unchanged at 122.8 m^2): O_IF iteration 8 -> 9,
the agents changed BPR 5.105 -> 10.0, fan PR 1.685 -> 1.5, LPC PR 1.935 -> 1.8, HPC PR 9.369 -> 12.0; cruise SFC
0.6432 -> 0.5297 and fuel 21,737 -> 18,519 kg (-14.8 %). Reverting the cycle at 9 -> 10 raised fuel to 20,869 kg.
Other inputs (drag factor, wing mass) were not checked for being identical across these pairs, so the whole change is
not attributed to the cycle. 17 transitions in the study changed an engine input (O_IF 8, O_SP 5, S_SP 3, O_GR 1;
snippet S2); most are rounding of the baseline cycle (5.105 -> 5.1 etc.).

### 0.7 Checklist `claude/09_rerun_checklist.md`

The file was not available to me (not on aipexws3/4/5/6/8). Without it I cannot state item-by-item status. What I can
state from the run is the three items the re-run was required to fix (Avion `.claude/BUGS.md` B231):
- Propulsion -> fuel coupling (B3): applied (0.6).
- Thinking off everywhere (B216/B230): applied and verified, 0 thinking-enabled generations (0.5).
- LLM seed 42 and sampling (B226): applied; seed 42 and greedy decoding in every generation (0.3, 0.4).
Fixes made before or during the run, all in every chain unless noted (BUGS.md): B241 deformed baseline mesh (one mesh
deformed per design), B253-B255, B258-B263, B264 recorded as a limitation in the agents' notes, B265 (the next
iteration starts from the design the last mission flew), B266 (checker), B267 (no payload in a name argument; the
whole tool call bounded; stack dumps on hang), B268 (failed-iteration carry; not in the aeb67dd runs, see 0.1),
B269 (unbound SU2 ranks; partly, see 0.1), best_practices 2026-11-10 entries, per-iteration W&B upload. Someone with
the checklist needs to map these to its items 1-20.

### 0.8 Networked peers

From the config (`config/aviary_mdo_f25_networked.yaml`: `initial_agents: 3`, `max_agents: 7`), the counted step logs
(S0) and `result.json` `agents_spawned`:

| | N_IF | N_GR |
|---|---|---|
| Peers started per iteration | 3 | 3 |
| Peers that took at least one step (all iterations together) | 3 (agent_1 920, agent_2 846, agent_3 971 steps) | 2 (agent_1 3,621, agent_2 752 steps; agent_3 none) |
| `spawn_peer` calls (all attempts, step logs) | 7 | 0 |
| `agents_spawned` in result.json | 3 in all 16 iterations | 2 in 19 iterations, 1 in 1 |

N_IF's 7 spawn calls did not raise `agents_spawned` above 3 in any iteration (whether they were refused by the cap
was not checked).

### 0.9 Agent-facing tools

The runner loaded 61 agent-facing tools ("Loaded 61 tools" in every console log; the five MCP servers plus at least one framework tool, see the note below). 40 of the 61 were called
at least once in some combination (S2); distinct tools called per combination: S_SP 30, S_IF 31, S_GR 29, O_SP 34,
O_IF 36, O_GR 34, N_IF 35, N_GR 34. Never called (21): analyze_mesh, compute_totals, create_session,
export_component_mesh, export_configuration_cad, generate_deformed_mesh, generate_mesh_from_step,
get_component_metadata, get_config_text, get_design_state, get_result_file_base64, get_su2_status,
get_valid_config_options, intersect_components, intersect_with_plane, list_result_files, parse_config, ping,
read_design_json, sample_component_surface, sweep_inputs. Note: the runner's "Loaded 61 tools" list includes
read_design_json, a framework tool, so "61 MCP tools" is approximate; the per-server split was not printed in the logs.

### 0.10 Timeout and retry rule

`--timeout 720`: 720 minutes (12 h) per iteration attempt. A completed attempt with zero fuel and no flown mission is
retried from the same starting design, up to 3 attempts ("attempt k/3: zero fuel, retrying" in the logs); after 3 the
iteration is recorded as failed and the chain continues. Per-tool-call limits (call_watchdog): run_su2_solver 3,600 s,
run_simulation / estimate_mass / generate_volume_mesh 1,800 s, run_cycle 1,200 s, generate_engine_deck 7,200 s,
others 900 s; the whole call (framework work + server) is bounded at that budget + 300 s (B267).

### 0.11 Taper and range checks

- Taper is still in the agents' design table, but as a **derived** row ("Wing taper ratio | Derived | tigl geometry:
  aviary Aircraft.Wing.TAPER_RATIO | ... | follows geometry"): it is not an input of `set_high_level_parameters`
  (best_practices: "span alone, root_chord, tip_chord, dihedral, twist and taper are not inputs").
- Out-of-range inputs are not rejected by range. aviary-mcp `set_aircraft_parameters` only warns ("outside advisory
  range", "outside reliable_range"). tigl's mesh deformation refuses a design the baseline mesh cannot follow
  (`MeshDeformationRefused`, B241), which is a deformation limit, not the design table's stated range. The table's
  ranges (e.g. BPR "up to 10", fan PR 1.4 to 1.7, fuselage length 28 to 50 m) are guidance in the prompt only.

### Snippet S0 (run on aipexws8, read-only)

```python
# cd ~/Jessica/avion_analysis/code && LINK20_STUDY=final ~/Jessica/Avion/.venv/bin/python -
import json, glob, collections
from analysis.link20.extract import SOURCES
for ds, root, combos in SOURCES:
    for combo in combos:
        gens = think = 0; samp = collections.Counter(); steps_by_agent = collections.Counter(); tools = set()
        for d in sorted(glob.glob(f"{root}/repeat_*/{combo}/")):
            try: att = json.load(open(d + "result.json"))["attempt"]
            except OSError: continue                          # failed iteration: no result.json
            for line in open(f"{d}step_log_attempt{att}.jsonl"):
                r = json.loads(line)
                if r.get("kind") != "step": continue
                steps_by_agent[r.get("agent")] += 1
                for g in r.get("generations") or []:
                    gens += 1; think += bool(g.get("thinking_enabled"))
                    if g.get("sampling"): samp[json.dumps(g["sampling"], sort_keys=True)] += 1
            for line in open(f"{d}tool_calls_attempt{att}.jsonl"): tools.add(json.loads(line)["tool"])
```
S2 = the same loop collecting tool names across all combinations vs the "Loaded 61 tools" list of the S_GR console
log, plus, on links.csv, consecutive flown iterations whose `engine.*` columns differ by > 1e-6.

## 1. Baseline starting point

Source: aipexws8 `/data/Jessica/l20_20261112/baseline/baseline_run1.json` (`analysis/link20/baseline_mission.py`,
flown 2026-11-12 on a separate aviary-mcp, port 13603, same aviary-mcp code as the study, a5ef294).

1. **How it was flown:** the unchanged starting design only: the 8 anchor parameters (wing area 122.78 m^2, AR 9.37,
   sweep 21.65 deg, taper 0.246, fuselage length 37.68 m, height 4.15 m, width 3.96 m, engine scale 1.0) on aviary's
   bench aircraft, mission 2,500 nmi / 239 passengers / M0.78 / 33,000 ft, optimizer_max_iter 200. **No** SU2, mass or
   pyCycle coupled values applied (aviary's own drag, wing mass and engine).
2. **Result:** fuel 16,390.5 kg (18,197.0), GTOW 89,682.6 kg (91,660.6), wing mass 8,664.2 kg; converged = true,
   exit_code 0, 12 optimizer iterations, 4.8 s. The optimizer's message text is not saved in the file (only
   converged / exit_code / iterations), so it cannot be quoted.
3. **Limits:** fuel <= 15,000 kg: **no** (16,390.5). GTOW <= 90,000 kg: **yes** (89,682.6). The previous run's baseline
   met neither.
   Why it differs from the previous baseline: same anchor and mission; the aviary-mcp code changed between the two
   flights (old: flown 2026-10-22 on the code of that time; new: a5ef294). Which change moved the baseline was not
   isolated.
4. **Traditional optimizers:** not run on this setup. Report 09 (BO, GA, MADS, SLSQP, random x 5 seeds, 2026-10-31 to
   11-02) used the pre-fix code (before B3: the engine deck did not exist, baseline 18,197.0 kg), so its numbers are not
   comparable with this run and are not repeated here.

## 2. Engineering outcomes (Results 4.1, Table 2)

### 2.1 Iterations flown and converged

Source: `physics.py -> reports/physics/convergence.csv`, `contamination.csv`, `failed_links.csv`.

| Combo | Flown / 20 | Converged (of flown) | Unconverged iterations | Not flown |
|---|---|---|---|---|
| S_SP | 20 (20) | 20 (5) | none | none |
| S_IF | 20 (20) | 20 (1) | none | none |
| S_GR | 20 (16) | 15 (2) | 3, 4, 14, 19, 20 | none |
| O_SP | 20 (20) | 20 (5) | none | none |
| O_IF | 19 (19) | 17 (10) | 6, 8 | 20 (failed, zero fuel) |
| O_GR | 20 (20) | 20 (11) | none | none |
| N_IF | 15 (7) | 14 (3) | 13 | 2, 3, 10, 15 (failed, zero fuel); 12 (completed but no flown mission) |
| N_GR | 20 (18) | 20 (0) | none | none |
| **All** | **154 (140)** | **146 (37)** | | |

S_GR unconverged list derived from `convergence.csv conv_links` (1 2 5-13 15-18 converged). N_IF: 16 iterations
completed but only 15 flew (iteration 12 completed without a flown mission, `contamination.csv did_not_fly` = 2 3 10 12
15 vs failed = 2 3 10 15). N_IF 13 carries a B187 flag (`contamination.csv`, the only contaminated flown iteration).

### 2.2 Fuel per combination (flown iterations, kg)

Mean/median/std/min/max: `physics.py -> reports/physics/summary_fuel_kg.csv`. Mean vs baseline (16,390.5 kg):
snippet A. n = flown iterations (column 2).

| Combo | n | Mean | Median | Std | Min | Max | Mean vs baseline (kg, %) |
|---|---|---|---|---|---|---|---|
| S_SP | 20 (20) | 21,024.2 (25,071.0) | 20,985.6 (25,143.4) | 940.5 (6,522.2) | 19,774.2 (11,823.8) | 23,792.7 (36,414.7) | +4,633.7, +28.3 % (+6,874.0, +37.8 %) |
| S_IF | 20 (20) | 21,144.9 (22,021.8) | 20,988.8 (21,850.5) | 1,138.8 (1,442.6) | 19,403.3 (19,355.3) | 23,008.5 (26,154.7) | +4,754.4, +29.0 % (+3,824.8, +21.0 %) |
| S_GR | 20 (16) | 43,484.9 (25,456.3) | 48,119.5 (27,919.7) | 10,002.0 (3,492.7) | 22,106.2 (19,280.7) | 51,836.1 (28,290.9) | +27,094.4, +165.3 % (+7,259.3, +39.9 %) |
| O_SP | 20 (20) | 22,167.0 (31,908.0) | 23,006.4 (34,625.9) | 2,515.8 (6,315.8) | 14,812.1 (18,520.8) | 23,086.0 (41,137.6) | +5,776.5, +35.2 % (+13,711.0, +75.3 %) |
| O_IF | 19 (19) | 20,654.0 (25,003.1) | 20,987.3 (23,553.8) | 2,062.1 (6,585.0) | 13,765.6 (17,302.2) | 22,914.1 (41,808.1) | +4,263.5, +26.0 % (+6,806.1, +37.4 %) |
| O_GR | 20 (20) | 25,637.8 (30,969.4) | 23,192.0 (32,892.7) | 5,116.6 (5,573.2) | 19,321.3 (18,125.8) | 38,977.8 (40,918.3) | +9,247.3, +56.4 % (+12,772.4, +70.2 %) |
| N_IF | 15 (7) | 23,679.0 (19,551.7) | 22,914.1 (18,452.1) | 6,142.5 (2,659.7) | 16,577.5 (17,520.5) | 38,631.9 (25,196.4) | +7,288.5, +44.5 % (+1,354.7, +7.4 %) |
| N_GR | 20 (18) | 24,349.1 (32,998.9) | 20,985.2 (34,737.4) | 8,173.7 (15,100.6) | 14,199.4 (6,668.9) | 38,912.9 (54,356.5) | +7,958.6, +48.6 % (+14,801.9, +81.3 %) |
| All | 154 (140) | 25,349.1 (27,232.9) | 22,070.4 (n/a) | | 13,765.6 | | +8,958.6, +54.7 % |

Old "vs baseline" is relative to the old baseline 18,197.0 kg; new is relative to 16,390.5 kg, so the two columns
compare against different references. Pooled n = 154: `sensitivity.csv all_flown ALL`; pooled vs baseline: snippet A.
Mean fuel is above this run's baseline in all 8 combinations (n = 15-20 each). Converged-only means
(`sensitivity.csv converged_only`) differ from the all-flown means only in S_GR (46,954.8, n = 15), O_IF (20,526.6,
n = 17) and N_IF (23,894.3, n = 14).

### 2.3 Best converged fuel and iteration-20 fuel

Source: snippet A (best = lowest-fuel flown iteration whose counted mission converged; vs 16,390.5 kg); iteration 20
fuel cross-checked with `summary_fuel_kg.csv final_value`. Old values: report 01 convergence table ("Min fuel,
converged (link)") and fuel table ("Link 20").

| Combo | Best converged fuel (iteration) | Best conv. vs baseline | Iteration-20 fuel | It. 20 vs baseline | It. 20 converged |
|---|---|---|---|---|---|
| S_SP | 19,774.2 (3) — (23,553.6 (4)) | +3,383.7 | 20,103.4 (31,501.4) | +3,712.9 | yes |
| S_IF | 19,403.3 (19) — (20,740.5 (18)) | +3,012.8 | 19,403.3 (26,154.7) | +3,012.8 | yes |
| S_GR | 28,530.3 (1) — (22,810.5 (1)) | +12,139.8 | 35,624.5 (28,161.9) | +19,234.0 | **no** (unconverged) |
| O_SP | 14,812.1 (6; same value at 19) — (26,952.6 (9)) | −1,578.4 | 23,006.4 (34,021.9) | +6,615.9 | yes |
| O_IF | 13,765.6 (4) — (18,807.4 (19)) | −2,624.9 | did not fly (failed) — (21,823.5) | n/a | n/a |
| O_GR | 19,321.3 (7) **[flawed: B271]** — (18,125.8 (8)) | +2,930.8 | 23,192.0 (22,848.4) | +6,801.5 | yes |
| N_IF | 16,577.5 (17) **[flawed: B272]** — (17,520.5 (18)) | +187.0 | 18,958.6 (25,196.4) | +2,568.1 | yes (iteration 20 is B272) |
| N_GR | 14,199.4 (11) **[flawed: B271]** — (none converged) | −2,191.1 | 35,348.7 (29,510.6) | +18,958.2 | yes |

S_GR's lowest flown fuel (22,106.2 kg, iteration 3) is unconverged, so its best converged value is iteration 1.
O_IF iteration 20 failed (zero fuel), so there is no iteration-20 fuel; its last flown iteration is 19 (20,766.7 kg,
snippet A-2). N_IF's best converged without the flawed iterations would be the next-lowest converged value, not computed
here (see Possible bugs).

### 2.4 GTOW

Mean: `physics.py -> summary_gtow_kg.csv`; lowest GTOW and its iteration / convergence: snippet A.

| Combo | n | Mean GTOW (kg) | Lowest GTOW (iteration, converged) |
|---|---|---|---|
| S_SP | 20 | 98,711.4 (107,516.1) | 97,369.1 (3, yes) — (92,676.3 (11)) |
| S_IF | 20 | 97,687.1 (102,702.0) | 95,023.4 (19, yes) — (97,983.6 (18)) |
| S_GR | 20 | 130,625.3 (109,476.5) | 96,671.4 (3, **no**) — (95,958.4 (2)) |
| O_SP | 20 | 98,951.6 (116,284.7) | **88,021.2 (6, yes; identical at 19)** — (94,093.5 (4)) |
| O_IF | 19 | 99,111.0 (109,389.4) | 88,226.4 (4, yes) — (95,406.4 (6)) |
| O_GR | 20 | 105,399.3 (117,866.1) | 95,520.8 (7, yes) **[flawed: B271]** — (96,128.0 (8)) |
| N_IF | 15 | 103,448.0 (96,281.1) | 92,480.3 (11, yes) **[flawed: B272]** — (91,700.7 (18)) |
| N_GR | 20 | 105,036.2 (130,263.3) | 91,529.7 (11, yes) **[flawed: B271]** — (91,433.9 (16)) |

**Lowest GTOW of any flown iteration: O_SP iteration 6, 88,021.2 kg (converged; iteration 19 has the identical
value)** (old: N_GR link 16, 91,433.9 kg, unconverged). Mean GTOW vs this run's baseline (89,682.6 kg) is above it in
all 8 combinations (smallest S_IF +8,004.5 kg = 97,687.1 − 89,682.6, arithmetic on the table).

### 2.5 Trend: mean of iterations 1-10 vs 11-20, least-squares slope

Source: `physics.py -> summary_fuel_kg.csv` (`mean_first10`, `mean_last10`, `ols_slope_per_link` = numpy polyfit
degree 1 of fuel on iteration number over flown iterations); reproduced exactly by snippet A.

| Combo | Mean it. 1-10 (n) | Mean it. 11-20 (n) | 11-20 minus 1-10 | Slope (kg/iteration) |
|---|---|---|---|---|
| S_SP | 21,357.0 (10) | 20,691.4 (10) | −665.6 (+1,444.3) | −56.8 (+401.2) |
| S_IF | 21,449.3 (10) | 20,840.5 (10) | −608.8 (+850.4) | −81.4 (+102.3) |
| S_GR | 40,464.8 (10) | 46,505.0 (10) | +6,040.2 (+2,562.4) | +677.5 (+259.3) |
| O_SP | 22,166.4 (10) | 22,167.7 (10) | +1.3 (+6,628.2) | −49.1 (+684.0) |
| O_IF | 20,731.2 (10) | 20,568.1 (9) | −163.1 (+1,085.6) | −18.7 (−26.7) |
| O_GR | 24,405.4 (10) | 26,870.1 (10) | +2,464.7 (+2,792.3) | +293.2 (+246.2) |
| N_IF | 23,915.5 (7) | 23,472.1 (8) | −443.4 (+2,529.5) | +186.9 (+182.8) |
| N_GR | 20,028.7 (10) | 28,669.5 (10) | +8,640.8 (−2,382.5) | +944.1 (−43.9) |

Sign of the slope: negative in 4 of 8 (S_SP, S_IF, O_SP, O_IF), positive in 4 (old: positive in 6 of 8). The two trend
measures disagree in sign for O_SP (+1.3 kg mean difference, −49.1 kg/iteration slope) and N_IF (−443.4 vs +186.9).
Iterations are a chained path, not independent samples, so the slope is descriptive (no standard error given).
S_GR's means include 5 unconverged iterations; N_IF's halves rest on 7 and 8 iterations.

### 2.6 Iteration-1 fuel

Source: `physics.py -> reports/physics/link1.csv` (`first_fuel_kg` = first flown mission of iteration 1; `fuel_kg` =
the iteration's counted fuel); vs 16,390.5 kg: snippet A. Old values: ws8
`~/Jessica/avion_analysis/reports_20261028/physics/link1.csv` (read with `cat`).

| Combo | First mission of it. 1 (kg) | vs baseline (kg, %) | Counted it.-1 fuel | Converged | Old first mission |
|---|---|---|---|---|---|
| S_SP | 21,876.2 | +5,485.7, +33.5 % | 21,876.2 | yes | 23,077.1 |
| S_IF | 21,876.2 | +5,485.7, +33.5 % | 21,876.2 | yes | 23,077.5 |
| S_GR | 21,788.5 | +5,398.0, +32.9 % | 28,530.3 | yes | 22,997.6 |
| O_SP | 22,914.1 | +6,523.6, +39.8 % | 22,914.1 | yes | 22,974.5 |
| O_IF | 22,914.1 | +6,523.6, +39.8 % | 22,914.1 | yes | 22,914.4 |
| O_GR | 21,788.5 | +5,398.0, +32.9 % | 21,698.6 | yes | 22,663.5 |
| N_IF | 22,916.0 | +6,525.5, +39.8 % | 22,916.0 | yes | did not fly |
| N_GR | 21,788.5 | +5,398.0, +32.9 % | 19,761.2 | yes | 22,997.6 |

Range of the first iteration-1 mission: 21,788.5-22,916.0 kg, i.e. +32.9 % to +39.8 % above this run's baseline
(old: +24.5 % to +26.8 % above the old baseline). The chain therefore again starts well above the baseline: applying
the coupled SU2 / mass / pyCycle values on iteration 1 adds about 5,400-6,500 kg to the 16,390.5 kg baseline. The
first-mission values repeat exactly across combinations with the same handler path (S_SP = S_IF; S_GR = O_GR = N_GR;
O_SP = O_IF), which suggests a deterministic first mission from the shared anchor.

### 2.7 Across the study: limits and convergence

Source: totals from `physics.py -> constraints.csv` and `convergence.csv`; converged counts within each limit:
snippet A.

| | New | Old |
|---|---|---|
| Iterations flown | 154 | 140 |
| Flown and converged | 146 | 37 |
| Fuel <= 15,000 kg | 4 (O_SP 6, O_SP 19, O_IF 4, N_GR 11), all 4 converged | 4, 0 converged |
| GTOW <= 90,000 kg | 3 (O_SP 6, O_SP 19, O_IF 4), all 3 converged | 0 |
| Both limits | 3 (O_SP 6, O_SP 19, O_IF 4), all 3 converged | 0 |

N_GR 11 (fuel 14,199.4 kg, GTOW 91,529.7 kg) meets the fuel limit only and is **[flawed: B271]**. O_SP 6 and 19 have
identical fuel and GTOW (14,812.1 / 88,021.2 kg), so the three both-limit iterations are two distinct designs. n = 3-4,
fewer than 5: treat as cases, not rates.

### 2.8 Converged iterations below this run's baseline (16,390.5 kg)

Source: snippet A (`fuel_kg < 16,390.5` among flown iterations). 5 flown iterations, all 5 converged (old: 11 flown
below the old baseline 18,197.0 kg, 3 converged).

| Combo | Iteration | Fuel (kg) | GTOW (kg) | Converged | Note |
|---|---|---|---|---|---|
| O_IF | 4 | 13,765.6 | 88,226.4 | yes | engine scale 1.106 (snippet A-2: next iteration 1.61, fuel 21,811.6) |
| N_GR | 11 | 14,199.4 | 91,529.7 | yes | **[flawed: B271]**; wing area 160.0 m², AR 13.0 (end_state.csv max at 11) |
| O_SP | 6 | 14,812.1 | 88,021.2 | yes | engine scale 0.978 (end_state.csv min at 6) |
| O_SP | 19 | 14,812.1 | 88,021.2 | yes | identical to O_SP 6 |
| N_GR | 12 | 15,255.1 | 92,345.6 | yes | |

`physics.py -> below_baseline_links.csv` lists 11 iterations because it uses the old baseline (18,197.0 kg); the 6
extra ones (N_GR 10, 9, 8; N_IF 17, 11; O_IF 12) lie between 16,390.5 and 18,197.0 kg, and N_IF 11 and 17 are
[flawed: B272].

### Snippet A (run on aipexws8, read-only; reads links.csv and physics.py's links_physics.csv)

`cd ~/Jessica/avion_analysis/code && LINK20_STUDY=final ~/Jessica/Avion/.venv/bin/python - <<'EOF' ... EOF`

```python
import pandas as pd, numpy as np
B_F, B_G = 16390.5, 89682.6
L = pd.read_csv("/data/Jessica/l20_final/out/links.csv")
P = pd.read_csv("/data/Jessica/l20_final/reports/physics/links_physics.csv")
S = {"sequential":"S","orchestrated":"O","networked":"N"}; H = {"staged_pipeline":"SP","iterative_feedback":"IF","graph_routed":"GR"}
L["combo"] = L.structure.map(S) + "_" + L.handler.map(H)
df = L.merge(P[["combo","link","mission_converged"]], on=["combo","link"], how="left")
df["flew"] = df.flew.astype(str).eq("True")
df["conv"] = df.mission_converged.astype(str).eq("True") & df.flew
ORDER = ["S_SP","S_IF","S_GR","O_SP","O_IF","O_GR","N_IF","N_GR"]
rows = []
for c in ORDER:
    d = df[(df.combo == c)].sort_values("link"); f = d[d.flew]
    fc = f[f.conv]; b = fc.loc[fc.fuel_kg.idxmin()] if len(fc) else None
    a, z = f[f.link <= 10], f[f.link > 10]
    sl = np.polyfit(f.link, f.fuel_kg, 1)[0]
    l1 = d[d.link == 1].iloc[0]; l20 = d[d.link == 20].iloc[0]
    g = f.loc[f.gtow_kg.idxmin()]
    rows.append(dict(combo=c, n_flown=len(f), n_conv=len(fc), mean=f.fuel_kg.mean(), d_mean=f.fuel_kg.mean()-B_F,
        pct=100*(f.fuel_kg.mean()-B_F)/B_F, best_conv=b.fuel_kg if b is not None else np.nan, best_conv_it=b.link if b is not None else None,
        best_conv_vs=(b.fuel_kg-B_F) if b is not None else np.nan, it20=l20.fuel_kg if l20.flew else np.nan, it20_vs=(l20.fuel_kg-B_F) if l20.flew else np.nan,
        n1_10=len(a), m1_10=a.fuel_kg.mean(), n11_20=len(z), m11_20=z.fuel_kg.mean(), diff=z.fuel_kg.mean()-a.fuel_kg.mean(), slope=sl,
        gtow_mean=f.gtow_kg.mean(), gtow_min=g.gtow_kg, gtow_min_it=g.link, gtow_min_conv=g.conv,
        it1_first=l1.first_fuel_kg, it1_first_vs=l1.first_fuel_kg-B_F, it1_first_pct=100*(l1.first_fuel_kg-B_F)/B_F, it1_final=l1.fuel_kg, it1_conv=l1.conv))
pd.set_option("display.width", 300); pd.set_option("display.max_columns", 50)
print(pd.DataFrame(rows).round(1).to_string(index=False))
f = df[df.flew]
fo, go = f.fuel_kg <= 15000, f.gtow_kg <= 90000
print("TOTAL flown", len(f), "conv", int(f.conv.sum()))
for nm, m in [("fuel_ok", fo), ("gtow_ok", go), ("both", fo & go)]:
    print(nm, int(m.sum()), "converged", int((m & f.conv).sum()), f.loc[m, ["combo","link","fuel_kg","gtow_kg","conv"]].values.tolist())
print("pooled mean fuel", round(f.fuel_kg.mean(),1), "vs base", round(f.fuel_kg.mean()-B_F,1), round(100*(f.fuel_kg.mean()-B_F)/B_F,1))
bb = f[f.fuel_kg < B_F].sort_values("fuel_kg")
print("below 16390.5 flown:", len(bb), "converged:", int(bb.conv.sum()))
print(bb[["combo","link","fuel_kg","gtow_kg","conv"]].round(1).to_string(index=False))
# engine cycle inputs vs fuel, consecutive iterations (used in 3.2)
E = ["engine.T4_MAX","engine.splitter.BPR","engine.fan.PR","engine.lpc.PR","engine.hpc.PR"]
for c in ORDER:
    d = df[df.combo == c].sort_values("link").reset_index(drop=True)
    for i in range(1, len(d)):
        p, q = d.loc[i-1], d.loc[i]
        ch = [e for e in E if pd.notna(p[e]) and pd.notna(q[e]) and abs(q[e]-p[e]) > 1e-3*abs(p[e])]
        if ch:
            print(c, int(q.link), {e.split('.',1)[1]: (p[e], q[e]) for e in ch}, "sfc", p["propulsion.sfc_cruise"], "->", q["propulsion.sfc_cruise"],
                  "scale", round(p["Aircraft.Engine.SCALE_FACTOR"],3), "->", round(q["Aircraft.Engine.SCALE_FACTOR"],3),
                  "fuel", round(p.fuel_kg,1) if p.flew else "noflight", "->", round(q.fuel_kg,1) if q.flew else "noflight", "conv", p.conv, q.conv)
    print(c, "iterations with engine values:", int(d[E[0]].notna().sum()), "distinct sfc:", sorted(d["propulsion.sfc_cruise"].dropna().round(6).unique().tolist()))
```

Snippet A-2 (same invocation; prints the coupled values around the engine-change iterations, used in 2.3, 2.8, 3.2):

```python
import pandas as pd
L = pd.read_csv("/data/Jessica/l20_final/out/links.csv")
S = {"sequential":"S","orchestrated":"O","networked":"N"}; H = {"staged_pipeline":"SP","iterative_feedback":"IF","graph_routed":"GR"}
L["combo"] = L.structure.map(S) + "_" + L.handler.map(H)
K = [("S_SP",[14,15,16,19,20]),("O_IF",[8,9,10,11,12,13,19,20]),("O_GR",[12,13]),("O_SP",[2,3,4,5,13,14,15])]
cols = ["link","fuel_kg","propulsion.sfc_cruise","Aircraft.Engine.SCALE_FACTOR","Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR","Mission.Design.LIFT_COEFFICIENT","Aircraft.Wing.AREA","Aircraft.Wing.ASPECT_RATIO","Aircraft.Wing.MASS_SCALER","wing_mass_kg"]
for c, ks in K:
    print(c); print(L[(L.combo==c) & L.link.isin(ks)].sort_values("link")[cols].round(4).to_string(index=False))
```

(The O_IF 4 → 5 engine-scale figures in 2.8 come from snippet A's engine-change line for O_IF 5.)

---

## 3. Design movement (Results 4.1, second paragraph)

"Moved" = differs from the previous iteration's value (iteration 1: from the baseline) by more than 0.1 %, as in
report 02. Source unless stated: `design_params.py -> reports/design_params/`.

### 3.1 Transitions in which each parameter moved (> 0.1 %)

`change_counts_pivot.csv` (moved), `transitions_pivot.csv` (transitions with a value on both sides: 20 per combination
for the geometry and engine scale except O_IF 19 and N_IF 16; engine cycle inputs 19-20, but N_GR only 10 / 9 because
N_GR has engine values on 10 iterations only; design CL / drag factor 19, N_IF 14). Old values in parentheses
(report 02 section 1).

| Parameter | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR |
|---|---|---|---|---|---|---|---|---|
| Wing area | 4 (7) | 9 (6) | 4 (7) | 0 (2) | 0 (1) | 1 (3) | 6 (6) | 13 (15) |
| Wing AR | 9 (15) | 6 (1) | 2 (5) | 0 (6) | 7 (6) | 4 (4) | 4 (8) | 11 (16) |
| Wing sweep | 0 (15) | 0 (1) | 1 (5) | 1 (6) | 1 (6) | 1 (4) | 1 (8) | 4 (14) |
| Wing taper | 0 (0) | 0 (0) | 0 (0) | 0 (0) | 0 (0) | 0 (0) | 0 (0) | 0 (0) |
| Wing span | 13 (17) | 15 (7) | 6 (7) | 0 (6) | 7 (6) | 4 (5) | 13 (10) | 14 (16) |
| Fuselage length / height / width | 0 (0) | 0 (0) | 0 (2) | 0 (0) | 0 (6) | 0 (1-2) | 0 (0) | 0 (4-5) |
| Engine scale | 16 (19) | 18 (10) | 16 (13) | 15 (15) | 11 (16) | 14 (13) | 12 (12) | 16 (9) |
| T4 | 0 (0) | 0 (0) | 0 (0) | 0 (2) | 0 (8) | 0 (4) | 0 (2) | 0 (0) |
| BPR | 3 (0) | 0 (0) | 0 (0) | 0 (2) | 5 (10) | 1 (7) | 0 (0) | 0 (0) |
| Fan PR | 0 (0) | 0 (0) | 0 (0) | 4 (4) | 9 (10) | 1 (4) | 0 (0) | 0 (0) |
| LPC PR | 0 (0) | 0 (0) | 0 (0) | 4 (4) | 9 (9) | 0 (4) | 0 (0) | 0 (0) |
| HPC PR | 0 (0) | 0 (0) | 0 (0) | 1 (2) | 5 (9) | 0 (8) | 0 (0) | 0 (0) |
| Design CL | 4 (16) | 1 (9) | 12 (10) | 4 (11) | 6 (11) | 11 (13) | 10 (12) | 17 (19) |
| Drag factor | 16 (19) | 16 (10) | 4 (11) | 14 (15) | 14 (17) | 14 (14) | 13 (15) | 16 (3) |
| Wing mass scaler | no values (18) | no values (8) | no values (7) | no values (7) | no values (7) | no values (6) | no values (7) | no values (19) |

The single sweep move in O_SP, O_IF and N_IF is iteration 1 (`change_counts.csv moved_at_links` = 1): the aviary
`Aircraft.Wing.SWEEP` goes from 21.65 to 27.33 ° there and then stays (see Possible bugs). Mission and tails never
moved: 0 of the successful `configure_mission` calls differ from the fixed mission (`mission_settings.csv`). Tails:
the only tail morphs are O_SP 1, O_IF 1, N_IF 1 and N_GR 20 (`tail_morph_calls.csv`). N_GR 20 restates the existing
tails exactly; the three iteration-1 calls keep area and AR (change ≤ 0.02 m²) but **change the tail sweeps**: Wing2H
28.00 → 32.21 °, Wing3V 37.20 → 40.39 ° (the design-table defaults, which differ from the measured geometry). These
are the same three combinations whose wing sweep jumps at iteration 1 (Possible bugs 3). Wing taper: 0 moves everywhere. Wing mass scaler: `Aircraft.Wing.MASS_SCALER` is empty in
links.csv on all 160 rows of this run (`transitions_pivot.csv` = 0), so it cannot be counted (in the old run it was the
data-plane wing-mass value).

### 3.2 Engine cycle inputs: who changed them, and did fuel move

Sources: `design_params.py -> change_counts.csv`, `sfc_changes.csv`, `cycle_key_counts.csv`; per-transition values and
fuel from snippet A (engine-change lines) and A-2, on `links.csv engine.*`, `propulsion.sfc_cruise`, `fuel_kg`.

Combinations that changed at least one cycle input between iterations: **S_SP, O_SP, O_IF, O_GR** (4 of 8; old: O_SP,
O_IF, O_GR plus N_IF T4). T4_MAX did not move in any combination (0 transitions; old 2-8 in the orchestrated
combinations). S_IF, S_GR, N_IF and N_GR end every iteration at the canonical cycle (T4 2,857 °R, BPR 5.105, fan PR
1.685, LPC 1.935, HPC 9.369); their successful `set_inputs` calls (`cycle_key_counts.csv`, e.g. N_IF 65 T4_MAX calls)
set values the cycle already had. N_GR has engine values on 10 of 20 iterations only (snippet A: "iterations with
engine values: 10").

**Cycle changes now move cruise SFC and fuel** (old run: SFC changed only where the cycle did, but fuel was not
attributable). Every change of BPR / PR changes `propulsion.sfc_cruise`, and in the transitions where engine scale,
drag factor, design CL and geometry stay (nearly) fixed, fuel moves with SFC:

| Combo, transition | Cycle change | SFC | Engine scale | Drag factor | Fuel (kg) | Both converged |
|---|---|---|---|---|---|---|
| S_SP 14 → 15 | BPR 5.105 → 4.5 | 0.6432 → 0.6719 (+4.5 %) | 1.468 → 1.460 | 1.194 → 1.188 | 20,458.1 → 21,181.4 (+723.3) | yes |
| S_SP 15 → 16 | BPR 4.5 → 5.105 | 0.6719 → 0.6432 | 1.460 → 1.470 | 1.188 → 1.197 | 21,181.4 → 19,982.0 (−1,199.4; area 115 → 120 m² too) | yes |
| S_SP 19 → 20 | BPR 5.105 → 6.0 | 0.6432 → 0.6065 (−5.7 %) | 1.559 → 1.559 | 1.206 → 1.206 | 21,163.1 → 20,103.4 (−1,059.8) | yes |
| O_IF 8 → 9 | BPR 10, fan 1.5, LPC 1.8, HPC 12 | 0.6432 → 0.5297 (−17.7 %) | 1.610 → 1.609 | 1.275 → 1.274 | 21,736.7 → 18,519.2 (−3,217.5) | no / yes |
| O_IF 9 → 10 | back to canonical | 0.5297 → 0.6432 | 1.609 → 1.538 | 1.274 → 1.274 | 18,519.2 → 20,869.1 (+2,349.9; AR 10.5 → 11.5 too) | yes |
| O_IF 11 → 12 | BPR 10, fan 1.5, LPC 1.8, HPC 12 | 0.6432 → 0.5297 (−17.7 %) | 1.538 → 1.532 | 1.274 → 1.269 | 20,869.1 → 17,680.6 (−3,188.5) | yes |
| O_IF 12 → 13 **[flawed: B270]** | back to canonical (baseline engine flown, not the carried one) | 0.5297 → 0.6432 | 1.532 → 1.532 | 1.269 → **1.816** | 17,680.6 → 20,767.4 (+3,086.8) | yes |
| O_IF 19 → 20 | BPR 10, fan 1.5, LPC 2.0, HPC 12 | 0.6432 → 0.5214 | — | — | did not fly (failed) | n/a |
| O_GR 12 → 13 | BPR 6.5, fan 1.8 | 0.6432 → 0.5637 (−12.4 %) | 1.793 → 2.318 | 1.481 → 2.000 | 27,159.0 → 32,944.9 (+5,785.9) | yes |
| O_SP 3, 4, 5, 14, 15 | fan/LPC/HPC PR ± 0.005-0.01 | 0.6432 ↔ 0.6448-0.6450 | 1.444-1.449 | 1.282-1.286 | changes of −88 to +80 kg | yes |

SFC percentages = arithmetic on the snippet values. Reading: the two cleanest cases (S_SP 19 → 20 and O_IF 11 → 12:
engine scale, drag factor, CL and geometry unchanged to < 0.5 %) show fuel falling 1,059.8 kg and 3,188.5 kg when SFC
falls 5.7 % and 17.7 %; S_SP 14 → 15 shows the reverse (+4.5 % SFC, +723.3 kg). So propulsion coupling (B3 / B231)
is live in this run. Where scale and drag factor move at the same time (O_GR 13: scale +29 %, drag factor 1.48 → 2.0)
the SFC gain is swamped and fuel rises. O_IF 13 is B270: its fuel change is not a cycle effect (the drag factor and CL
also jumped, 1.269 → 1.816 and 0.558 → 0.641, snippet A-2). n = 9 flown transitions with a cycle change; descriptive
only.

### 3.3 Net / cumulative movement (drift vs oscillation)

`design_params.py -> drift_ratio_pivot.csv` (net / cumulative movement from the baseline to the last iteration;
1 = one-way drift, 0 = back where it started), reversals / moves from `drift.csv`
(`direction_reversals` / `nonzero_moves`), cumulative movement as % of the start value from `cumulative_pct_pivot.csv`.
Old values from report 02 section 4.

| Parameter | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR |
|---|---|---|---|---|---|---|---|---|
| Wing area: ratio (rev./moves) | 0.12 (3/4) — (0.08) | 0.23 (4/9) — (0.07) | 0.83 (2/4) — (0.56) | 1.00* (0/0) — (0.49) | 0.83* (0/0) — (0.91) | 0.99 (0/1) — (0.70) | 0.07 (2/6) — (0.12) | 0.10 (3/13) — (0.35) |
| Wing AR | 0.08 (3/9) — (0.05) | 1.00 (0/6) — (0.96) | 0.27 (1/2) — (0.82) | 1.00* (0/0) — (0.19) | 0.52 (3/7) — (0.22) | 0.99 (0/4) — (0.64) | 0.53 (1/4) — (0.33) | 0.38 (2/11) — (0.00) |
| Wing sweep | 0.44* (0/0) — (0.06) | 0.99* (0/0) — (1.00) | 0.99 (0/1) — (1.00) | 1.00 (0/1) — (0.35) | 1.00 (0/1) — (0.22) | 0.99 (0/1) — (1.00) | 1.00 (0/1) — (0.43) | 0.68 (2/4) — (0.46) |
| Engine scale | 0.40 (10/16) — (0.23) | 0.34 (7/18) — (0.34) | 0.18 (7/16) — (0.05) | 0.19 (12/15) — (0.23) | 0.28 (7/11) — (0.08) | 0.13 (11/14) — (0.07) | 0.38 (4/12) — (0.29) | 0.29 (7/16) — (0.22) |

\* No move above 0.1 %: the ratio is computed on sub-tolerance changes (tigl re-measurement, e.g. O_SP area 122.78 →
122.788, cumulative 0.0065 % of the start) and carries no meaning. The same holds for S_SP / S_IF sweep (cumulative
0.21 % / 0.13 %).

Cumulative movement as % of the start value (`cumulative_pct_pivot.csv`): engine scale 90.5 % (S_IF) to 744.5 % (S_GR)
(old 205.7 % S_IF to 1,455.0 % N_GR); wing area 108.2 % in N_GR, 63.4 % S_IF, 47.1 % S_GR; wing AR 102.8 % N_GR,
81.6 % S_SP (old 124.8 % S_SP, 396.7 % N_GR).

Reading: engine scale oscillates in all 8 (ratio 0.13-0.40, 7-12 reversals). Geometry drifts one way in S_GR (area
122.8 → 75.0 m², ratio 0.83, `end_state.csv`), O_GR (area, AR and sweep each ~0.99: area 122.8 → 110.0 m², AR 9.36 →
11.5, sweep 21.65 → 20.01 °) and S_IF AR (1.00, 9.36 → 13.0). N_GR, N_IF, S_SP and S_IF wing area oscillate (0.07-0.23).

### 3.4 Geometry outside the stated design ranges

Two references, as the source of "stated range" matters:

(a) **Ranges stated to the agents in this run's design table** (the `Range` column of the variables table in each
iteration's `step_log_attempt1.md`, e.g. ws8 `/data/Jessica/l20_final/runs/l20_seq_staged/repeat_000/*/step_log_attempt1.md`
lines 74-112; the same three rows in all step logs grepped across the 7 run folders): wing area 100 to 160 m²;
wing aspect ratio 7 to 17 ("reliable 9 to 11.5"); wing sweep (tigl `sweep` key, default 27.33 °) 15 to 40 °.

(b) **`~/Jessica/avion_analysis/out/agent_bounds.json` on aipexws8** (the file design_params.py was given; dated
2026-10-27, 160 links). This is NOT a stated range: it is the min / max of every value the agents set in the OLD
20-link study (wing area 20.84-304.92 m², AR 2.51-36.28, sweep −77.05 to 23.62 °), and it is what
`reports/design_params/agent_bounds.csv` prints in this run's output (identical numbers, so the bounds table in the new
stdout describes the old run, not this one).

Values of this run: end-of-iteration (`end_state.csv` min / max) and every successful Wing1 morph inside iterations,
including ones that were not flown (`wing_morph_range.csv`).

| Combo | Area end-state min-max (m²) | Area, all morphs | AR end-state | AR, all morphs | Aviary sweep end-state (°) | Tigl sweep, all morphs (°) |
|---|---|---|---|---|---|---|
| S_SP | 115.0-122.8 | 115.0-122.8 | 8.50-11.0 | 8.50-11.0 | 21.65-21.68 | 21.66-21.68 |
| S_IF | **90.0** (it. 13)-122.8 | **90.0**-122.8 | 9.36-**13.0** | 10.0-**13.0** | 21.65-21.68 | 21.66-21.68 |
| S_GR | **75.0** (it. 3-7, 19-20)-122.8 | **75.0**-122.8 | 10.5-**12.0** | 10.0-**13.0** | 21.67 | 21.66-21.68 |
| O_SP | 122.79 | 122.79 | 9.37 | 9.37 | 27.33 | 27.33 |
| O_IF | 122.79 | 122.79 | 9.37-**12.0** | 9.37-**12.0** | 27.33-27.35 | 27.00-27.35 |
| O_GR | 110.0-122.8 | 110.0-122.8 | 9.36-11.5 | 9.36-**12.0** | 20.01-21.65 | 20.01-21.66 |
| N_IF | 115.0-135.0 | 115.0-135.0 | 9.37-11.0 | 9.37-11.5 | 27.33-27.35 | 27.33-27.35 |
| N_GR | 100.0-160.0 (it. 11) | **90.0**-160.0 | 10.0-**13.0** | 9.36-**14.5** | 21.65-26.01 | 21.65-26.01 |

Against (a): **wing area below the stated 100 m² minimum in S_GR on 19 of 20 end states (iterations 2-20: 95.0 at 2,
75.0 at 3-7 and 19-20, 80.0 at 8-18; all flew), in S_IF on 5 end states (95.0 at 9, 11, 12; 90.0 at 13, 14), and
in N_GR inside an iteration only (90.0 m² morph; end states ≥ 100.0)** (per-iteration areas: snippet B).

Snippet B (aipexws8, same invocation as snippet A):

```python
import pandas as pd
L = pd.read_csv("/data/Jessica/l20_final/out/links.csv")
for comb, nm in [("mdo_f25_sequential_graph_routed","S_GR"),("mdo_f25_sequential_iterative_feedback","S_IF")]:
    d = L[L.combination==comb].sort_values("link")
    print(nm, [(int(r.link), round(r["Aircraft.Wing.AREA"],1)) for _, r in d.iterrows()])
```
N_GR reached exactly the 160 m² maximum at iteration 11 ([flawed: B271]). No AR outside 7-17 (max 14.5, N_GR morph);
AR above the "reliable" 11.5 in S_IF, S_GR, O_IF, O_GR, N_GR (bold, max 14.5). Sweep: all values (tigl sweep and aviary
quarter-chord sweep alike) within 15-40 °; the lowest is O_GR 20.01 °. No flagged extremes (sweep < 0, AR > 15, T4 <
2,000 °R): `extremes_flagged.csv` is empty (old: N_GR negative sweep on 12 links, AR > 15 on 11).
Against (b): all of this run's area and AR values lie inside the old run's observed box; the sweep values 23.62-27.35 °
in O_SP, O_IF, N_IF and N_GR (max 27.354 °, O_IF iteration 14) lie above its 23.62 ° maximum.
Whether out-of-range inputs were rejected by the tools is a section 0 item (not checked here); the S_GR / S_IF 75-90 m²
end states show that at least area values below the stated range were accepted and flown.

### 3.5 Spearman ρ between % move and fuel change (consecutive flown pairs)

`design_params.py -> fuel_comovement.csv` (rows `spearman(pct move, dfuel) over moved pairs`): pooled over the 8
combinations; n = consecutive flown pairs (iteration k−1, k) in which the parameter moved (> 0.1 %). Flown consecutive
pairs: 142 (old 123), both missions converged 130 (old 17), neither with a B187 flag 141 (old 104); per combination
S_SP 19, S_IF 19, S_GR 19, O_SP 19, O_IF 18, O_GR 19, N_IF 10, N_GR 19 (stdout).

| Parameter | ρ, all flown pairs (n) | ρ, both converged (n) | Old ρ, all flown (n) |
|---|---|---|---|
| Design CL | 0.84 (58) | 0.84 (53) | 0.25 (79) |
| Drag factor | 0.67 (99) | 0.67 (94) | 0.50 (81) |
| Engine scale | 0.65 (107) | 0.67 (97) | 0.54 (83) |
| Fan PR | 0.51 (13) | 0.42 (11) | |
| LPC PR | 0.45 (12) | 0.36 (10) | −0.04 to 0.15 (14-18), cycle inputs pooled |
| BPR | −0.60 (8) | −0.41 (7) | |
| HPC PR | −0.95 (5) | not computed (4) | |
| Wing area | −0.36 (36) | −0.51 (34) | 0.35 (37) |
| Wing span | −0.34 (64) | −0.42 (59) | 0.28 (61) |
| Wing AR | −0.34 (39) | −0.32 (36) | 0.16 (49) |
| Wing sweep | −0.30 (5) | −0.30 (5) | −0.21 (47) |
| T4, taper, fuselage, wing mass scaler | no moved pairs (0) | | |

Marked n < 5 or ≈ 5: sweep (5), HPC PR (5; 4 converged, ρ not computed by the script), BPR (8; 7). The cycle-input ρ
rest on 1-4 combinations (3.2) and mix confounded pairs. Signs flipped for the geometry: larger wing area, span and
AR now go with lower fuel (ρ −0.34 to −0.36), where the old run had positive ρ. Fuel co-moves most strongly with the
data-plane coupled values (design CL 0.84, drag factor 0.67, engine scale 0.65), which aviary uses directly, so these ρ
are partly mechanical. Pairs are along a chain (not independent); ρ is descriptive, no p-values.

---

## 4. Coordination signatures (Results 4.2, Table 3)

**Conventions for sections 4 and 5.**
- Values are `new (old)`. "Old" = the previous 20-iteration run, reports `03_efficiency.md`, `04_coordination_org.md`,
  `05_recommendations.md`, `06_tools_reliability.md` in `MAS-Aviary-analysis/analysis/link20/reports/`. Where the old
  reports give no value, I write "(old: not reported)".
- Format: `mean ± std (n)`. n = iterations counted. "Per iteration" = per chain iteration (iteration k = link k = `repeat_<k-1>`).
- Sources: `script -> reports/<topic>/<file>`. On aipexws8: `/data/Jessica/l20_final/reports/`.
- Which iterations count:
  - Step-log metrics (agent steps, output tokens, tool calls, board and orchestrator tool counts) count every
    iteration, the failed ones included: n = 20 for all 8 combinations.
  - Metrics from `result.json` (`total_turns`, od.*, `agents_spawned`, duration) exist only for completed iterations:
    O_IF n = 19 (iteration 20 failed) and N_IF n = 16 (iterations 2, 3, 10, 15 failed).
- Fuel and convergence: sections 4 and 5 are process metrics. No number here depends on mission convergence.
- Known-flawed iterations: O_IF 13 (B270), O_GR 5, 7, 8 (B271), N_GR 6 (B272), N_GR 11 (B271), and N_IF 11, 17, 20
  (B272). These flaws are in what reached the mission, not in how the agents coordinated, so these iterations are
  **kept** in every number below. Where one of them stands out on a coordination metric, the text says so.
- Code pins: the networked chains (N_IF, N_GR) ran from the `/data/Avion` worktree at `b985d28`. The other six
  combinations ran on the cb20c60-era pins. Where a metric might differ for that reason, the text says so; see 4.9 and
  "Possible bugs / surprises".
- Thinking was off in every generation of every combination this time: 0 thinking-enabled generations
  (`efficiency.py -> reports/efficiency/generation_coverage.csv`). In the old run, orchestrated workers had thinking on,
  and 67–86 % of orchestrated output tokens came from thinking-enabled generations (03 §2). So old-vs-new token
  comparisons for O_* are not like-for-like.


### 4.1 Effort per iteration and chain totals

| Combo | Agent steps, mean ± std (n=20) | Median steps | Duration h, mean | Median h (n) | Output ktok, mean (n=20) | Chain h, recorded | Chain h incl. failed-iteration span | Earlier-attempt span h |
|---|---|---|---|---|---|---|---|---|
| S_SP | 42.2 ± 3.21 (60.8) | 42.5 (61.5) | 0.374 (0.512) | 0.316 (0.488) (20) | 9.37 (18.27) | 7.48 (10.23) | 7.48 | 0 |
| S_IF | 48.9 ± 8.47 (67.5) | 44.5 (64.5) | 0.312 (0.623) | 0.302 (0.631) (20) | 11.14 (23.28) | 6.24 (12.45) | 6.24 | 0 |
| S_GR | 86.5 ± 35.7 (178) | 76 (159) | 0.641 (1.629) | 0.524 (1.348) (20) | 18.08 (58.05) | 12.81 (32.58) | 12.81 | 0 |
| O_SP | 61.5 ± 2.84 (75.8) | 62 (78.5) | 0.312 (1.171) | 0.314 (1.247) (20) | 12.41 (47.40) | 6.23 (23.42) | 6.23 | 0.91 |
| O_IF | 138 ± 173 (175) | 65 (93) | 0.771 (1.735) | 0.375 (1.419) (19) | 28.51 (74.78) | 14.64, 19 its (31.23, 18 links) | 17.18 | 11.84 |
| O_GR | 102 ± 48.1 (134) | 99 (122) | 1.592 (1.678) | 0.879 (1.549) (20) | 25.82 (91.04) | 31.83 (33.55) | 31.83 | 0 |
| N_IF | 176 ± 55.9 (165) | 176 (172.5) | 2.134 (1.334) | 2.395 (1.340) (16) | 21.75 (28.97) | 34.14, 16 its (26.69) | 42.90 | 46.19 |
| N_GR | 219 ± 135 (359) | 156.5 (393.5) | 2.812 (4.037) | 1.969 (4.572) (20) | 37.42 (77.83) | 56.25 (72.66, 18 links) | 56.25 | 0 |

Sources:
- Steps: `coordination.py -> reports/coordination/common.csv`; `efficiency.py -> reports/efficiency/agent_steps.csv`.
- Duration: `efficiency.py -> duration_hours.csv`. Tokens: `output_ktokens.csv`. Chain totals: `chain_totals.csv`.

Notes:
- "Chain h recorded" is the sum of `duration_s` over iterations that have one.
- "incl. failed-iteration span" adds the step-log span of the failed iterations.
- Earlier runner attempts are not in either column. They add 46.19 h of span to N_IF (14 iterations retried), 11.84 h
  to O_IF (3) and 0.91 h to O_SP (1) (`status_timeouts.csv`).
- Two iterations ran ≥ 480 min of duration: O_GR max 12.45 h and N_GR max 10.25 h. Neither was a counted timeout
  (`timed_out` = 0 everywhere; `status_timeouts.csv`, column `durations_ge_480min` = 1 each). See surprises.
- Outliers that drive the means:
  - O_IF iteration 12: 607 agent steps, 69 `message_orchestrator`.
  - O_IF iteration 16: 405 orchestrator steps, 323 `assign_task`.
  - N_GR iteration 20: 501 agent steps.
  
  Use medians for O_IF and N_GR (`coordination.py -> max_links.csv`).
- Old token figures include thinking tokens for O_*. The new run has none (see conventions).
- Pooled by structure (agent steps, mean ± std, median):
  - sequential 59.2 ± 28.7, median 47 (n=60). Old: 102 ± 62.9.
  - orchestrated 100.7 ± 106.9, median 64 (n=60). Old: 128 ± 115.
  - networked 197.4 ± 104.5, median 161.5 (n=40). Old: 262 ± 134.
  
  Source: `pooled_structure.csv`.
- Pooled by handler (agent steps, mean ± std, median):
  - staged pipeline 51.9 ± 10.2, median 51.5 (n=40). Old: 68.3 ± 11.9.
  - iterative feedback 121.1 ± 116.7, median 66.5 (n=60). Old: 136 ± 118.
  - graph-routed 135.8 ± 102.9, median 112.5 (n=60). Old: 224 ± 127.
  
  Source: `pooled_handler.csv`.
- Agents stepping, mean (n=20): S_SP 7, S_IF 7, S_GR 6.1 (5.3), O_SP 8 (7.95), O_IF 7.85 (7.75), O_GR 6.95 (7.4),
  N_IF 3 (3), N_GR 1.95 (2) (`common.csv`).

### 4.2 Agent MCP calls per iteration and status shares

| Combo | Agent calls / it, mean (median) | Success share | Failed share | Refused share | Failed / it | Refused / it |
|---|---|---|---|---|---|---|
| S_SP | 26.9 (26.5) — old 26.8 (25.0) | 0.976 (0.948) | 0.020 | 0.002 | 0.55 (1.15) | 0.05 (0.25) |
| S_IF | 30.5 (28.5) — old 27.6 (25.0) | 0.948 (0.944) | 0.044 | 0.005 | 1.35 (1.40) | 0.15 (0.15) |
| S_GR | 47.2 (38.5) — old 50.4 (42.5) | 0.893 (0.783) | 0.037 | 0.068 | 1.75 (7.30) | 3.20 (3.65) |
| O_SP | 33.2 (33.0) — old 29.4 (25.5) | 0.932 (0.918) | 0.051 | 0.003 | 1.70 (2.10) | 0.10 (0.25) |
| O_IF | 53.6 (38.5) — old 51.8 (41.0) | 0.802 (0.800) | 0.138 | 0.058 | 7.40 (6.95) | 3.10 (3.35) |
| O_GR | 48.9 (40.5) — old 57.2 (59.0) | 0.830 (0.835) | 0.142 | 0.027 | 6.95 (8.25) | 1.30 (1.15) |
| N_IF | 98.4 (95.0) — old 56.9 (46.5) | 0.588 (0.572) | 0.041 | 0.371 | 4.05 (7.05) | 36.50 (17.25) |
| N_GR | 103.4 (66.5) — old 156.8 (137.5) | 0.848 (0.765) | 0.058 | 0.094 | 6.00 (15.70) | 9.75 (21.00) |

Sources: `tools_reliability.py -> reports/tools_reliability/status_by_combo.csv` (section 4c of its stdout) and
`per_link.csv` (section 1). n = 20 iterations per combination, failed iterations included.

- **All combinations:** 8,843 agent calls: 7,148 success (0.808), 595 failed (0.067), 1,083 refused (0.122) and 17
  unknown (0.002). The 17 unknown are all `sample_surface_solution`.
- **Pre-init calls:** 423, all success, kept separate. Graph-routed only: ~7 per iteration.
- **Calls in earlier runner attempts:** N_IF 968, O_IF 151, O_SP 23. These are not in the table.
- **Old shares:** 06 §3 gives only the success share and per-link means. Old failed and refused shares are not
  listed there, so the table shows them for the new run only.

### 4.3 Refusals by error code

Counts over 20 iterations per combination, from `tools_reliability.py -> refused_by_code.csv`. Old counts from 06 §4 are
in brackets.

| Code | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR | Total |
|---|---|---|---|---|---|---|---|---|---|
| MISSING_REQUIRED_PARAMETERS | 0 (1) | 0 (1) | 0 (0) | 2 (2) | 51 (47) | 1 (1) | 402 (161) | 96 (200) | 552 (413) |
| CLAIMED_BY_ANOTHER_AGENT | 0 | 0 | 0 | 0 | 0 | 0 | 236 (130) | 0 | 236 (130) |
| ALREADY_DONE | 1 (0) | 3 (0) | 26 (17) | 0 (2) | 5 (9) | 21 (13) | 12 (21) | 53 (66) | 121 (128) |
| SESSION_ALREADY_OPEN | 0 | 0 | 36 (56) | 0 | 4 (1) | 1 (5) | 35 (11) | 22 (48) | 98 (121) |
| GEOMETRY_NOT_APPLIED | 0 (2) | 0 | 0 | 0 (1) | 0 (1) | 0 (4) | 28 (17) | 17 (28) | 45 (53) |
| STALE_SU2_REFERENCES | 0 | 0 | 1 | 0 | 1 | 3 | 9 | 7 | 21 (old row not read) |
| MORPH_NOT_EXPORTED | 0 | 0 | 0 | 0 | 0 | 0 | 8 (2) | 0 | 8 (2) |
| AERO_NONPHYSICAL | 0 | 0 | 1 (0) | 0 | 0 | 0 | 0 | 0 (20) | 1 (20) |
| NO_MESH | 0 (2) | 0 (2) | 0 | 0 | 1 (9) | 0 | 0 (2) | 0 | 1 (15) |
| NO_CHANGE_SINCE_FAILED_RUN | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 (51) | 0 (51) |
| CLAIM_FIRST / SESSION_EXISTS | 0 | 0 | 0 | 0 | 0 | 0 | 0 (0 / 1) | 0 (7 / 0) | 0 (8) |

- **Most common refusal code:** `MISSING_REQUIRED_PARAMETERS`, 552 of 1,083 refusals, (413 of the old total).
  - Tools hit: `run_cycle` 283 (98) and `run_simulation` 269 (315).
  - Source: `refused_by_code_and_tool.csv`. That file is pooled over all combinations, so a per-combination
    code × tool split is not in the outputs.
- **Most common code per combination:**
  - S_SP: ALREADY_DONE (1).
  - S_IF: ALREADY_DONE (3).
  - S_GR: SESSION_ALREADY_OPEN (36; all 98 SESSION_ALREADY_OPEN refusals are on `create_cycle_model`).
  - O_SP: MISSING_REQUIRED_PARAMETERS (2).
  - O_IF: MISSING_REQUIRED_PARAMETERS (51).
  - O_GR: ALREADY_DONE (21).
  - N_IF: MISSING_REQUIRED_PARAMETERS (402).
  - N_GR: MISSING_REQUIRED_PARAMETERS (96).
- **CLAIMED_BY_ANOTHER_AGENT (N_IF only):** 236 refusals. They hit `run_simulation` 74, `get_results` 54,
  `run_cycle` 27, `set_aircraft_parameters` 26, `export_cpacs` 15, `check_constraints` 14,
  `generate_volume_mesh` 11, `set_inputs` 9 and others.
- **Failed calls, top codes (not asked, for context):** UNKNOWN_PARAMETER 190, unspecified 214, READ_ONLY_PARAMETER
  135 and NO_RESULTS 52 (N_IF only) (`failed_by_code.csv`).
  - 87 of the 88 failed `run_cycle` calls are "'SFC' / 'SFC_CRUISE' / 'perf.SFC' is not an output of this cycle
    model" (stdout section 6).

### 4.4 Iterative Feedback (S_IF, O_IF, N_IF)

| Metric | S_IF | O_IF | N_IF |
|---|---|---|---|
| Retried turns (`n_retry_messages`) / it | 0.55 ± 0.887 (0.55 ± 0.759) | 2.65 ± 2.70 (2.2 ± 1.54) | 6.1 ± 3.95 (7.3 ± 1.84) |
| Iterations with any retry (of 20) | 6 (8) — its 3, 4, 6, 8, 12, 14 | 17 (16) | 16 (20) |
| Stuck-detection stops / it | 0.2 ± 0.41 (0.1) | 1.05 ± 1.19 (0.75) | 2.45 ± 1.70 (2.5) |
| Iterations with a stuck stop | 4 (2) | 14 (14) | 16 (19) |
| Escalation of commitment (turns) | 0.1 ± 0.308 (n=20) (0.1) | 0.632 ± 0.684 (n=19) (0.278, n=18) | 0.75 ± 0.775 (n=16) (0.5, n=20) |
| Ambidexterity | 0.0496 ± 0.0548 (n=6) (0.00245, n=8) | 0.0296 ± 0.0426 (n=17) (0.0228, n=16) | 0.0487 ± 0.0207 (n=16) (0.0365, n=20) |
| Max runner attempts used | 1 | 3 (it 20, failed) | 3 (its 2, 3, 4, 9, 10, 15) |
| Iterations with > 1 attempt | 0 (1) | 3: its 8, 17, 20 (6) | 14 (5) |

Sources:
- `coordination.py -> iterative.csv` (retries, stuck stops, escalation, ambidexterity).
- `common.csv` (`step_log_attempts`: max 1 / 3 / 3).
- `link_lists.csv` (retry, stuck and multi-attempt lists).
- `efficiency.py -> status_timeouts.csv` (`links_retried`).

Notes on these metrics:
- **Ambidexterity:** defined only where some agent has ≥ 2 messages. S_IF has it on 6 of 20 iterations, which is
  fewer than 5 + 1 points, so read it as small-n. In N_IF it measures peer turn-taking, not retries (same caveat as 04 §3).
- **Not computed:** `mean_attempts_to_success`, per the brief.
- **N_IF retries:** a higher share of N_IF iterations needed a second or third runner attempt, 14 (5).
  - Each extra attempt followed a zero-fuel result (`run_reliability_links.csv`).
  - In N_IF these attempts are the dominant reason the chain took 42.9 h of span plus 46.2 h of earlier attempts.

### 4.5 Graph-Routed (S_GR, O_GR, N_GR)

| Metric | S_GR | O_GR | N_GR |
|---|---|---|---|
| Route length (role runs) / it | 16.0 ± 6.42 (19.1 ± 5.64) | 14.7 ± 7.31 (18 ± 5.63) | 9.75 ± 4.17 (15.1 ± 6.94) |
| Backward moves / it | 2.3 ± 1.13 (4.3 ± 1.08) | 1.1 ± 0.968 (2.55 ± 1.05) | gap (gap) |
| Final level simple / moderate / complex | 0 / 14 / 6 (1 / 10 / 9) | 1 / 13 / 6 (0 / 12 / 8) | 0 / 0 / 20 (0 / 1 / 19) |
| Complexity escalations (`graph_level_changes`) / it | 1.1 ± 0.308 (1.2 ± 0.523) | 0.3 ± 0.47, in 6 its (0.4 ± 0.503) | 1.0 ± 0 (1.3 ± 0.47) |
| `request_complexity` calls / it | 0.05 ± 0.224 (0.35) | 0.2 ± 0.894 (0.25) | 3.9 ± 5.87 (9.9) |
| `set_complexity` (orchestrator) / it | — | 1.15 ± 0.366 (1.4) | — |
| Board reopenings / it | — | — | 1.3 ± 0.47, all 20 its (1.3, 16 links) |
| Stop: COMPLETE | 9: its 4, 6, 7, 8, 10, 11, 12, 18, 20 (0) | 9: its 1, 9, 10, 11, 12, 14, 15, 16, 20 (3) | 0, but see next row (0) |
| Stop: role-run budget | 11: 6 at moderate (15 runs) + 5 at complex (25 runs) (20) | 11: 7 moderate + 4 complex (its 5, 7, 8, 13) (17) | 1 (it 1, complex) (3) |
| Stop: 30-cycle cap | — | — | 1 (it 3) (13) |
| Stop: other | — | — | 1: "every TODO is done and nothing was left to act on" (it 16) |
| Stop: none recorded | 0 (0) | 0 (0) | 17: all others (4) |

Sources: `coordination.py -> graph.csv`, `graph_levels.csv`, `graph_stop_reasons.csv`.

- **Graph-routed iterations finish their route far more often now:** 18 of 60 stopped with COMPLETE (old: 3 of 60).
  - Counting the N_GR "every TODO is done" stop as a completion gives 19 of 60.
- **N_GR stop reasons:** 17 of 20 N_GR iterations record no stop reason (old: 4).
  - So the N_GR stop-reason split cannot be read from these files. N_GR also never stopped on the 30-cycle cap except iteration 3.
  - This may be a `b985d28` recording difference (see surprises).
- **Routing accuracy (1 − misroute rate):** S_GR 0.392 ± 0.156 (0.235), O_GR 0.543 ± 0.297 (0.385), n = 20 each.
  - Not computed for N_GR, where misroute counts peers (`graph.csv`).
- **Flawed O_GR iterations:** O_GR 5, 7 and 8 (B271) are three of the four O_GR iterations that hit the 25-run
  complex budget (the fourth is 13).

### 4.6 Staged Pipeline (S_SP, O_SP)

| Metric | S_SP | O_SP |
|---|---|---|
| Stages per iteration (recorded `stage_count`) | 7 ± 0 (7) | 14 ± 0 (14) — 7 worker + 7 orchestrator turns |
| Stages not meeting criterion / it | 1.6 ± 0.598, ≥ 1 in 20/20 its (1.05 ± 0.224, 20/20) | 0.75 ± 0.444, ≥ 1 in 15/20 (0.3 ± 0.733, 4/20) |
| Stages re-done | 0 (0) | 0 (0) |
| Worker completion rate | 0.771 ± 0.0855, median 0.714 (0.85 ± 0.032) | 0.893 ± 0.0635, median 0.857 (0.957 ± 0.105) |
| Completion rate as recorded | 0.771 ± 0.0854 (0.85) | 0.946 ± 0.0317 (0.979) — includes orchestrator turns |

Source: `coordination.py -> staged.csv` (n = 20 each).

**Aerodynamics criterion: not fixed in S_SP.** I counted per-stage `completion_met` in `result.json` (snippet 2
below), n = 20 iterations each:
- **S_SP:** aerodynamics_analyst is not met in 20/20, simulation_executor in 1/20, mdo_integrator in 11/20, and every
  other stage in 0/20. Total 32, which matches `stages_not_met` sum = 32.
  - The aero stage fails in every S_SP iteration, as in the old run, where 07 §4c noted that the stage-2 criterion
    names `run_simulation` and not `run_su2_solver`.
  - Without the aero stage, S_SP has 12 not-met of 120 worker stages (6 stages × 20), a rate of 0.90 met.
- **O_SP:** mdo_integrator is not met in 15/20 and every other stage in 0/20, aerodynamics included. Total 15, which
  matches `stages_not_met` sum = 15.
  - O_SP uses a different pipeline file (`mdo_f25_staged_pipeline.yaml`), so its aero criterion differs.
- **Not checked:** I did not read the criterion text in the pinned code, so "not fixed" here rests on the
  outcome, not on the source.
- **`early_abort`:** not computed, per the brief.

Snippet 2 (run on aipexws8, read-only; output above):
```python
import json, glob, collections
def walk(o, out):
    if isinstance(o, dict):
        if "stage_name" in o and "completion_met" in o:
            out.append((o["stage_name"], o["completion_met"]))
        for v in o.values(): walk(v, out)
    elif isinstance(o, list):
        for v in o: walk(v, out)
for run, combo in [("l20_seq_staged", "mdo_f25_sequential_staged_pipeline"),
                   ("l20_orch_staged_iter", "mdo_f25_orchestrated_staged_pipeline")]:
    notmet, total, nfiles = collections.Counter(), collections.Counter(), 0
    for f in sorted(glob.glob(f"/data/Jessica/l20_final/runs/{run}/repeat_*/{combo}/result.json")):
        nfiles += 1; out = []; walk(json.load(open(f)), out)
        seen = {}
        for s, m in out: seen.setdefault(s, m)
        for s, m in seen.items():
            total[s] += 1; notmet[s] += (m is False)
    print(run, "files", nfiles, {s: f"{notmet[s]}/{total[s]} not met" for s in total})
```
Output:
- l20_seq_staged, 20 files: aerodynamics_analyst 20/20, simulation_executor 1/20, mdo_integrator 11/20, others 0/20.
- l20_orch_staged_iter, 20 files: mdo_integrator 15/20, others 0/20.

### 4.7 Orchestrated (O_SP, O_IF, O_GR)

| Metric | O_SP | O_IF | O_GR |
|---|---|---|---|
| Orchestrator steps, mean (median) | 21.1 ± 0.308 (21) — old 21.6 (21) | 66.2 ± 122 (16) — old 79.2 (15) | 26.9 ± 10.5 (24) — old 37.9 (31.5) |
| Orchestrator share of agent steps | 0.344 ± 0.0155 (0.289) | 0.315 ± 0.146, median 0.258 (0.297, median 0.204) | 0.308 ± 0.136, median 0.264 (0.284) |
| Orchestrator share of output tokens | 0.223 (0.069) | 0.313, median 0.237 (0.190) | 0.345 (0.128) |
| Worker creation calls (`create_agent`) | 7 ± 0 (6.95) | 9.05 ± 4.73 (8.05) | 7.4 ± 1.57 (8.3) |
| Workers used (`agents_spawned`) | 7 ± 0 (6.95), n=20 | 6.95 ± 0.229 (7), n=19 | 6.15 ± 0.875 (6.4), n=20 |
| Task assignments, median (mean) | 7 (7.05) — old 7 (7.1) | 7 (39.8) — old 7 (54.5) | 9.5 (11.4) — old 11.5 (14.5) |
| Worker → orchestrator messages / it | 0 (0.15) | 9.2 ± 22.7 (10.9) | 1.4 ± 4.9 (3.8) |
| … iterations with any | 0 (1) | 3 (6) | 5 (10) |
| Orchestrator replies (`reply_to_agent`) / it | 0 (0.2) | 6.15 ± 18.6, in 4 its (2.2, 4 links) | 0.45 ± 0.945, in 5 its (2.25, 5 links) |
| `od.cost_per_agent` (s) | 29.3 ± 1.26 (36.8) | 43.2 ± 22.8, n=19 (56.1, n=18) | 115 ± 65.2, median 95.1 (74.2) |

Sources: `coordination.py -> orchestrated.csv`, `efficiency.py -> orchestrator_step_share.csv`,
`orchestrator_output_token_share.csv`. n = 20 unless stated.

- **Token share:** the old orchestrator output-token share was diluted by thinking-enabled workers. The new run has no
  thinking, so the rise in orchestrator token share is at least partly that change.
- **O_IF outliers:**
  - Iteration 12: 69 `message_orchestrator`, 607 agent steps.
  - Iteration 16: 405 orchestrator steps, 323 `assign_task`.
  - Iteration 13 (flawed, B270) is not an outlier on these metrics: 18 worker messages, 2 orchestrator messages
    (`check_orch_token_counts` in coordination stdout).
- **Not computed:** `orchestrator_overhead_ratio` and `information_ratio`, per the brief.

### 4.8 Sequential: propagation time

`od.propagation_time` = last minus first message timestamp, in seconds (`coordination.py -> od_metrics.csv`, n = 20 each):

| | S_SP | S_IF | S_GR |
|---|---|---|---|
| mean ± std | 1,070 ± 622 (1,568 ± 513) | 860 ± 163 (1,954 ± 494) | 2,040 ± 1,140 (5,585 ± 1,867) |
| median | 867 | 814 | 1,533 |

### 4.9 Networked (N_IF, N_GR)

| Metric | N_IF | N_GR |
|---|---|---|
| Peers taking steps / it | 3 ± 0 (3), n=20 | 1.95 ± 0.224 (2), n=20 — iteration with 1 peer: see surprises |
| `spawn_peer` calls | 0.1 / it, in 2 its: 8, 19 (0.2, 4 links) | 0 (0) |
| `claim_todo` calls / it | 22.6 ± 7.9 (19.2) | 30.4 ± 15.4 (42.6) |
| … granted | 10.8 ± 3.84 (9.8) | 21.1 ± 10.6 (30.4) |
| … refused | 11.8 ± 4.56 (9.45) | 9.35 ± 7.27 (12.2) |
| Claim violations (result.json) | 6.88 ± 7.44, n=16, in 16/16 (5.25, 19 links) | 0, n=20 (0) |
| Board waits (`wait_for_board`) / it | 26.6 ± 16.4 (26.1) | 2.7 ± 4.5, in 12 its (6.5) |
| TODOs marked done / it | 8.0 ± 4.05 (9.0) | 10.7 ± 5.0 (16.6) |
| TODOs marked failed / it | 4.95 ± 4.33, in 18 its (5.45, 19) | 1.0 ± 2.13, in 7 its (3.9, 13) |
| `release_todo` | 0 (0) | 0 (0) |
| TODO revisit rate | 0.339 ± 0.203 (0.327), n=20 | 0.629 ± 0.158 (0.774), n=20 |
| Duplicate work rate | 0.262 ± 0.217 (0.179), n=16 | 0.431 ± 0.196 (0.215), n=20 |
| Convergence score mean ± std | 0.280 ± 0.119 (0.219 ± 0.0735), n=16 | 0.357 ± 0.140 (0.272 ± 0.0643), n=20 |
| Convergence score max | 0.6105, it 9 (0.357) | 0.6586, it 3 (0.429) |
| Joint-myopia classification | joint_myopia 1 (it 9), incoherent_team 15, missing 4 (failed its) (old: 0 joint myopia) | joint_myopia 1 (it 3), incoherent_team 19 (old: 0) |

Sources:
- `coordination.py -> networked.csv`, `od_metrics.csv`, `link_lists.csv`.
- Classification: `od.convergence_classification` read from each networked `result.json` on aipexws8 (command below).
  It is not a column of `links.csv`.

```bash
for r in l20c_net_iter l20c_net_graph; do for d in /data/Jessica/l20_final/runs/$r/repeat_0*/; do
  f=$(find $d -maxdepth 2 -name result.json | head -1)
  c=$(grep -o "\"convergence_classification\": *[^,}]*" "$f" 2>/dev/null | head -1)
  echo "$r $(basename $d) ${f:+has} $c"; done; done
```

- **Joint-myopia rule:** the classification follows `org_theory_metrics._convergence_classification`.
  - Convergence ≥ 0.6 is classed `joint_myopia` when eval fails and `effective_consensus` when it succeeds.
  - Below 0.6 it is `incoherent_team` (eval fails) or `healthy_diversity` (eval succeeds).
  - Every completed networked iteration is either `joint_myopia` or `incoherent_team`. So the recorded eval-success
    flag was false on every one of them, including the 15 N_IF and 20 N_GR iterations that flew. The labels therefore
    carry only the 0.6 threshold (see surprises).
- **Code version:** claim violations and board waits differ sharply between N_IF and N_GR.
  - The old report put this partly down to code version: N_IF was on v3combos, N_GR on v4net.
  - In this run both networked chains ran from the same `b985d28` worktree. So the difference is now a handler
    difference, not a code difference.
  - Compared with the six non-networked combinations, any networked-only number may still reflect `b985d28` vs cb20c60-era pins.
- **Not computed:** `claim_conflicts`, per the brief.
- **Estimated values:** blackboard writes/utilisation are still Tier-2 fallback estimates (`check_od_warnings`, 20/20
  N_GR, 16/16 N_IF) and are not reported.

### 4.10 Methodology ordering of median agent steps (Staged Pipeline < Iterative Feedback < Graph-Routed)

Median agent steps per iteration, n = 20 each, from `coordination.py -> common.csv`. Confirmed by snippet 1, part F.

| Structure | SP | IF | GR | Holds? |
|---|---|---|---|---|
| Sequential | 42.5 (61.5) | 44.5 (64.5) | 76 (159) | **Yes**: 42.5 < 44.5 < 76 |
| Orchestrated | 62 (78.5) | 65 (93) | 99 (122) | **Yes**: 62 < 65 < 99 |
| Networked (no SP) | — | 176 (172.5) | 156.5 (393.5) | **No**: IF > GR (old: IF < GR) |

- **Sequential and orchestrated:** the ordering still holds, but the SP–IF gap is small (2 and 3 steps).
- **Networked:** the ordering now reverses on medians.
  - By mean it still holds: N_IF 176 < N_GR 219, driven by N_GR iterations 20 (501 steps) and others above 300.
  - N_GR's median halved, 393.5 → 156.5, while N_IF barely moved.
  - Only N_GR fell this much, and N_GR also changed code pin (v4net → `b985d28`).
- **Ordering by mean:** O_IF (138) > O_GR (102), the same as in the old run, because of the O_IF outliers.

---

## 5. Behavior over the chain (Results 4.3)

### Method (how 03/04/06 compute it, reproduced)

**`coordination.py` `trends()`, used for 04 §9:**
- For each combination, every metric in `TREND` is tested.
  - `TREND` = agent_steps, total_turns, n_agents_stepping, step_log_attempts, n_retry_messages, stuck_detected,
    route_length, backward_moves, graph_level_changes, orchestrator_steps, n_assign_task, n_message_orchestrator,
    n_claim_granted, n_claim_refused, n_wait_for_board, n_mark_todo_failed, od.todo_revisit_rate,
    od.completion_rate_workers, od.ambidexterity_score, od.misroute_rate, od.orchestrator_overhead_ratio,
    od.convergence_score, od.duplicate_work_rate.
- The test is `scipy.stats.spearmanr(link, metric)`. It is skipped when there are fewer than 5 non-null values or
  fewer than 2 distinct values.
- The means of iterations 1–10 and 11–20 are reported alongside.
- p is uncorrected.

**`efficiency.py` §6, used for 03 §6:**
- For hours, output ktok and agent steps (plus 3 more), rho = Pearson on ranks and p = `spearmanr` p.
- Means of iterations 1–5 and 16–20 are reported alongside.

**`tools_reliability.py` §11, used for 06 §10:**
- rho of agent failed and refused counts per iteration vs iteration (rank correlation, no p printed).
- Means of 1–10 vs 11–20 reported alongside.
- A pooled rho is computed over all 160 iterations.
- The agent failed/refused counts equal `links.kb_failed` / `kb_refused`; the script checks this and prints `True`.

**Snippet 1 (run on aipexws8).** It re-does these from `/data/Jessica/l20_final/out/links.csv`. Its output matched
the script CSVs to every printed digit.

```bash
ssh -i ~/.ssh/voir_shard -o BatchMode=yes aipexws8@172.24.49.250 \
  'cd ~/Jessica/avion_analysis/code && LINK20_STUDY=final ~/Jessica/Avion/.venv/bin/python -' < s4s5_snippet.py
```
```python
import pandas as pd, numpy as np
from scipy.stats import spearmanr
from analysis.link20.reports.scripts.coordination import load, TREND, ORDER
df = load("/data/Jessica/l20_final/out/links.csv")
# (A) coordination.trends() re-done verbatim
rows = []
for ab in ORDER:
    g = df[df.ab == ab]
    for c in TREND:
        d = g[["link", c]].apply(pd.to_numeric, errors="coerce").dropna()
        if len(d) < 5 or d[c].nunique() < 2:
            continue
        rho, p = spearmanr(d.link, d[c])
        rows.append((ab, c, len(d), rho, p))
t = pd.DataFrame(rows, columns=["combo", "metric", "n", "rho", "p"])
print("A tests", len(t), "p<0.05", int((t.p < 0.05).sum()))
print(t[t.p < 0.05].to_string(index=False))
print("A tests per combo", t.groupby("combo").size().to_dict())
# (B) efficiency trend (hours, ktok, agent_steps): rho/p vs link, means of links 1-5 and 16-20
df["hours"] = df.duration_s / 3600; df["ktok"] = df.output_tokens / 1000
for ab in ORDER:
    g = df[df.ab == ab]
    out = [ab]
    for col in ("hours", "ktok", "agent_steps"):
        m = g[["link", col]].dropna()
        r = spearmanr(m.link, m[col])
        e, l = g[g.link <= 5][col].dropna(), g[g.link >= 16][col].dropna()
        out.append(f"{col}: rho={r.statistic:.3f} p={r.pvalue:.3f} n={len(m)} 1-5={e.mean():.3f}(n={len(e)}) 16-20={l.mean():.3f}(n={len(l)})")
    print("B", " | ".join(out))
# (C) failed / refused agent calls per iteration
for ab in ORDER + ["ALL"]:
    g = df if ab == "ALL" else df[df.ab == ab]
    s = []
    for col in ("kb_failed", "kb_refused"):
        r = spearmanr(g.link, g[col])
        s.append(f"{col}: rho={r.statistic:.3f} p={r.pvalue:.3f} n={len(g)} 1-10={g[g.link<=10][col].mean():.2f} 11-20={g[g.link>10][col].mean():.2f}")
    print("C", ab, " | ".join(s))
# (D) convergence score per iteration, N_IF and N_GR
for ab in ["N_IF", "N_GR"]:
    g = df[df.ab == ab].sort_values("link")
    print("D", ab, [(int(l), None if pd.isna(v) else round(v, 4), s) for l, v, s in zip(g.link, g["od.convergence_score"], g.run_status)])
    m = g[["link", "od.convergence_score"]].dropna(); r = spearmanr(m.link, m["od.convergence_score"])
    print("D", ab, f"rho={r.statistic:.3f} p={r.pvalue:.3f} n={len(m)} mean={m['od.convergence_score'].mean():.4f} std={m['od.convergence_score'].std():.4f} max={m['od.convergence_score'].max():.4f} n>=0.6={(m['od.convergence_score']>=0.6).sum()}")
# (E) verdict counts per combination and last_verdict grid
print("E", df.groupby("ab")[["verdict_complete", "verdict_continue", "verdict_retry"]].sum().loc[ORDER].to_string())
print("E last_verdict", df.pivot_table(index="ab", columns="link", values="last_verdict", aggfunc="first").reindex(ORDER).fillna("-").to_string())
# (F) per-combo median agent steps (4.10)
print("F", df.groupby("ab").agent_steps.median().loc[ORDER].to_dict())
```
(Full file: aipexws8 `/data/Jessica/l20_final/reports/snippets/s4s5_snippet.py`.)

### 5.1 Spearman rho of each coordination metric vs iteration, per combination

- **Tests:** 68 (old: 72). n = 20 per test, except the result.json metrics:
  - O_IF: 19, or 17 for ambidexterity.
  - N_IF: 16.
  - S_IF ambidexterity: 6 (small n).
- **Tests per combination:** S_SP 2, S_IF 5, S_GR 8, O_SP 5, O_IF 11, O_GR 12, N_IF 13, N_GR 12.
- **p < 0.05:** 5 of 68 (old: 1 of 72, O_SP agent steps). Chance alone would give 68 × 0.05 = 3.4. Uncorrected.

| Combo | Metric | n | rho | p | mean its 1–10 → 11–20 |
|---|---|---|---|---|---|
| O_IF | orchestrator_steps | 20 | +0.517 | 0.0196 | 17 → 115.5 |
| O_GR | agent_steps | 20 | −0.597 | 0.0055 | 126.1 → 78.6 |
| O_GR | route_length | 20 | −0.447 | 0.0483 | 17.7 → 11.7 |
| O_GR | n_message_orchestrator | 20 | −0.491 | 0.0278 | 2.7 → 0.1 |
| O_GR | od.orchestrator_overhead_ratio | 20 | +0.449 | 0.0470 | 0.0677 → 0.1135 |

Source: `coordination.py -> reports/coordination/trends.csv`, `test_counts.csv`. Reproduced by snippet 1 part A.

Notes:
- `od.orchestrator_overhead_ratio` is in the script's TREND list. The brief says not to compute it, so I list it
  only because it is one of the 5 hits; it should not be reported as a finding. Without it, 4 of 67 tests reach p < 0.05.
- O_GR is the only combination with a coherent trend. Effort falls along the chain:
  - agent steps, route length and worker → orchestrator messages all fall;
  - turns (rho −0.434, p 0.056), agents stepping (−0.432, 0.057), retries (−0.432, 0.057) and misroute rate
    (−0.429, 0.059) fall too, just above 0.05.
- O_GR iterations 1–8 hold three of the B271-flawed iterations (5, 7, 8) and the longest routes. Iteration 1 has
  207 steps, the maximum (`max_links.csv`).
- The O_IF orchestrator-step trend is carried by two late outliers, iterations 12 and 16. The medians do not show it.

### 5.2 Duration, output tokens and agent steps over the chain

| Combo | rho hours (p), n | hours its 1–5 → 16–20 | rho ktok (p), n=20 | ktok 1–5 → 16–20 | rho steps (p), n=20 | steps 1–5 → 16–20 |
|---|---|---|---|---|---|---|
| S_SP | −0.089 (0.71), 20 — old 0.12 (0.61) | 0.334 → 0.429 (0.49 → 0.54) | −0.340 (0.14) — old 0.06 (0.80) | 10.24 → 9.02 (17.8 → 19.5) | −0.257 (0.27) — old 0.36 (0.12) | 44.0 → 40.4 (59.8 → 65.6) |
| S_IF | −0.144 (0.54), 20 — old 0.24 (0.30) | 0.326 → 0.290 (0.59 → 0.61) | −0.118 (0.62) — old 0.22 (0.34) | 11.77 → 10.05 (22.5 → 22.9) | −0.397 (0.083) — old 0.31 (0.19) | 52.2 → 42.6 (62.2 → 70.0) |
| S_GR | −0.125 (0.60), 20 — old −0.29 (0.22) | 0.855 → 0.616 (2.09 → 1.73) | −0.090 (0.71) — old −0.25 (0.29) | 23.28 → 17.91 (68.8 → 59.5) | −0.108 (0.65) — old −0.23 (0.32) | 111.2 → 84.0 (221.0 → 194.2) |
| O_SP | −0.093 (0.70), 20 — old −0.20 (0.41) | 0.312 → 0.298 (1.44 → 1.13) | −0.056 (0.82) — old −0.18 (0.45) | 12.30 → 11.79 (57.7 → 45.5) | −0.058 (0.81) — old 0.49 (0.028) | 61.6 → 61.0 (70.6 → 78.4) |
| O_IF | +0.098 (0.69), 19 — old −0.21 (0.40) | 0.341 → 1.260 (n=5 → **4**) (1.57 → 1.84) | +0.194 (0.41) — old −0.01 (0.98) | 14.82 → 45.06 (63.7 → 105.2) | +0.251 (0.29) — old 0.08 (0.75) | 61.0 → 226.2 (87.8 → 294.6) |
| O_GR | **−0.549 (0.012)**, 20 — old 0.14 (0.56) | 3.384 → 0.721 (1.75 → 1.83) | **−0.531 (0.016)** — old 0.22 (0.35) | 28.93 → 19.20 (102.2 → 104.4) | **−0.597 (0.005)** — old 0.06 (0.81) | 129.0 → 70.0 (123.8 → 140.2) |
| N_IF | −0.488 (0.055), **16** — old −0.04 (0.87) | 2.844 → 1.947 (n=**3** → 5) (1.35 → 1.43) | −0.101 (0.67) — old 0.06 (0.80) | 22.69 → 22.63 (26.0 → 28.6) | +0.002 (0.99) — old −0.08 (0.72) | 184.0 → 190.2 (164.4 → 164.4) |
| N_GR | +0.250 (0.29), 20 — old 0.27 (0.28) | 2.788 → 4.455 (3.96 → 4.59) | +0.238 (0.31) — old 0.20 (0.40) | 37.29 → 46.44 (87.6 → 82.5) | +0.238 (0.31) — old 0.39 (0.09) | 228.2 → 293.4 (341.0 → 421.0) |

Source: `efficiency.py -> reports/efficiency/trend_over_chain.csv`. Reproduced by snippet 1 part B. Means of iterations
1–5 and 16–20 have n = 5 each, except those in bold (n < 5).

- **Significant trends:** only O_GR moves significantly. It gets faster, cheaper and shorter along the chain:
  3.38 h → 0.72 h, 28.9 → 19.2 ktok and 129 → 70 steps.
  - Its iteration 1–5 mean duration is inflated by the 12.45 h maximum. The iteration is not named in the printed
    outputs; `duration_hours.csv` gives only the max.
- **O_IF:** the late rise comes from iterations 12 and 16. 16–20 duration has n = 4 because iteration 20 failed.
- **N_IF:** iteration 1–5 duration has n = 3 because iterations 2 and 3 failed. It is marked n < 5.
- **Other metrics:** from the same script, tokens per minute trends in S_SP (rho −0.708, p < 0.001) and N_IF (+0.691,
  p 0.003). The largest prompt grows in N_GR (+0.46, p 0.041) and shrinks in S_GR (−0.525, p 0.018). These are
  outside the spec items. Uncorrected for 48 tests (8 combos × 6 metrics), as in 03.

### 5.3 Failed and refused agent calls per iteration

| Combo | Failed its 1–10 → 11–20 | rho failed (p) | Refused its 1–10 → 11–20 | rho refused (p) | old rho failed / refused |
|---|---|---|---|---|---|
| S_SP | 0.7 → 0.4 | −0.009 (0.97) | 0.1 → 0.0 | −0.179 (0.45) | −0.126 / 0.157 |
| S_IF | 1.3 → 1.4 | −0.189 (0.43) | 0.3 → 0.0 | −0.342 (0.14) | 0.249 / 0.298 |
| S_GR | 1.7 → 1.8 | −0.027 (0.91) | 3.1 → 3.3 | +0.115 (0.63) | 0.325 / −0.182 |
| O_SP | 1.7 → 1.7 | +0.352 (0.13) | 0.0 → 0.2 | +0.338 (0.15) | 0.193 / −0.306 |
| O_IF | 5.8 → 9.0 | +0.275 (0.24) | 0.4 → 5.8 | **+0.473 (0.035)** | 0.158 / 0.024 |
| O_GR | 9.0 → 4.9 | **−0.521 (0.019)** | 1.8 → 0.8 | **−0.469 (0.037)** | 0.277 / 0.062 |
| N_IF | 4.3 → 3.8 | −0.188 (0.43) | 46.8 → 26.2 | −0.426 (0.061) | 0.237 / −0.222 |
| N_GR | 3.1 → 8.9 | +0.355 (0.12) | 7.7 → 11.8 | +0.259 (0.27) | 0.267 / 0.312 |
| **Pooled, 160 its** | 3.45 → 3.99 | −0.006 (0.94) | 7.53 → 6.01 | +0.010 (0.90) | 0.157 / 0.017 |

Sources:
- rho and the 1–10 / 11–20 means: `tools_reliability.py -> reports/tools_reliability/trend_over_links.csv` and its
  stdout section 11 (n = 10 + 10 per combination).
- p values: snippet 1 part C (`links.kb_failed` / `kb_refused`, identical counts). `tools_reliability.py` prints no p.
- Pooled 1–10 / 11–20 means: snippet 1 part C.
- Old values from 06 §10.

Readings:
- **Old vs new:** in the old run failures crept up in every combination. Now the pooled trend is flat in both
  failures and refusals (|rho| ≤ 0.01).
- **O_GR:** gets cleaner along the chain, with failed and refused calls both falling (p < 0.05).
- **O_IF:** refusals rise, 0.4 → 5.8. Iteration 20 (failed) has 45 failed + refused calls, and iteration 12 has 45 too
  (`failed_plus_refused_per_link_wide.csv`).
- **N_IF:** refusals fall from 46.8 to 26.2 per iteration (p 0.061).
- **Tests:** 18 tests here (16 per combination plus 2 pooled). 3 are below 0.05, all uncorrected.

### 5.4 Convergence score per iteration (N_IF, N_GR)

`od.convergence_score` from `links.csv` (snippet 1 part D). "—" = failed iteration, which has no `result.json`.

| It | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| N_IF | 0.2801 | — | — | 0.2068 | 0.2944 | 0.1369 | 0.2894 | 0.2108 | **0.6105** | — | 0.3224 | 0.3267 | 0.3259 | 0.1924 | — | 0.4624 | 0.2440 | 0.1879 | 0.2116 | 0.1746 |
| N_GR | 0.1809 | 0.4986 | **0.6586** | 0.4545 | 0.5306 | 0.3602 | 0.2969 | 0.4587 | 0.2901 | 0.2007 | 0.3021 | 0.1091 | 0.3882 | 0.2096 | 0.1749 | 0.4896 | 0.3849 | 0.4241 | 0.4231 | 0.3071 |

- **Trend:** no trend in either chain.
  - N_IF: rho −0.126, p 0.64 (n = 16). Mean 0.2798 ± 0.1191, max 0.6105.
  - N_GR: rho −0.189, p 0.42 (n = 20). Mean 0.3571 ± 0.1404, max 0.6586.
  - Means 1–10 → 11–20 (`trends.csv`): N_IF 0.290 (n=7) → 0.272 (n=9), N_GR 0.393 → 0.321 (n=10 each).
- **Threshold:** one iteration in each chain crosses the 0.6 joint-myopia threshold (N_IF 9, N_GR 3). In the old run
  none did: max 0.357 and 0.429 (04 §6).
- **Old values:** 04 does not list the old per-iteration values.
- **Flawed iterations:** N_IF 11, 17, 20 and N_GR 6, 11 (flawed) are unremarkable here.

### 5.5 Integrator verdicts

Counts per combination (20 iterations each):
- `summary_per_combination.csv`: `recommendations_report.py -> reports/recommendations/summary_per_combination.csv`.
- "All": per-iteration fields `verdict_complete` / `verdict_continue` / `verdict_retry` of `links.csv`, summed by
  snippet 1 part E.

| Combo | Iterations without a recommendation | Carried (last) C / N / R | All recorded C / N / R | Iteration-20 verdict |
|---|---|---|---|---|
| S_SP | 0 (0) | 0 / 20 / 0 (0 / 19 / 1) | 0 / 20 / 0 (0 / 141 / 6) | CONTINUE (CONTINUE) |
| S_IF | 0 (0) | 0 / 20 / 0 (0 / 18 / 2) | 0 / 20 / 0 (0 / 124 / 19) | CONTINUE (CONTINUE) |
| S_GR | 0 (4) | 0 / 20 / 0 (0 / 16 / 0) | 0 / 43 / 0 (0 / 279 / 0) | CONTINUE (CONTINUE) |
| O_SP | 0 (1) | 2 / 18 / 0 (1 / 9 / 9) | 2 / 18 / 0 (2 / 99 / 164) | CONTINUE (none) |
| O_IF | 2: its 10, 20 (1) | 6 / 12 / 0 (1 / 11 / 7) | 8 / 13 / 0 (3 / 166 / 155) | none, iteration failed (CONTINUE) |
| O_GR | 4: its 9, 12, 14, 20 (1) | 2 / 14 / 0 (0 / 14 / 5) | 2 / 22 / 0 (0 / 33 / 16) | none (CONTINUE) |
| N_IF | 2: its 2, 3, both failed (1) | 0 / 18 / 0 (0 / 18 / 1) | 0 / 18 / 0 (0 / 26 / 3) | CONTINUE (CONTINUE) |
| N_GR | 0 (4) | 0 / 18 / 2 (0 / 11 / 5) | 0 / 24 / 2 (0 / 18 / 8) | RETRY (CONTINUE) |

Carried verdict per iteration (8 × 20 grid). C = COMPLETE, N = CONTINUE, R = RETRY, - = no recommendation recorded.
Source: `recommendations_report.py -> reports/recommendations/last_verdict_per_link.csv`, identical to snippet 1 part E.

| | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S_SP | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N |
| S_IF | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N |
| S_GR | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N |
| O_SP | N | N | N | N | N | C | N | N | N | N | N | N | N | N | N | C | N | N | N | N |
| O_IF | N | N | C | N | C | N | N | N | N | - | C | N | N | C | C | N | N | C | N | - |
| O_GR | N | N | N | N | N | N | N | N | - | N | N | - | N | - | C | C | N | N | N | - |
| N_IF | N | - | - | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N |
| N_GR | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | N | R | N | N | R |

Old grid, from 05 §1:

| | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S_SP | N | N | N | N | N | N | N | N | N | N | N | R | N | N | N | N | N | N | N | N |
| S_IF | R | N | N | N | N | N | N | R | N | N | N | N | N | N | N | N | N | N | N | N |
| S_GR | N | N | N | N | N | N | - | N | N | N | N | N | - | - | - | N | N | N | N | N |
| O_SP | R | R | R | R | R | R | N | C | N | N | N | N | N | N | R | R | N | R | N | - |
| O_IF | R | R | R | R | R | N | R | - | N | N | N | N | R | N | N | N | C | N | N | N |
| O_GR | R | R | R | R | R | N | N | N | - | N | N | N | N | N | N | N | N | N | N | N |
| N_IF | N | N | N | N | N | N | N | N | N | N | N | N | N | R | N | N | N | N | - | N |
| N_GR | R | N | N | N | N | R | N | - | R | - | - | N | N | N | N | N | R | N | R | N |

Readings:
- **RETRY:** it has almost vanished. 2 carried RETRY in 160 iterations, both N_GR (iterations 17 and 20); the old run had 30.
  - The old opening runs of RETRY in O_SP 1–6, O_IF 1–5 and O_GR 1–5 are gone.
- **COMPLETE:** it is more frequent, 10 carried (old: 2). All 10 are in orchestrated combinations: O_SP 2, O_IF 6, O_GR 2.
  - The sequential and networked integrators never say COMPLETE.
- **Recommendation loop:** the old record_recommendation loop is gone. "All recorded" verdicts are now close to one
  per iteration: S_GR 43 in 20 iterations, versus 279 in the old run.
- **Flawed iteration:** O_IF 13 is CONTINUE.

---

## 6. Recommendations and the ladder (Results 4.4, Table 4)

Values are shown as `new (old)`. "New" is the 20-iteration re-run (155 completed iterations; outputs in
aipexws8 `/data/Jessica/l20_final/reports/`). "Old" is the previous run's reports
(`MAS-Aviary-analysis/analysis/link20/reports/05_recommendations.md`, `07_methods_answers.md` Q7 and
`08_iterations_and_ladder.md`).

**Scoring definition.** All scores use the single definition in `07_methods_answers.md` Q7 ("Proposed single
definition"), implemented as `rec_scoring_reconcile.py: proposed()`:
- **Item.** One parameter change parsed from the recommendation that iteration k carried forward. It is scored on
  the change from v0 (end of k) to v1 (end of k+1).
- **Wanted direction.** sign(target − v0) for value items; towards [lo, hi] for range items; towards the stated side
  for bound items; the stated up/down for direction items.
- **Already met** (excluded). The target is within 0.1 % of v0.
- **Moved.** The relative change is more than 0.1 %.
- **Reached.** v1 is within 1 % of the target.

The script reproduces both of the older scorings exactly ("02 reproduces rec_vs_actual.csv: True | 05 reproduces
follow_through_items.csv: True", `rec_scoring_reconcile -> reports/rec_scoring_reconcile/stdout.txt`).

**Known-flawed iterations** that touch these numbers are listed under items 3 and 6: O_IF 13, O_GR 5/7/8, N_GR 6/11
and N_IF 11/17/20. None was dropped.

---

### 6.1 Carried recommendations, parameter-naming recommendations, parsed items

Source: `recommendations_report -> reports/recommendations_report/stdout.txt` §6a. It covers iterations 1–19, whose
recommendation is carried to an iteration k+1 that exists.

| | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR | **All** |
|---|---|---|---|---|---|---|---|---|---|
| Carried recs with text | 19 (19) | 19 (19) | 19 (15) | 17 (18) | 12 (17) | 14 (18) | 17 (18) | 19 (15) | **136 (139)** |
| Naming ≥ 1 plotted parameter | 15 (18) | 17 (10) | 4 (8) | 0 (12) | 7 (10) | 6 (12) | 17 (10) | 19 (15) | **85 (95)** |
| Parsed items | 15 (21) | 17 (10) | 4 (8) | 0 (15) | 7 (12) | 6 (13) | 17 (11) | 19 (26) | **85 (116)** |

- "Items in recommended_changes.csv: 85; fresh parse of last_rec_change (links 1-19): 85" (same stdout).
- In this run every parameter-naming recommendation parsed to exactly one item (85 = 85). In the old run it was
  95 recommendations and 116 items.
- O_SP's 17 carried recommendations name no plotted parameter, so O_SP has 0 items.
  - Its modal recommendation is "Reduce gross takeoff weight (GROSS_MASS_KG) by optimizing payload or reducing
    structural mass" (§7 of the same stdout).

### 6.2 Already met, no recorded outcome, scored

Source: `rec_scoring_reconcile -> reports/methods_q7/counts_proposed.csv` (and the stdout).

| | new (old) |
|---|---|
| Parsed items | 85 (116) |
| Already met (excluded) | 2 (18) |
| No recorded outcome (excluded) | 6 (6) |
| **Scored** | **77 (92)** |
| Followed / unchanged / opposite | 58 / 13 / 6 (57 / 27 / 8) |

Check: 77 + 2 + 6 = 85.

The 2 already-met items:
- S_GR 4: design CL target 0.558 with v0 0.557554.
- N_GR 2: AR target 11.0 with v0 11.0.

The old run had 18 already-met items, 11 of them in N_GR, where the integrator recorded values that had already
been applied.

The 6 items with no recorded outcome are all next to a failed or not-flown iteration:
- O_IF 20 (AR).
- N_IF 2 (area), 10 (area), 11 (area), 15 (AR) and 16 (AR). N_IF iterations 2, 3, 10, 15 and 16 have a missing
  end-state on one side.

(`recommendations_report` stdout §6b, "not measurable items".)

### 6.3 Actionable vs not actionable

**Variable lists.** I used the same lists as `08` (`ladder_and_table.py: PROMPT_RANGES / NOT_ACTIONABLE`). The
script is byte-identical on ws8 and locally (`diff` → no difference).
- **Actionable.** Wing area 100–160 m², AR 7–17, sweep 15–40°, BPR ≤ 10, T4 ≥ 2,857 °R, fan PR 1.4–1.7, HPC PR ≤ 30.
- **Not actionable.** Engine scale factor, wing mass scaler and design CL are "set by framework". Taper "follows
  geometry".

**Did the variable set change in this run?** Mostly no; three things differ. I compared the GLOBAL DESIGN SPACE
table in this run's prompts with the old one (`MAS-Aviary@117b55e:config/mdo_f25_sequential_agents.yaml:29-71`).
- **Unchanged.** All seven agent-set ranges are the same, and so are the framework-set and follows-geometry rows.
  Taper is still "Derived | tigl geometry … | follows geometry".
- **Changed 1 (prompt rows).** The re-run table adds two "Set" rows:
  - `T4_TAKEOFF` (range "up to 3640");
  - `T4_CLIMB` ("not set").
- **Changed 2 (wing mass).** "Wing mass scaler → `Aircraft.Wing.MASS_SCALER`" is replaced by "Wing mass |
  framework: mass result to aviary `Aircraft.Wing.MASS` | set by framework".
- Neither change touches a scored item:
  - No recommendation named T4_TAKEOFF or T4_CLIMB.
  - `Aircraft.Wing.MASS_SCALER` is no longer among the plotted parameters (`recommendations_report` stdout §6b2
    lists 15 parameters).
- **Changed 3 (formatting only).** The sequential and orchestrated tables differ only in unit text (m² vs m^2,
  °R vs degR). In the networked runs the table is not in `step_log_attempt1.jsonl`; it sits in `trace.json` (1,077
  matches of "100 to 160" in `l20c_net_graph/repeat_010`). I did not diff it.
- **Taper.** Taper is unchanged: 0 of 144 transitions (`recommendations_report` stdout §6b2), and 0 taper items
  were parsed in this run (old: 6, all unchanged).
- **Sweep note.** The re-run prompt calls aviary's `Aircraft.Wing.SWEEP` "Wing quarter-chord sweep | follows
  geometry", but the ladder script treats it as actionable (15–40°). No sweep item is scored in this run (old: 1),
  so this has no effect here.

Commands used: on ws8, the GLOBAL DESIGN SPACE table extracted from
`runs/*/repeat_010/*/step_log_attempt1.jsonl`, md5-compared and `difflib`-diffed. Locally, `git show
117b55e:config/mdo_f25_sequential_agents.yaml`.

Split (`ladder_and_table -> reports/ladder/split_by_param.csv`, stdout "not actionable scored 7 followed 3 /
actionable scored 70 followed 55"):

| Group | Scored | Followed | Unchanged | Opposite |
|---|---|---|---|---|
| Actionable | **70 (65)** | **55 (51)** | 12 (11) | 3 (3) |
| Not actionable | **7 (27)** | **3 (6)** | 1 (16) | 3 (5) |
| **All** | **77 (92)** | **58 (57)** | 13 (27) | 6 (8) |

Per variable (followed / unchanged / opposite):

| Variable | new | old |
|---|---|---|
| Wing area | 29 / 8 / 3 | 21 / 3 / 1 |
| Wing AR | 24 / 4 / 0 | 29 / 2 / 0 |
| BPR | 2 / 0 / 0 | 1 / 3 / 0 |
| HPC PR, fan PR, sweep, T4 | none scored | 0/2/0, 0/1/0, 0/0/1, 0/0/1 |
| Engine scale factor (n.a.) | 0 / 1 / 2 | 5 / 8 / 5 |
| Design CL (n.a.) | 3 / 0 / 1 | none scored |
| Wing mass scaler, taper (n.a.) | none | 1/2/0, 0/6/0 |

**Were the 3 "followed" not-actionable items moved by an agent or by the framework? By the framework.** All three
are design-CL items in O_GR: iteration 7 (target 0.58, value 0.5498 → 0.5575), iteration 18 (direction down,
0.7156 → 0.6239) and iteration 20 (direction down, 0.7156 → 0.6239).
- In each of these iterations, the end-state `Mission.Design.LIFT_COEFFICIENT` equals the SU2 trimmed cruise CL
  that the framework injects. `design.json` `aero.injected_into_aviary.LIFT_COEFFICIENT` = `aero.cl_cruise` =
  0.5574667052 (iteration 7), 0.6238746855 (iterations 18 and 20).
- The CL in every `set_aircraft_parameters` call (made by `mission_architect` / `simulation_executor`) is that
  10-digit SU2 value, not a rounded agent target. No call carries 0.58.
- The only agent attempt to act on CL is in iteration 19, not 18: `aerodynamics_analyst` sets the SU2 config
  `LIFT_COEFFICIENT: "0.58"` via `update_config_entries` (`repeat_018/.../tool_calls_attempt1.jsonl` line 27).
  - SU2 then exits with code 1 (config error, line 28).
  - The agent removes the key (line 29).
- Iteration 7 is itself a known-flawed iteration (B271: SU2 solved the previous shape's mesh), so its CL move
  comes from a stale SU2 solve.
- The old run's 6 followed not-actionable items were also framework moves (`08` §2a). Neither run has an agent-made
  "followed" move on a not-actionable variable.

Snippet (ws8, read-only):
```python
import json, glob
base="/data/Jessica/l20_final/runs/l20_orch_graph"
for r in ["005","006","016","017","018","019"]:
    d=glob.glob(f"{base}/repeat_{r}/*/")[0]
    for f in sorted(glob.glob(d+"tool_calls_attempt*.jsonl")):
        for i,line in enumerate(open(f)):
            c=json.loads(line)
            if c.get("tool")!="set_aircraft_parameters": continue
            p=(c.get("args") or {}).get("parameters") or {}
            print(f, i+1, c["agent"], p.get("Mission.Design.LIFT_COEFFICIENT"))
    a=json.load(open(d+"design.json"))["aero"]; print(a["injected_into_aviary"], a["cl_cruise"])
```
(The O_GR iteration 19 `update_config_entries` lines came from `grep -n LIFT_COEFFICIENT` on the same files.)

### 6.4 Feasible: actionable items vs the prompt's stated range

Source: `ladder_and_table -> reports/ladder/feasibility.csv`.

| Scored items | n | Followed / unchanged / opposite |
|---|---|---|
| Actionable, target **inside** range | **36 (34)** | 33 / 3 / 0 (29 / 4 / 1) |
| Actionable, target **outside** range | **4 (1)** | 4 / 0 / 0 (0 / 0 / 1) |
| Actionable, direction only | 30 (30) | 18 / 9 / 3 (22 / 7 / 1) |
| Not actionable (no agent range) | 7 (27) | 3 / 1 / 3 (6 / 16 / 5) |
| **Total** | **77 (92)** | 58 / 13 / 6 (57 / 27 / 8) |

36 of the 40 actionable items that state a target are inside the range (old: 34 of 35).

**The 4 outside items are all wing-area targets below the prompt's 100 m² floor, and all 4 were followed.** The
agents built wings below the stated range:

| Item | Target | v0 → v1 (m²) |
|---|---|---|
| S_IF 9 | 95 | 105.0 → 95.0 |
| S_IF 11 | 95 | 105.0 → 95.0 |
| S_IF 13 | 90 | 95.0 → 90.0 |
| S_GR 3 | 85 | 95.0 → 75.0, past the target |

The old run's single outside item was a T4 below the floor, and it was not followed.

(The lo/hi in the stdout's "outside items" list are the item's own target bounds, not the prompt range.)

### 6.5 Executed: followed / scored, followed / moved

Source: `counts_proposed.csv`. Moved = followed + opposite.

| | Followed / scored | % | Followed / moved |
|---|---|---|---|
| S_SP | 15 / 15 (20 / 21) | 100.0 (95.2) | 15 / 15 (20 / 20) |
| S_IF | 15 / 17 (7 / 7) | 88.2 (100.0) | 15 / 16 (7 / 7) |
| S_GR | 1 / 3 (2 / 6) | 33.3 (33.3), n < 5 | 1 / 1 (2 / 2) |
| O_SP | 0 / 0 (6 / 15) | n/a (40.0) | 0 / 0 (6 / 8) |
| O_IF | 6 / 6 (3 / 10) | 100.0 (30.0) | 6 / 6 (3 / 5) |
| O_GR | 3 / 6 (4 / 12) | 50.0 (33.3) | 3 / 5 (4 / 7) |
| N_IF | 8 / 12 (7 / 10) | 66.7 (70.0) | 8 / 8 (7 / 7) |
| N_GR | 10 / 18 (8 / 11) | 55.6 (72.7) | 10 / 13 (8 / 9) |
| **All** | **58 / 77 (57 / 92)** | **75.3 (62.0)** | **58 / 64 = 90.6 % (57 / 65 = 87.7 %)** |

- O_SP has no scored items (6.1).
- S_GR rests on n = 3.
- O_GR's 3 followed items are exactly the 3 framework-moved CL items (6.3). O_GR has no followed actionable item:
  its one actionable item, AR at iteration 9, was not followed (`scored_items.csv`).
- Of O_GR's 6 scored items, iterations 7 and 9 touch the known-flawed iterations 7 and 8 (B271).

### 6.6 Effective: fuel and GTOW change k → k+1

Source: `ladder_and_table -> reports/ladder/effective_summary.csv` and stdout ("pairs: 77 both flew: 75 both
converged: 70"). Change = value(k+1) − value(k) in kg; improved = went down. Not followed = unchanged + opposite.

| Pairs | Group | Metric | n | Mean | Median | Improved |
|---|---|---|---|---|---|---|
| All (both flew) | followed | fuel | 57 (51) | −159.7 (−380.5) | −504.1 (−948.6) | 33 (27) |
| All (both flew) | not followed | fuel | 18 (31) | −18.2 (+164.8) | +11.5 (+10.7) | 7 (8) |
| All (both flew) | followed | GTOW | 57 (51) | +182.3 (−785.2) | −205.7 (−134.3) | 30 (26) |
| All (both flew) | not followed | GTOW | 18 (31) | −330.9 (+307.4) | 0.0 (0.0) | 8 (11) |
| Converged only | followed | fuel | 54 (5) | +17.8 (−7,510.0) | −538.8 (−9,821.4) | 31 (4) |
| Converged only | not followed | fuel | 16 (6) | −15.0 (−94.9) | +22.6 (+5.4) | 6 (2) |
| Converged only | followed | GTOW | 54 (5) | +474.2 (−10,710.0) | −140.7 (−13,127.2) | 28 (4) |
| Converged only | not followed | GTOW | 16 (6) | −659.8 (−140.0) | 0.0 (+7.8) | 7 (2) |

**Distinct iteration steps.**
- Pairs where both flew: 75 item pairs (old 82) on **75 distinct steps** (old 70). 57 steps have followed items and
  18 have not-followed items, and no step has both.
- Converged-only pairs: 70 item pairs on 70 distinct steps (54 followed, 16 not followed).
- So in this run no step is counted twice. That matches 6.1: one item per recommendation.

**Converged-only pairs.** The converged-only rows are no longer small: 70 pairs (old 11), because most of this
run's missions converged. The followed group's converged mean fuel change is still positive (+17.8 kg) while its
median is −538.8 kg, so a few large increases dominate the mean. Means and medians point in different directions.
Report the median and the improved count, not the mean.

**Pairs that touch a known-flawed iteration (k or k+1).** 11 item pairs, kept in the table above:

| Pair | Variable | Followed | Both flew | Δfuel (kg) |
|---|---|---|---|---|
| O_IF 14 (k = 13, B270) | AR | yes | yes | +360.4 |
| O_GR 7 (k+1 = 7, B271) | CL | yes | yes | −1,436.8 |
| O_GR 9 (k = 8, B271) | AR | no | yes | −11,818.8 |
| N_IF 12 (k = 11, B272) | AR | yes | no | not flown |
| N_IF 17 (B272) | AR | yes | yes | −4,661.7 |
| N_IF 18 (k = 17, B272) | area | yes | yes | +17,837.0 |
| N_IF 20 (B272) | area | yes | yes | −19,673.3 |
| N_GR 6 (B272) | area | yes | yes | −5,575.8 |
| N_GR 7 (k = 6, B272) | area | no | yes | −402.8 |
| N_GR 11 (B271) | area | yes | yes | −2,201.3 |
| N_GR 12 (k = 11, B271) | area | no | yes | +1,055.7 |

The N_IF 18 and N_IF 20 swings (+17.8 t and −19.7 t) are large enough to move the followed means.

Snippet (ws8):
```python
import pandas as pd
e=pd.read_csv("/data/Jessica/l20_final/reports/ladder/effective_pairs.csv")
for lab,sub in (("both flew",e[e.both_flew]),("both converged",e[e.both_converged])):
    steps=sub.groupby(["abbrev","link"]).followed.agg(["any","all"])
    print(lab,len(sub),len(steps),int(steps["any"].sum()),int((~steps["all"]).sum()),int((steps["any"]&~steps["all"]).sum()))
flaw={("O_IF",13),("O_GR",5),("O_GR",7),("O_GR",8),("N_GR",6),("N_GR",11),("N_IF",11),("N_IF",17),("N_IF",20)}
print(e[[((a,l) in flaw) or ((a,l-1) in flaw) for a,l in zip(e.abbrev,e.link)]])
```

### 6.7 Base rate: recommended vs not-recommended parameter changes

Source: `recommendations_report` stdout §6b2 (`reports/recommendations/change_base_rate.csv`). Changed means ≥ 0.1 %.
This control uses report 05's measurable items (79), not the 77 scored items, as in the old report.

| | new | old |
|---|---|---|
| Recommended parameter changed | **64 of 79 (81.0 %)** | 77 of 110 (70.0 %) |
| Not-recommended parameter changed | **333 of 2,066 (16.1 %)** | 551 of 2,232 (24.7 %) |

By parameter, recommended vs not recommended:
- **Wing area.** 32 of 40 vs 4 of 104 (old 27 of 32 vs 16 of 112).
- **AR.** 24 of 29 vs 16 of 115 (old 36 of 38 vs 21 of 106).

Framework-written parameters move without being asked:
- Drag factor: 104 of 144.
- Engine scale factor: 106 of 141 (not recommended).
- Design CL: 58 of 139 (not recommended).

A "followed" item on these is weak evidence (see 6.3).

### 6.8 Target reached (v1 within 1 % of target)

Source: `counts_proposed.csv`, cross-checked from `reports/ladder/scored_items.csv` `reached_proposed` (ws8 snippet in
6.6). The base is scored items that state a target (value, range or bound).

| | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR | **All** |
|---|---|---|---|---|---|---|---|---|---|
| Reached / target items | 15/15 (11/11) | 15/17 (7/7) | 0/2 (0/0) | 0/0 (4/14) | 6/6 (2/8) | 1/3 (2/12) | 0/0 (2/3) | 0/1 (0/0) | **37/44 (28/55)** |

- n < 5 for S_GR, O_GR and N_GR.
- The two networked combinations state almost no numeric targets. N_IF's 12 scored items are all direction-only.
- 4 of the 37 reached targets are outside the prompt range (6.4: S_IF 9, 11, 13, all at 95 or 90 m²; S_GR 3
  overshot to 75 m² and is not "reached").

### 6.9 Recommendations by target role and by variable

Carried-forward recommendation per iteration (of 20), by target role. Source: `recommendations_report` stdout §4a,
`reports/recommendations/carried_rec_by_role.csv`.

| Role | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR | Total |
|---|---|---|---|---|---|---|---|---|---|
| Geometry engineer | 13 | 16 | 2 | 1 | 8 | 3 | 18 | 20 | **81 (63)** |
| Aerodynamics analyst | 0 | 2 | 16 | 0 | 0 | 5 | 0 | 0 | **23 (16)** |
| None recorded | 0 | 0 | 0 | 2 | 8 | 6 | 2 | 0 | **18 (14)** |
| Mission architect | 0 | 0 | 1 | 11 | 1 | 3 | 0 | 0 | **16 (18)** |
| Propulsion analyst | 7 | 2 | 1 | 0 | 0 | 1 | 0 | 0 | **11 (24)** |
| Structures analyst | 0 | 0 | 0 | 6 | 3 | 2 | 0 | 0 | **11 (21)** |
| Simulation executor | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0 (4)** |

"None recorded" (18) is larger than the 8 iterations with no recommendation at all (6.10), because it also counts
recommendations with no target role. The counts match the final COMPLETE verdicts: O_SP 2, O_IF 6 and O_GR 2 last
verdicts are COMPLETE (stdout §3). I inferred this from the matching counts and did not check it text by text.

By design variable (one recommendation can name several). Source: stdout §4b, `carried_rec_by_variable.csv`.

| Variable | S_SP | S_IF | S_GR | O_SP | O_IF | O_GR | N_IF | N_GR | Total |
|---|---|---|---|---|---|---|---|---|---|
| Wing area | 4 | 10 | 2 | 0 | 0 | 0 | 11 | 19 | **46 (36)** |
| Wing aspect ratio | 9 | 6 | 1 | 0 | 7 | 2 | 8 | 3 | **36 (51)** |
| Aero coefficients (CL, CD) | 0 | 2 | 15 | 1 | 0 | 7 | 0 | 0 | **25 (11)** |
| None recorded | 0 | 0 | 0 | 2 | 8 | 6 | 2 | 0 | **18 (14)** |
| Aircraft mass (OEM, MTOM) | 0 | 0 | 0 | 7 | 3 | 0 | 0 | 0 | **10 (11)** |
| Mission (range, Mach, trajectory, reserves) | 0 | 0 | 0 | 5 | 0 | 3 | 0 | 0 | **8 (8)** |
| Other | 4 | 0 | 0 | 2 | 0 | 1 | 0 | 0 | **7 (1)** |
| Wing mass / mass scaler | 0 | 0 | 0 | 3 | 2 | 2 | 0 | 0 | **7 (18)** |
| Engine cycle (BPR, PRs, T4, SFC, thrust) | 3 | 0 | 2 | 0 | 1 | 0 | 0 | 0 | **6 (14)** |
| Wing taper ratio | 0 | 0 | 0 | 1 | 0 | 3 | 0 | 0 | **4 (10)** |
| Engine scale factor | 0 | 2 | 0 | 0 | 0 | 1 | 0 | 0 | **3 (22)** |
| Run or validate only | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | **1 (11)** |
| Optimizer settings | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0 (10)** |
| Wing sweep | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0 (6)** |

- **Wing taper (4).** Taper is named in 4 recommendations, but no taper item was parsed (6.3).
- **S_GR.** S_GR recommends to the aerodynamics analyst on aero coefficients (16 and 15 of 20). Its modal text,
  repeated 7 times, is "Reduce cruise drag coefficient further to decrease fuel burn and bring gross takeoff weight
  within constraint". Such recommendations name no plotted parameter, which is why S_GR has only 4 items.
- **N_GR.** N_GR names the geometry engineer in 20 of 20 iterations. Its modal text, 9 times, is "Increase wing area
  further to reduce fuel burn and GTOW" (stdout §5).

### 6.10 record_recommendation calls refused by the role guard; iterations with no recommendation

The refusal reply is `{"error": "Only the mdo_integrator records the recommendation; you are <role>.", "recorded":
false}`.

**Two counts.**
- `recommendations_report` stdout §3b: step logs, one attempt per iteration.
- My count: every `step_log_attempt*.jsonl`, including retry attempts of failed iterations, counting the reply in
  each step's `observations`.
- `record_recommendation` does not appear in `tool_calls_attempt*.jsonl` (it is a local tool, not an MCP call), so
  both counts come from the step logs.

| | Calls | Recorded | Refused: not the integrator | Refused: already recorded | Mine, all attempts (calls / guard / already) |
|---|---|---|---|---|---|
| S_SP | 80 (234) | 20 | **60 (87)** | 0 (0) | 80 / 60 / 0 |
| S_IF | 98 (250) | 20 | **78 (107)** | 0 (0) | 98 / 78 / 0 |
| S_GR | 185 (633) | 43 | **108 (354)** | 34 (0) | 185 / 108 / 34 |
| O_SP | 29 (294) | 20 | **9 (29)** | 0 (0) | 30 / 10 / 0 |
| O_IF | 49 (367) | 21 | **27 (43)** | 1 (0) | 49 / 27 / 1 |
| O_GR | 66 (136) | 24 | **33 (74)** | 9 (13) | 66 / 33 / 9 |
| N_IF | 31 (90) | 18 | **5 (53)** | 8 (8) | 43 / 6 / 9 |
| N_GR | 146 (277) | 26 | **116 (249)** | 4 (2) | 146 / 116 / 4 |
| **All** | **684 (2,281)** | **192** | **436 (996)** | **56 (23)** | 697 / 438 / 57 |

- The "Recorded" column equals `n_recommendations_design_json` in every combination (stdout §3b). The old run's
  recorded counts (147, 143, 279, 265, 324, 49, 29, 26) were inflated by the record_recommendation loop in the old
  v2full code, so the calls and refusals are not comparable for S_SP–O_IF.
- My count matches the script in 6 of 8 combinations. The extra calls in O_SP (+1) and N_IF (+12) come from the
  attempt files that the script does not read. I did not check which attempts.
- **N_GR.** N_GR is still the worst-hit by the role guard: 116 of 146 calls refused, even though agent_1 is the
  N_GR recorder (stdout §3: rec_agents `agent_1`).
  - The first refusal is in iteration 1: "Only the mdo_integrator records the recommendation; you are agent_1."
  - Unlike the old run, no N_GR iteration ends without a recommendation.
- Example refusals, first iteration of each combination (my snippet below):
  - `structures_analyst` (S_IF, O_SP, O_GR)
  - `aerodynamics_analyst` (S_SP)
  - `mission_architect` (S_GR)
  - `simulation_executor` (O_IF)
  - `agent_2` (N_IF)

**Iterations with no recorded recommendation: 8 (old: 14 "None recorded" links).** Source: stdout §3b.

| Combination | Iteration | Status | record_recommendation calls (all refused as not the integrator) |
|---|---|---|---|
| N_IF | 2 | failed | 0 |
| N_IF | 3 | failed | 1 |
| O_GR | 9 | completed | 0 |
| O_GR | 12 | completed | 0 |
| O_GR | 14 | completed | 0 |
| O_GR | 20 | completed | 1 |
| O_IF | 10 | completed | 1 |
| O_IF | 20 | failed | 2 |

5 of the 8 are completed iterations whose integrator did not record anything (O_GR 9, 12, 14, 20 and O_IF 10).
O_GR 9 follows the flawed O_GR 8 (B271).

Snippet (ws8, read-only):
```python
import json, glob, collections
tot=collections.defaultdict(collections.Counter); ex={}
for f in sorted(glob.glob("/data/Jessica/l20_final/runs/*/repeat_*/*/step_log_attempt*.jsonl")):
    p=f.split("/"); key=p[7]; it=int(p[6].split("_")[1])+1
    for line in open(f):
        if "record_recommendation" not in line: continue
        try: s=json.loads(line)
        except Exception: continue
        n=sum(1 for c in (s.get("tool_calls") or []) if c.get("name")=="record_recommendation")
        if not n: continue
        obs=str(s.get("observations") or "")
        guard=obs.count("Only the mdo_integrator records the recommendation")
        tot[key]["calls"]+=n; tot[key]["refused_not_integrator"]+=guard
        tot[key]["refused_already"]+=obs.lower().count("already recorded")
        if guard and key not in ex: ex[key]=(it, obs[:160])
for k,v in sorted(tot.items()): print(k, dict(v))
for k,v in sorted(ex.items()): print("EX", k, v)
```

---

## 7. Per-iteration table (Appendix E)

Source: `ladder_and_table.py` -> `reports/ladder/iterations.csv` (160 rows) and `iterations_latex_rows.tex` (aipexws8
`/data/Jessica/l20_final/reports/ladder/`). Same definitions as report 08: fuel, GTOW, wing mass from the last
successful mission of the counted attempt; Converged = SLSQP's flag for that mission; "--" when the iteration did not
fly; duration from result.json ("--" for failed iterations, which have none); output tokens summed from the step logs;
agent steps = steps of the counted attempt.

Failed iterations (no result.json): O_IF 20; N_IF 2, 3, 10, 15. Completed but did not fly: N_IF 12. Known-flawed
iterations are marked in section 9.


#### S_SP

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 21,876 | 98,535 | 8,735 | y | y | 40 | 0.31 | 9,197 |
| 2 | 20,968 | 99,308 | 8,892 | y | y | 46 | 0.35 | 10,673 |
| 3 | 19,774 | 97,369 | 8,932 | y | y | 42 | 0.33 | 9,917 |
| 4 | 20,290 | 98,200 | 8,908 | y | y | 44 | 0.34 | 11,033 |
| 5 | 21,474 | 97,994 | 8,770 | y | y | 48 | 0.34 | 10,373 |
| 6 | 23,793 | 100,756 | 8,195 | y | y | 47 | 0.33 | 9,432 |
| 7 | 22,425 | 99,255 | 8,675 | y | y | 43 | 0.31 | 9,616 |
| 8 | 20,972 | 99,314 | 8,892 | y | y | 42 | 0.32 | 9,817 |
| 9 | 21,000 | 99,357 | 8,892 | y | y | 41 | 0.31 | 9,164 |
| 10 | 21,000 | 99,357 | 8,892 | y | y | 43 | 0.32 | 9,161 |
| 11 | 21,000 | 99,357 | 8,892 | y | y | 41 | 0.30 | 8,140 |
| 12 | 20,910 | 99,221 | 8,892 | y | y | 40 | 0.30 | 8,651 |
| 13 | 21,676 | 99,699 | 8,639 | y | y | 37 | 0.26 | 6,649 |
| 14 | 20,458 | 97,714 | 8,655 | y | y | 45 | 0.35 | 10,774 |
| 15 | 21,181 | 98,115 | 8,655 | y | y | 44 | 0.88 | 9,754 |
| 16 | 19,982 | 97,451 | 8,832 | y | y | 37 | 0.30 | 8,613 |
| 17 | 20,458 | 97,714 | 8,655 | y | y | 36 | 0.29 | 7,602 |
| 18 | 19,982 | 97,451 | 8,832 | y | y | 43 | 0.33 | 9,535 |
| 19 | 21,163 | 99,368 | 8,800 | y | y | 44 | 0.31 | 8,497 |
| 20 | 20,103 | 98,693 | 8,800 | y | y | 42 | 0.92 | 10,863 |

#### S_IF

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 21,876 | 98,535 | 8,735 | y | y | 42 | 0.28 | 9,180 |
| 2 | 20,968 | 99,308 | 8,892 | y | y | 48 | 0.29 | 9,534 |
| 3 | 20,290 | 98,200 | 8,908 | y | y | 67 | 0.38 | 14,278 |
| 4 | 21,010 | 98,602 | 8,642 | y | y | 59 | 0.34 | 12,441 |
| 5 | 20,924 | 98,475 | 8,642 | y | y | 45 | 0.34 | 13,416 |
| 6 | 20,924 | 98,475 | 8,642 | y | y | 49 | 0.32 | 11,682 |
| 7 | 22,275 | 99,548 | 8,314 | y | y | 44 | 0.25 | 7,654 |
| 8 | 21,609 | 98,460 | 8,309 | y | y | 60 | 0.35 | 13,416 |
| 9 | 23,008 | 99,479 | 7,978 | y | y | 42 | 0.26 | 8,137 |
| 10 | 21,609 | 98,460 | 8,309 | y | y | 48 | 0.30 | 10,341 |
| 11 | 23,008 | 99,479 | 7,978 | y | y | 43 | 0.28 | 9,532 |
| 12 | 21,800 | 97,503 | 7,967 | y | y | 64 | 0.41 | 17,127 |
| 13 | 22,539 | 97,996 | 7,787 | y | y | 44 | 0.28 | 8,876 |
| 14 | 22,035 | 97,211 | 7,801 | y | y | 62 | 0.40 | 15,851 |
| 15 | 20,644 | 96,335 | 8,175 | y | y | 47 | 0.31 | 11,054 |
| 16 | 19,644 | 95,918 | 8,553 | y | y | 44 | 0.27 | 8,863 |
| 17 | 20,121 | 96,102 | 8,364 | y | y | 44 | 0.30 | 10,367 |
| 18 | 19,807 | 95,610 | 8,398 | y | y | 44 | 0.32 | 11,703 |
| 19 | 19,403 | 95,023 | 8,398 | y | y | 41 | 0.30 | 10,785 |
| 20 | 19,403 | 95,023 | 8,398 | y | y | 40 | 0.26 | 8,514 |

#### S_GR

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 28,530 | 110,102 | 8,997 | y | y | 131 | 0.99 | 27,153 |
| 2 | 32,170 | 111,889 | 7,967 | y | y | 143 | 1.04 | 30,475 |
| 3 | 22,106 | 96,671 | 7,268 | y | n | 138 | 1.29 | 30,763 |
| 4 | 22,662 | 96,720 | 7,268 | y | n | 68 | 0.35 | 13,155 |
| 5 | 51,679 | 144,996 | 7,268 | y | y | 76 | 0.61 | 14,855 |
| 6 | 51,836 | 145,351 | 7,268 | y | y | 49 | 0.39 | 11,462 |
| 7 | 51,836 | 145,351 | 7,268 | y | y | 52 | 0.38 | 11,369 |
| 8 | 48,542 | 143,082 | 7,436 | y | y | 126 | 0.97 | 26,366 |
| 9 | 47,589 | 137,355 | 7,436 | y | y | 75 | 0.45 | 12,816 |
| 10 | 47,698 | 137,635 | 7,436 | y | y | 49 | 0.32 | 10,312 |
| 11 | 47,698 | 137,635 | 7,436 | y | y | 48 | 0.32 | 10,827 |
| 12 | 50,753 | 152,283 | 7,436 | y | y | 55 | 0.49 | 10,595 |
| 13 | 47,698 | 137,635 | 7,436 | y | y | 84 | 0.53 | 17,140 |
| 14 | 49,248 | 135,662 | 7,211 | y | n | 145 | 1.09 | 30,487 |
| 15 | 49,518 | 136,023 | 7,211 | y | y | 72 | 0.52 | 14,164 |
| 16 | 49,592 | 136,248 | 7,211 | y | y | 76 | 0.56 | 15,384 |
| 17 | 49,592 | 136,248 | 7,211 | y | y | 77 | 0.55 | 14,789 |
| 18 | 49,592 | 136,248 | 7,211 | y | y | 48 | 0.34 | 10,506 |
| 19 | 35,735 | 117,756 | 6,236 | y | n | 136 | 1.14 | 32,455 |
| 20 | 35,624 | 117,613 | 6,236 | y | n | 83 | 0.48 | 16,438 |

#### O_SP

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 22,914 | 100,072 | 8,718 | y | y | 59 | 0.29 | 11,502 |
| 2 | 23,006 | 100,202 | 8,718 | y | y | 63 | 0.31 | 11,982 |
| 3 | 23,086 | 100,287 | 8,718 | y | y | 60 | 0.30 | 11,770 |
| 4 | 22,998 | 100,160 | 8,718 | y | y | 64 | 0.33 | 13,160 |
| 5 | 23,006 | 100,202 | 8,718 | y | y | 62 | 0.33 | 13,101 |
| 6 | 14,812 | 88,021 | 8,718 | y | y | 55 | 0.25 | 10,210 |
| 7 | 22,914 | 100,072 | 8,718 | y | y | 62 | 0.33 | 13,337 |
| 8 | 23,006 | 100,202 | 8,718 | y | y | 63 | 0.32 | 12,087 |
| 9 | 23,006 | 100,202 | 8,718 | y | y | 63 | 0.30 | 11,380 |
| 10 | 22,914 | 100,072 | 8,718 | y | y | 64 | 0.32 | 12,502 |
| 11 | 23,006 | 100,202 | 8,718 | y | y | 65 | 0.35 | 14,724 |
| 12 | 22,914 | 100,072 | 8,718 | y | y | 58 | 0.34 | 14,652 |
| 13 | 23,006 | 100,202 | 8,718 | y | y | 62 | 0.35 | 14,808 |
| 14 | 22,998 | 100,160 | 8,718 | y | y | 66 | 0.33 | 13,456 |
| 15 | 22,914 | 100,072 | 8,718 | y | y | 59 | 0.28 | 10,635 |
| 16 | 23,006 | 100,202 | 8,718 | y | y | 61 | 0.31 | 12,240 |
| 17 | 23,006 | 100,202 | 8,718 | y | y | 62 | 0.30 | 11,204 |
| 18 | 23,006 | 100,202 | 8,718 | y | y | 65 | 0.32 | 12,665 |
| 19 | 14,812 | 88,021 | 8,718 | y | y | 58 | 0.27 | 11,588 |
| 20 | 23,006 | 100,202 | 8,718 | y | y | 59 | 0.29 | 11,271 |

#### O_IF

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 22,914 | 100,072 | 8,718 | y | y | 60 | 0.36 | 16,090 |
| 2 | 22,341 | 101,540 | 8,917 | y | y | 64 | 0.41 | 18,231 |
| 3 | 22,369 | 101,583 | 8,917 | y | y | 64 | 0.35 | 14,658 |
| 4 | 13,766 | 88,226 | 8,917 | y | y | 53 | 0.25 | 10,923 |
| 5 | 21,812 | 100,614 | 8,931 | y | y | 64 | 0.34 | 14,174 |
| 6 | 21,737 | 100,533 | 8,931 | y | n | 73 | 0.44 | 18,122 |
| 7 | 21,250 | 99,726 | 8,953 | y | y | 63 | 0.26 | 8,595 |
| 8 | 21,737 | 100,533 | 8,931 | y | n | 68 | 0.41 | 17,625 |
| 9 | 18,519 | 99,711 | 8,931 | y | y | 66 | 0.37 | 16,465 |
| 10 | 20,869 | 99,095 | 8,982 | y | y | 117 | 0.57 | 21,432 |
| 11 | 20,869 | 99,095 | 8,982 | y | y | 64 | 0.43 | 20,508 |
| 12 | 17,681 | 98,130 | 8,982 | y | y | 607 | 4.01 | 109,702 |
| 13 | 20,767 | 98,941 | 8,982 | y | y | 135 | 0.70 | 28,467 |
| 14 | 21,128 | 99,460 | 9,015 | y | y | 59 | 0.32 | 13,168 |
| 15 | 20,987 | 99,246 | 9,015 | y | y | 77 | 0.39 | 16,710 |
| 16 | 20,987 | 99,246 | 9,015 | y | y | 601 | 4.10 | 118,778 |
| 17 | 20,987 | 99,246 | 9,015 | y | y | 49 | 0.33 | 14,711 |
| 18 | 20,939 | 99,171 | 8,982 | y | y | 51 | 0.24 | 8,081 |
| 19 | 20,767 | 98,940 | 8,982 | y | y | 67 | 0.37 | 16,077 |
| 20 | -- | -- | -- | n | -- | 363 | -- | 67,654 |

#### O_GR

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 21,699 | 98,319 | 8,735 | y | y | 207 | 12.45 | 38,930 |
| 2 | 21,788 | 98,409 | 8,735 | y | y | 114 | 1.39 | 38,515 |
| 3 | 21,788 | 98,409 | 8,735 | y | y | 96 | 0.84 | 20,750 |
| 4 | 21,788 | 98,409 | 8,735 | y | y | 85 | 0.73 | 17,622 |
| 5 | 23,615 | 100,982 | 8,770 | y | y | 143 | 1.51 | 28,810 |
| 6 | 20,758 | 96,895 | 8,770 | y | y | 147 | 2.53 | 76,924 |
| 7 | 19,321 | 95,521 | 8,469 | y | y | 146 | 1.28 | 30,231 |
| 8 | 38,978 | 124,482 | 8,470 | y | y | 161 | 1.43 | 35,568 |
| 9 | 27,159 | 107,071 | 8,470 | y | y | 51 | 0.60 | 15,499 |
| 10 | 27,159 | 107,071 | 8,470 | y | y | 111 | 0.87 | 23,269 |
| 11 | 27,318 | 107,296 | 8,470 | y | y | 127 | 0.96 | 22,727 |
| 12 | 27,159 | 107,071 | 8,470 | y | y | 48 | 0.52 | 13,277 |
| 13 | 32,945 | 118,377 | 8,490 | y | y | 160 | 2.02 | 31,109 |
| 14 | 23,192 | 103,029 | 8,490 | y | y | 53 | 0.61 | 15,408 |
| 15 | 23,192 | 103,029 | 8,490 | y | y | 48 | 0.49 | 11,807 |
| 16 | 23,192 | 103,029 | 8,490 | y | y | 33 | 0.41 | 12,856 |
| 17 | 32,660 | 117,265 | 8,490 | y | y | 84 | 0.72 | 17,975 |
| 18 | 23,192 | 103,029 | 8,490 | y | y | 102 | 1.15 | 30,993 |
| 19 | 32,660 | 117,265 | 8,490 | y | y | 84 | 0.88 | 22,828 |
| 20 | 23,192 | 103,029 | 8,490 | y | y | 47 | 0.44 | 11,323 |

#### N_IF

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 22,916 | 100,074 | 8,718 | y | y | 168 | 2.79 | 23,556 |
| 2 | -- | -- | -- | n | -- | 268 | -- | 30,224 |
| 3 | -- | -- | -- | n | -- | 139 | -- | 17,137 |
| 4 | 22,914 | 100,072 | 8,718 | y | y | 161 | 2.76 | 20,435 |
| 5 | 23,003 | 100,124 | 8,718 | y | y | 184 | 2.99 | 22,113 |
| 6 | 19,503 | 95,827 | 9,071 | y | y | 255 | 3.82 | 31,000 |
| 7 | 26,848 | 109,536 | 9,295 | y | y | 100 | 1.36 | 12,236 |
| 8 | 27,024 | 109,801 | 9,295 | y | y | 204 | 2.63 | 26,820 |
| 9 | 25,200 | 106,855 | 9,322 | y | y | 131 | 2.19 | 21,789 |
| 10 | -- | -- | -- | n | -- | 240 | -- | 30,663 |
| 11 | 16,602 | 92,480 | 8,990 | y | y | 129 | 1.20 | 18,465 |
| 12 | -- | -- | -- | n | -- | 131 | 1.19 | 15,758 |
| 13 | 20,665 | 94,386 | 9,007 | y | n | 220 | 2.60 | 23,304 |
| 14 | 20,688 | 99,130 | 9,007 | y | y | 103 | 0.88 | 12,543 |
| 15 | -- | -- | -- | n | -- | 141 | -- | 15,897 |
| 16 | 21,239 | 99,889 | 9,032 | y | y | 82 | 0.79 | 11,632 |
| 17 | 16,578 | 95,203 | 9,032 | y | y | 227 | 2.76 | 27,500 |
| 18 | 34,414 | 118,900 | 8,836 | y | y | 189 | 1.64 | 18,756 |
| 19 | 38,632 | 124,592 | 8,669 | y | y | 247 | 2.75 | 32,935 |
| 20 | 18,959 | 104,851 | 8,669 | y | y | 206 | 1.80 | 22,308 |

#### N_GR

| Iter | Fuel (kg) | GTOW (kg) | Wing mass (kg) | Flew | Converged | Agent steps | Duration (h) | Output tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | 19,761 | 97,277 | 8,932 | y | y | 438 | 5.66 | 79,630 |
| 2 | 19,713 | 97,305 | 8,932 | y | y | 136 | 1.24 | 22,546 |
| 3 | 19,713 | 97,305 | 8,932 | y | y | 150 | 1.21 | 20,908 |
| 4 | 20,890 | 97,884 | 8,480 | y | y | 154 | 1.99 | 23,244 |
| 5 | 26,906 | 108,169 | 9,009 | y | y | 263 | 3.84 | 40,109 |
| 6 | 21,331 | 102,587 | 9,009 | y | y | 100 | 0.92 | 16,987 |
| 7 | 20,928 | 99,741 | 9,236 | y | y | 138 | 1.46 | 21,475 |
| 8 | 18,194 | 96,256 | 9,702 | y | y | 162 | 2.04 | 33,413 |
| 9 | 16,451 | 93,945 | 9,947 | y | y | 103 | 1.21 | 17,719 |
| 10 | 16,401 | 93,895 | 9,947 | y | y | 159 | 2.20 | 51,328 |
| 11 | 14,199 | 91,530 | 10,525 | y | y | 282 | 2.42 | 43,398 |
| 12 | 15,255 | 92,346 | 10,019 | y | y | 106 | 1.17 | 16,608 |
| 13 | 21,043 | 100,671 | 9,552 | y | y | 128 | 1.41 | 28,495 |
| 14 | 29,248 | 111,792 | 8,991 | y | y | 433 | 5.24 | 68,539 |
| 15 | 23,103 | 102,360 | 8,810 | y | y | 154 | 1.95 | 31,863 |
| 16 | 37,182 | 122,225 | 8,652 | y | y | 185 | 1.48 | 22,876 |
| 17 | 37,100 | 122,142 | 8,652 | y | y | 93 | 1.09 | 18,596 |
| 18 | 38,913 | 127,994 | 8,161 | y | y | 483 | 10.25 | 72,313 |
| 19 | 35,303 | 125,306 | 8,606 | y | y | 205 | 3.58 | 43,028 |
| 20 | 35,349 | 119,994 | 8,606 | y | y | 501 | 5.87 | 75,396 |

LaTeX rows (`iterations_latex_rows.tex`; columns: combination, iteration, fuel, GTOW, wing mass, flew, converged, agent
steps, duration, output tokens):

```latex
% S_SP
S\_SP & 1 & 21,876 & 98,535 & 8,735 & y & y & 40 & 0.31 & 9,197 \\
S\_SP & 2 & 20,968 & 99,308 & 8,892 & y & y & 46 & 0.35 & 10,673 \\
S\_SP & 3 & 19,774 & 97,369 & 8,932 & y & y & 42 & 0.33 & 9,917 \\
S\_SP & 4 & 20,290 & 98,200 & 8,908 & y & y & 44 & 0.34 & 11,033 \\
S\_SP & 5 & 21,474 & 97,994 & 8,770 & y & y & 48 & 0.34 & 10,373 \\
S\_SP & 6 & 23,793 & 100,756 & 8,195 & y & y & 47 & 0.33 & 9,432 \\
S\_SP & 7 & 22,425 & 99,255 & 8,675 & y & y & 43 & 0.31 & 9,616 \\
S\_SP & 8 & 20,972 & 99,314 & 8,892 & y & y & 42 & 0.32 & 9,817 \\
S\_SP & 9 & 21,000 & 99,357 & 8,892 & y & y & 41 & 0.31 & 9,164 \\
S\_SP & 10 & 21,000 & 99,357 & 8,892 & y & y & 43 & 0.32 & 9,161 \\
S\_SP & 11 & 21,000 & 99,357 & 8,892 & y & y & 41 & 0.30 & 8,140 \\
S\_SP & 12 & 20,910 & 99,221 & 8,892 & y & y & 40 & 0.30 & 8,651 \\
S\_SP & 13 & 21,676 & 99,699 & 8,639 & y & y & 37 & 0.26 & 6,649 \\
S\_SP & 14 & 20,458 & 97,714 & 8,655 & y & y & 45 & 0.35 & 10,774 \\
S\_SP & 15 & 21,181 & 98,115 & 8,655 & y & y & 44 & 0.88 & 9,754 \\
S\_SP & 16 & 19,982 & 97,451 & 8,832 & y & y & 37 & 0.30 & 8,613 \\
S\_SP & 17 & 20,458 & 97,714 & 8,655 & y & y & 36 & 0.29 & 7,602 \\
S\_SP & 18 & 19,982 & 97,451 & 8,832 & y & y & 43 & 0.33 & 9,535 \\
S\_SP & 19 & 21,163 & 99,368 & 8,800 & y & y & 44 & 0.31 & 8,497 \\
S\_SP & 20 & 20,103 & 98,693 & 8,800 & y & y & 42 & 0.92 & 10,863 \\
% S_IF
S\_IF & 1 & 21,876 & 98,535 & 8,735 & y & y & 42 & 0.28 & 9,180 \\
S\_IF & 2 & 20,968 & 99,308 & 8,892 & y & y & 48 & 0.29 & 9,534 \\
S\_IF & 3 & 20,290 & 98,200 & 8,908 & y & y & 67 & 0.38 & 14,278 \\
S\_IF & 4 & 21,010 & 98,602 & 8,642 & y & y & 59 & 0.34 & 12,441 \\
S\_IF & 5 & 20,924 & 98,475 & 8,642 & y & y & 45 & 0.34 & 13,416 \\
S\_IF & 6 & 20,924 & 98,475 & 8,642 & y & y & 49 & 0.32 & 11,682 \\
S\_IF & 7 & 22,275 & 99,548 & 8,314 & y & y & 44 & 0.25 & 7,654 \\
S\_IF & 8 & 21,609 & 98,460 & 8,309 & y & y & 60 & 0.35 & 13,416 \\
S\_IF & 9 & 23,008 & 99,479 & 7,978 & y & y & 42 & 0.26 & 8,137 \\
S\_IF & 10 & 21,609 & 98,460 & 8,309 & y & y & 48 & 0.30 & 10,341 \\
S\_IF & 11 & 23,008 & 99,479 & 7,978 & y & y & 43 & 0.28 & 9,532 \\
S\_IF & 12 & 21,800 & 97,503 & 7,967 & y & y & 64 & 0.41 & 17,127 \\
S\_IF & 13 & 22,539 & 97,996 & 7,787 & y & y & 44 & 0.28 & 8,876 \\
S\_IF & 14 & 22,035 & 97,211 & 7,801 & y & y & 62 & 0.40 & 15,851 \\
S\_IF & 15 & 20,644 & 96,335 & 8,175 & y & y & 47 & 0.31 & 11,054 \\
S\_IF & 16 & 19,644 & 95,918 & 8,553 & y & y & 44 & 0.27 & 8,863 \\
S\_IF & 17 & 20,121 & 96,102 & 8,364 & y & y & 44 & 0.30 & 10,367 \\
S\_IF & 18 & 19,807 & 95,610 & 8,398 & y & y & 44 & 0.32 & 11,703 \\
S\_IF & 19 & 19,403 & 95,023 & 8,398 & y & y & 41 & 0.30 & 10,785 \\
S\_IF & 20 & 19,403 & 95,023 & 8,398 & y & y & 40 & 0.26 & 8,514 \\
% S_GR
S\_GR & 1 & 28,530 & 110,102 & 8,997 & y & y & 131 & 0.99 & 27,153 \\
S\_GR & 2 & 32,170 & 111,889 & 7,967 & y & y & 143 & 1.04 & 30,475 \\
S\_GR & 3 & 22,106 & 96,671 & 7,268 & y & n & 138 & 1.29 & 30,763 \\
S\_GR & 4 & 22,662 & 96,720 & 7,268 & y & n & 68 & 0.35 & 13,155 \\
S\_GR & 5 & 51,679 & 144,996 & 7,268 & y & y & 76 & 0.61 & 14,855 \\
S\_GR & 6 & 51,836 & 145,351 & 7,268 & y & y & 49 & 0.39 & 11,462 \\
S\_GR & 7 & 51,836 & 145,351 & 7,268 & y & y & 52 & 0.38 & 11,369 \\
S\_GR & 8 & 48,542 & 143,082 & 7,436 & y & y & 126 & 0.97 & 26,366 \\
S\_GR & 9 & 47,589 & 137,355 & 7,436 & y & y & 75 & 0.45 & 12,816 \\
S\_GR & 10 & 47,698 & 137,635 & 7,436 & y & y & 49 & 0.32 & 10,312 \\
S\_GR & 11 & 47,698 & 137,635 & 7,436 & y & y & 48 & 0.32 & 10,827 \\
S\_GR & 12 & 50,753 & 152,283 & 7,436 & y & y & 55 & 0.49 & 10,595 \\
S\_GR & 13 & 47,698 & 137,635 & 7,436 & y & y & 84 & 0.53 & 17,140 \\
S\_GR & 14 & 49,248 & 135,662 & 7,211 & y & n & 145 & 1.09 & 30,487 \\
S\_GR & 15 & 49,518 & 136,023 & 7,211 & y & y & 72 & 0.52 & 14,164 \\
S\_GR & 16 & 49,592 & 136,248 & 7,211 & y & y & 76 & 0.56 & 15,384 \\
S\_GR & 17 & 49,592 & 136,248 & 7,211 & y & y & 77 & 0.55 & 14,789 \\
S\_GR & 18 & 49,592 & 136,248 & 7,211 & y & y & 48 & 0.34 & 10,506 \\
S\_GR & 19 & 35,735 & 117,756 & 6,236 & y & n & 136 & 1.14 & 32,455 \\
S\_GR & 20 & 35,624 & 117,613 & 6,236 & y & n & 83 & 0.48 & 16,438 \\
% O_SP
O\_SP & 1 & 22,914 & 100,072 & 8,718 & y & y & 59 & 0.29 & 11,502 \\
O\_SP & 2 & 23,006 & 100,202 & 8,718 & y & y & 63 & 0.31 & 11,982 \\
O\_SP & 3 & 23,086 & 100,287 & 8,718 & y & y & 60 & 0.30 & 11,770 \\
O\_SP & 4 & 22,998 & 100,160 & 8,718 & y & y & 64 & 0.33 & 13,160 \\
O\_SP & 5 & 23,006 & 100,202 & 8,718 & y & y & 62 & 0.33 & 13,101 \\
O\_SP & 6 & 14,812 & 88,021 & 8,718 & y & y & 55 & 0.25 & 10,210 \\
O\_SP & 7 & 22,914 & 100,072 & 8,718 & y & y & 62 & 0.33 & 13,337 \\
O\_SP & 8 & 23,006 & 100,202 & 8,718 & y & y & 63 & 0.32 & 12,087 \\
O\_SP & 9 & 23,006 & 100,202 & 8,718 & y & y & 63 & 0.30 & 11,380 \\
O\_SP & 10 & 22,914 & 100,072 & 8,718 & y & y & 64 & 0.32 & 12,502 \\
O\_SP & 11 & 23,006 & 100,202 & 8,718 & y & y & 65 & 0.35 & 14,724 \\
O\_SP & 12 & 22,914 & 100,072 & 8,718 & y & y & 58 & 0.34 & 14,652 \\
O\_SP & 13 & 23,006 & 100,202 & 8,718 & y & y & 62 & 0.35 & 14,808 \\
O\_SP & 14 & 22,998 & 100,160 & 8,718 & y & y & 66 & 0.33 & 13,456 \\
O\_SP & 15 & 22,914 & 100,072 & 8,718 & y & y & 59 & 0.28 & 10,635 \\
O\_SP & 16 & 23,006 & 100,202 & 8,718 & y & y & 61 & 0.31 & 12,240 \\
O\_SP & 17 & 23,006 & 100,202 & 8,718 & y & y & 62 & 0.30 & 11,204 \\
O\_SP & 18 & 23,006 & 100,202 & 8,718 & y & y & 65 & 0.32 & 12,665 \\
O\_SP & 19 & 14,812 & 88,021 & 8,718 & y & y & 58 & 0.27 & 11,588 \\
O\_SP & 20 & 23,006 & 100,202 & 8,718 & y & y & 59 & 0.29 & 11,271 \\
% O_IF
O\_IF & 1 & 22,914 & 100,072 & 8,718 & y & y & 60 & 0.36 & 16,090 \\
O\_IF & 2 & 22,341 & 101,540 & 8,917 & y & y & 64 & 0.41 & 18,231 \\
O\_IF & 3 & 22,369 & 101,583 & 8,917 & y & y & 64 & 0.35 & 14,658 \\
O\_IF & 4 & 13,766 & 88,226 & 8,917 & y & y & 53 & 0.25 & 10,923 \\
O\_IF & 5 & 21,812 & 100,614 & 8,931 & y & y & 64 & 0.34 & 14,174 \\
O\_IF & 6 & 21,737 & 100,533 & 8,931 & y & n & 73 & 0.44 & 18,122 \\
O\_IF & 7 & 21,250 & 99,726 & 8,953 & y & y & 63 & 0.26 & 8,595 \\
O\_IF & 8 & 21,737 & 100,533 & 8,931 & y & n & 68 & 0.41 & 17,625 \\
O\_IF & 9 & 18,519 & 99,711 & 8,931 & y & y & 66 & 0.37 & 16,465 \\
O\_IF & 10 & 20,869 & 99,095 & 8,982 & y & y & 117 & 0.57 & 21,432 \\
O\_IF & 11 & 20,869 & 99,095 & 8,982 & y & y & 64 & 0.43 & 20,508 \\
O\_IF & 12 & 17,681 & 98,130 & 8,982 & y & y & 607 & 4.01 & 109,702 \\
O\_IF & 13 & 20,767 & 98,941 & 8,982 & y & y & 135 & 0.70 & 28,467 \\
O\_IF & 14 & 21,128 & 99,460 & 9,015 & y & y & 59 & 0.32 & 13,168 \\
O\_IF & 15 & 20,987 & 99,246 & 9,015 & y & y & 77 & 0.39 & 16,710 \\
O\_IF & 16 & 20,987 & 99,246 & 9,015 & y & y & 601 & 4.10 & 118,778 \\
O\_IF & 17 & 20,987 & 99,246 & 9,015 & y & y & 49 & 0.33 & 14,711 \\
O\_IF & 18 & 20,939 & 99,171 & 8,982 & y & y & 51 & 0.24 & 8,081 \\
O\_IF & 19 & 20,767 & 98,940 & 8,982 & y & y & 67 & 0.37 & 16,077 \\
O\_IF & 20 & -- & -- & -- & n & -- & 363 & -- & 67,654 \\
% O_GR
O\_GR & 1 & 21,699 & 98,319 & 8,735 & y & y & 207 & 12.45 & 38,930 \\
O\_GR & 2 & 21,788 & 98,409 & 8,735 & y & y & 114 & 1.39 & 38,515 \\
O\_GR & 3 & 21,788 & 98,409 & 8,735 & y & y & 96 & 0.84 & 20,750 \\
O\_GR & 4 & 21,788 & 98,409 & 8,735 & y & y & 85 & 0.73 & 17,622 \\
O\_GR & 5 & 23,615 & 100,982 & 8,770 & y & y & 143 & 1.51 & 28,810 \\
O\_GR & 6 & 20,758 & 96,895 & 8,770 & y & y & 147 & 2.53 & 76,924 \\
O\_GR & 7 & 19,321 & 95,521 & 8,469 & y & y & 146 & 1.28 & 30,231 \\
O\_GR & 8 & 38,978 & 124,482 & 8,470 & y & y & 161 & 1.43 & 35,568 \\
O\_GR & 9 & 27,159 & 107,071 & 8,470 & y & y & 51 & 0.60 & 15,499 \\
O\_GR & 10 & 27,159 & 107,071 & 8,470 & y & y & 111 & 0.87 & 23,269 \\
O\_GR & 11 & 27,318 & 107,296 & 8,470 & y & y & 127 & 0.96 & 22,727 \\
O\_GR & 12 & 27,159 & 107,071 & 8,470 & y & y & 48 & 0.52 & 13,277 \\
O\_GR & 13 & 32,945 & 118,377 & 8,490 & y & y & 160 & 2.02 & 31,109 \\
O\_GR & 14 & 23,192 & 103,029 & 8,490 & y & y & 53 & 0.61 & 15,408 \\
O\_GR & 15 & 23,192 & 103,029 & 8,490 & y & y & 48 & 0.49 & 11,807 \\
O\_GR & 16 & 23,192 & 103,029 & 8,490 & y & y & 33 & 0.41 & 12,856 \\
O\_GR & 17 & 32,660 & 117,265 & 8,490 & y & y & 84 & 0.72 & 17,975 \\
O\_GR & 18 & 23,192 & 103,029 & 8,490 & y & y & 102 & 1.15 & 30,993 \\
O\_GR & 19 & 32,660 & 117,265 & 8,490 & y & y & 84 & 0.88 & 22,828 \\
O\_GR & 20 & 23,192 & 103,029 & 8,490 & y & y & 47 & 0.44 & 11,323 \\
% N_IF
N\_IF & 1 & 22,916 & 100,074 & 8,718 & y & y & 168 & 2.79 & 23,556 \\
N\_IF & 2 & -- & -- & -- & n & -- & 268 & -- & 30,224 \\
N\_IF & 3 & -- & -- & -- & n & -- & 139 & -- & 17,137 \\
N\_IF & 4 & 22,914 & 100,072 & 8,718 & y & y & 161 & 2.76 & 20,435 \\
N\_IF & 5 & 23,003 & 100,124 & 8,718 & y & y & 184 & 2.99 & 22,113 \\
N\_IF & 6 & 19,503 & 95,827 & 9,071 & y & y & 255 & 3.82 & 31,000 \\
N\_IF & 7 & 26,848 & 109,536 & 9,295 & y & y & 100 & 1.36 & 12,236 \\
N\_IF & 8 & 27,024 & 109,801 & 9,295 & y & y & 204 & 2.63 & 26,820 \\
N\_IF & 9 & 25,200 & 106,855 & 9,322 & y & y & 131 & 2.19 & 21,789 \\
N\_IF & 10 & -- & -- & -- & n & -- & 240 & -- & 30,663 \\
N\_IF & 11 & 16,602 & 92,480 & 8,990 & y & y & 129 & 1.20 & 18,465 \\
N\_IF & 12 & -- & -- & -- & n & -- & 131 & 1.19 & 15,758 \\
N\_IF & 13 & 20,665 & 94,386 & 9,007 & y & n & 220 & 2.60 & 23,304 \\
N\_IF & 14 & 20,688 & 99,130 & 9,007 & y & y & 103 & 0.88 & 12,543 \\
N\_IF & 15 & -- & -- & -- & n & -- & 141 & -- & 15,897 \\
N\_IF & 16 & 21,239 & 99,889 & 9,032 & y & y & 82 & 0.79 & 11,632 \\
N\_IF & 17 & 16,578 & 95,203 & 9,032 & y & y & 227 & 2.76 & 27,500 \\
N\_IF & 18 & 34,414 & 118,900 & 8,836 & y & y & 189 & 1.64 & 18,756 \\
N\_IF & 19 & 38,632 & 124,592 & 8,669 & y & y & 247 & 2.75 & 32,935 \\
N\_IF & 20 & 18,959 & 104,851 & 8,669 & y & y & 206 & 1.80 & 22,308 \\
% N_GR
N\_GR & 1 & 19,761 & 97,277 & 8,932 & y & y & 438 & 5.66 & 79,630 \\
N\_GR & 2 & 19,713 & 97,305 & 8,932 & y & y & 136 & 1.24 & 22,546 \\
N\_GR & 3 & 19,713 & 97,305 & 8,932 & y & y & 150 & 1.21 & 20,908 \\
N\_GR & 4 & 20,890 & 97,884 & 8,480 & y & y & 154 & 1.99 & 23,244 \\
N\_GR & 5 & 26,906 & 108,169 & 9,009 & y & y & 263 & 3.84 & 40,109 \\
N\_GR & 6 & 21,331 & 102,587 & 9,009 & y & y & 100 & 0.92 & 16,987 \\
N\_GR & 7 & 20,928 & 99,741 & 9,236 & y & y & 138 & 1.46 & 21,475 \\
N\_GR & 8 & 18,194 & 96,256 & 9,702 & y & y & 162 & 2.04 & 33,413 \\
N\_GR & 9 & 16,451 & 93,945 & 9,947 & y & y & 103 & 1.21 & 17,719 \\
N\_GR & 10 & 16,401 & 93,895 & 9,947 & y & y & 159 & 2.20 & 51,328 \\
N\_GR & 11 & 14,199 & 91,530 & 10,525 & y & y & 282 & 2.42 & 43,398 \\
N\_GR & 12 & 15,255 & 92,346 & 10,019 & y & y & 106 & 1.17 & 16,608 \\
N\_GR & 13 & 21,043 & 100,671 & 9,552 & y & y & 128 & 1.41 & 28,495 \\
N\_GR & 14 & 29,248 & 111,792 & 8,991 & y & y & 433 & 5.24 & 68,539 \\
N\_GR & 15 & 23,103 & 102,360 & 8,810 & y & y & 154 & 1.95 & 31,863 \\
N\_GR & 16 & 37,182 & 122,225 & 8,652 & y & y & 185 & 1.48 & 22,876 \\
N\_GR & 17 & 37,100 & 122,142 & 8,652 & y & y & 93 & 1.09 & 18,596 \\
N\_GR & 18 & 38,913 & 127,994 & 8,161 & y & y & 483 & 10.25 & 72,313 \\
N\_GR & 19 & 35,303 & 125,306 & 8,606 & y & y & 205 & 3.58 & 43,028 \\
N\_GR & 20 & 35,349 & 119,994 & 8,606 & y & y & 501 & 5.87 & 75,396 \\
```

## 8. Figure

`analysis/link20/fuel_panels.py` (MAS-Aviary analysis/20link-metrics f4dfbb1) from `reports/ladder/iterations.csv` and
the baseline JSON:

- **Vector:** `figures/rerun/fuel_burn_line_panels.pdf` (in this folder; also aipexws8 `/data/Jessica/l20_final/out/fuel_burn_line_panels.pdf`)
- PNG: `/data/Jessica/l20_final/out/fuel_burn_line_panels.png`; plotted values: `fuel_burn_line_panels_data.csv`

One panel per combination (rows sequential / orchestrated / networked, columns staged pipeline / iterative feedback /
graph-routed), shared axes from 0 kg. Filled marker = converged mission, hollow = not converged, x on the axis = did
not fly; neutral dashed line = baseline 16,390.5 kg, dotted = 15,000 kg limit. Plotted: 154 flown iterations
(146 converged), 6 did not fly (O_IF 20; N_IF 2, 3, 10, 12, 15).
## 9. Summary for the writer

Engineering outcome first: every combination's mean fuel is still above the baseline (+26.0 % O_IF to +165.3 % S_GR,
old +7.4 % to +81.3 %, against each run's own baseline). But this run converges (146 of 154 flown, old 37 of 140), and
3 iterations meet both limits (O_SP 6 and 19, one design, and O_IF 4; old: none). Agents' engine-cycle choices now
move fuel (section 0.6, 3.2). Then the ten findings the manuscript states:

1. **Coordination metrics stay stationary over the chain: agrees.** 4 of 67 Spearman tests reach p < 0.05 (old 1 of
   72), about what chance gives (3.4). The exception is O_GR, where steps, route length and worker messages fall along
   the chain (section 5.1).
2. **Methodology ordering of median agent steps (Staged Pipeline < Iterative Feedback < Graph-Routed): holds in
   sequential and orchestrated, reverses in networked.** S 42.5 < 44.5 < 76, O 62 < 65 < 99; N_IF 176 > N_GR 156.5
   (old N 172.5 < 393.5) (section 4.10).
3. **Iterative-feedback retries rise with decentralization: agrees.** Retried turns per iteration S_IF 0.55, O_IF 2.65,
   N_IF 6.1 (old 0.55, 2.2, 7.3) (section 4.4).
4. **Graph-Routed rarely completes: partly reverses.** 18 of 60 graph-routed iterations stop COMPLETE (old 3): S_GR 9,
   O_GR 9, N_GR 0. N_GR still never completes, and records no stop reason on 17 of its 20 iterations (section 4.5).
5. **Networked convergence stays low: agrees.** Mean convergence score N_IF 0.280, N_GR 0.357 (old 0.219, 0.272); one
   iteration in each crosses the 0.6 joint-myopia threshold (old none) (section 4.9, 5.4).
6. **Cost per agent rises from Staged Pipeline to Graph-Routed: agrees.** Orchestrated `cost_per_agent` O_SP 29.3 s,
   O_IF 43.2 s, O_GR 115 s (old 36.8, 56.1, 74.2) (section 4.7).
7. **Refusals concentrate where no fixed order exists: agrees.** 498 of the 552 `MISSING_REQUIRED_PARAMETERS`
   refusals are in N_IF and N_GR (old 361 of 413), and all 236 `CLAIMED_BY_ANOTHER_AGENT` refusals are in N_IF
   (section 4.3).
8. **Actionable vs non-actionable execution gap: agrees.** Followed: actionable 55 of 70 (78.6 %; old 51 of 65,
   78.5 %), not actionable 3 of 7 (42.9 %, n = 7; old 6 of 27). All 3 non-actionable "followed" moves were made by the
   framework (injected SU2 CL), not by an agent (section 6.3).
9. **Following recommendations vs fuel: still no clear relation.** Converged pairs: followed median fuel change
   -538.8 kg vs not followed +22.6 kg, but means +17.8 kg vs -15.0 kg (n = 54 and 16); old converged pairs were n = 5
   and 6 (section 6.6). Medians favour following, means do not.
10. **The integrator rarely says COMPLETE: partly reverses.** 10 of 160 carried verdicts are COMPLETE (old 2), all in
    the orchestrated combinations (O_SP 2, O_IF 6, O_GR 2); sequential and networked never say COMPLETE. RETRY fell
    to 2 (old 30) (section 5.5).

## Possible bugs / surprises

### Run setup and pipeline (sections 0, 1, 7, 8)

1. **Not one code version.** S_GR, O_SP/O_IF and O_GR ran MAS-Aviary aeb67dd; S_IF, S_SP, N_IF and N_GR ran b985d28
   (adds B268 only, which never acted on a counted iteration of the aeb67dd runs). State it in Methods (section 0.1).
2. **SU2 core pinning during the first night (B269).** Until ~11:20 on 2026-11-11 every concurrent SU2 solve on a
   machine ran on cores 0-15. It slowed the first ws8 launch so much that its networked chains were discarded and
   relaunched (l20c). Of the runs that kept going, O_GR shared ws8 with the slowed chains, so its first-night iterations
   ran slower solves; S_GR (ws5) and O_SP/O_IF (ws6) were the only study run on their machines, so overlapping solves
   were rarer there. The effect on their durations, and whether any outcome changed, was not quantified.
3. **The analysis scripts compared against the old baseline.** `physics.py` and `efficiency.py` hard-coded 18,197.0 kg.
   Fixed (b3eda63) and rerun; all numbers here use 16,390.5 kg. `budget_compare.py` and `optimizer_compare.py` still
   hard-code it (not used here).
4. **`design_params.py` was given the previous run's `agent_bounds.json` (2026-10-27).** Section 3.4 states which
   ranges each count uses (the agents' design table vs that file).
5. **The baseline moved by 1,807 kg with the same anchor and mission** (18,197.0 -> 16,390.5 kg); the cause (aviary-mcp
   changes between the two flights) was not isolated.
6. **The re-run checklist (`claude/09_rerun_checklist.md`) was not available**, so section 0.7 is incomplete.
7. **N_GR's third peer never takes a step** (agent_3: 0 steps in 20 iterations), and N_IF's 7 `spawn_peer` calls never
   raised `agents_spawned` above 3 (section 0.8).
8. **Agents built wings below the stated 100 m^2 floor** (S_GR 75-95 m^2 on 19 of 20 iterations, S_IF on 5): the design
   table's ranges are prompt guidance only; aviary only warns (section 0.11, 3.4, 6.4).

### Possible bugs / surprises (sections 2-3)

1. **physics.py compares against the old baseline (18,197.0 kg / 91,660.6 kg).** All `vs_base` columns,
   `n_below_base`, `below_baseline_links.csv` (11 iterations) and `link1.csv first_vs_base_*` in
   `/data/Jessica/l20_final/reports/physics/` are wrong for this run; against 16,390.5 kg only 5 iterations are below
   the baseline. The baseline constant in physics.py should be updated before anyone quotes those columns.
2. **design_params.py's `agent_bounds.csv` is the old study's file** (`out/agent_bounds.json`, 2026-10-27): it shows N_GR
   link 20 morphs of 20.8 m² / AR 36.3 / sweep −77 °, which belong to the old run. `wing_morph_range.csv` for this run
   tops out at 75.0-160.0 m², AR 8.5-14.5. agent_bounds.py was not rerun on the final study.
3. **Aviary `Aircraft.Wing.SWEEP` jumps 21.65 → 27.33 ° at iteration 1 in O_SP, O_IF and N_IF** and stays there, while
   S_*, O_GR and N_GR keep ~21.65 °. 27.33 ° is the tigl `sweep` key's default in the design table (a different
   sweep definition from aviary's quarter-chord 21.65 °). Looks like these combinations' iteration-1 morph (the same
   calls that restated the tails, `tail_morph_calls.csv`) wrote the tigl sweep into the aviary quarter-chord sweep.
   Not checked in the raw knowledge base.
4. **`Aircraft.Wing.MASS_SCALER` is empty on every row of this run's links.csv**, so the wing mass scaler (a moved
   parameter in all 8 combinations in the old run) cannot be counted. Either the data plane now sets `Aircraft.Wing.MASS`
   directly (aviary_key_counts.csv lists `Aircraft.Wing.MASS`, set on 34-148 calls) or extract.py misses the key.
5. **O_SP wing mass is 8,717.8 kg on all 20 iterations** (`summary_wing_mass_kg.csv`, std 0.0) and its geometry never
   moved, yet its fuel drops to 14,812.1 kg twice (iterations 6 and 19, identical to 0.1 kg, GTOW 88,021.2 kg both),
   with engine scale 0.978 at iteration 6 (the only scale below 1.0 in the run). Two of the three both-limit
   iterations of the study come from this; the low values are driven by the coupled engine scale / design CL (design CL
   moved at 6, 7, 19, 20), not by a design change. Worth checking what set the scale below 1.0.
6. **O_IF 4 (13,765.6 kg, the study minimum, meets both limits)** has engine scale 1.106; the next iteration jumps to
   1.61 and 21,811.6 kg with only a 0.3 % fan/LPC PR change. A ~45 % scale jump from a tiny cycle edit looks like the
   pyCycle thrust sizing (Fn_DES) changed, not the cycle; not traced.
7. **N_GR has engine.* values on only 10 of 20 iterations** (`transitions_pivot.csv` T4 = 10) although all 20 flew.
8. **N_IF iteration 12 completed but did not fly** (`contamination.csv`): a completed run with no flown mission.
9. Best-converged entries for O_GR (7), N_IF (17) and N_GR (11), N_GR 11's fuel-limit hit, and the lowest GTOW of
   O_GR / N_IF / N_GR all sit on known-flawed iterations (B271 / B272). A version of tables 2.3 / 2.4 / 2.8 that
   excludes the flawed iterations was not computed here.
10. First iteration-1 mission fuel is identical across combinations sharing a handler path (S_SP = S_IF 21,876.2;
    S_GR = O_GR = N_GR 21,788.5; O_SP = O_IF 22,914.1 kg). Consistent with a deterministic first flight from the
    common anchor, but it means iteration 1's first mission is not an independent outcome per combination.
11. S_GR flew 19 of 20 iterations below the stated 100 m² minimum (75.0-95.0 m², snippet B) with fuel up to 51,836.1 kg and engine
    scale up to 4.21; its mean fuel (+165.3 % vs baseline) dominates the "sequential" and "graph-routed" pooled means.

### Possible bugs / surprises (sections 4–5)

1. **N_GR stop reason is missing on 17 of 20 iterations** (`graph_stop_reasons.csv`; old 4 of 20).
   - S_GR and O_GR record a reason on 20/20.
   - Possibly a `b985d28` (networked worktree) recording difference. Not checked in the code.
2. **Every networked `convergence_classification` is `incoherent_team` or `joint_myopia`.**
   - This means the eval-success input was false on all 36 completed networked iterations, including the 35 that flew.
   - The classification is therefore not informative about outcome. Only the 0.6 threshold matters.
   - The eval flag fed to `org_theory_metrics` is not the flight result. This needs a code check.
3. **S_SP aerodynamics stage fails its completion criterion in 20/20 iterations, O_SP in 0/20.**
   - The criterion that names `run_simulation` (07 §4c) appears not to be fixed for the sequential pipeline.
   - This lowers S_SP worker completion to 0.771: 0.90 without the aero stage.
4. **Methodology ordering breaks in the networked structure** on medians (N_IF 176 > N_GR 156.5).
   - N_GR's median steps fell from 393.5 to 156.5.
   - Only N_GR changed pin this much (v4net → `b985d28`), so a code effect cannot be separated from a handler effect.
5. **N_GR "peers taking steps" has min 1** (mean 1.95, 39 peer-iterations of 40): one iteration ran with a single peer.
   - The printed outputs do not name the iteration.
6. **Two iterations ran longer than 480 min without being flagged as timed out:** O_GR max 12.45 h and N_GR max
   10.25 h (`status_timeouts.csv`: `durations_ge_480min` = 1, `timed_out` = 0).
   - Either the timeout rule was lifted for this run or it did not fire. Section 0 item 10 should confirm which.
7. **N_GR has 1 generation with thinking content** (236 output tokens, 258 thinking chars) while
   `gens_thinking_enabled` = 0 (`generation_coverage.csv`). Possibly the model emitted a think block unprompted.
8. **N_IF extra runner attempts:** 14 iterations needed a second or third runner attempt (old 5), all after zero fuel.
   - 968 agent calls and 46.2 h of span sit in earlier attempts that no per-iteration metric counts.
9. **N_IF step time is 2.48× its duration** (mean, n=16; N_GR 1.31×), because the peers run in parallel.
   - `max_concurrent_generations` is 0 for both networked chains (sequential and orchestrated: 1).
   - So the concurrency field is not recorded on the `b985d28` networked code. The old run did not report it either.
10. **Five coordination trends reach p < 0.05** (old 1), four of them in O_GR.
    - The O_GR effort decline coincides with the B271-flawed early iterations (5, 7, 8) and a 12.45 h early iteration.
    - The decline may reflect those problems rather than learning.
11. **The verdict mix changed sharply:** RETRY 30 → 2 and COMPLETE 2 → 10 carried.
    - Along with the loop fix, the prompt or integrator logic likely changed between pins. Section 0 should confirm.
12. **`od.cost_per_agent` in O_GR rose to 115 s (74.2), while O_SP and O_IF fell.**
    - It is the summed orchestrator-message duration ÷ workers. O_GR's orchestrator emits ~1 message per iteration, so
      this tracks iteration length more than per-worker cost.

### Possible bugs / surprises (section 6)

1. **The agents set wing areas below the prompt's 100 m² floor.** There are 4 followed items at 95, 90 and 85 m²,
   and S_GR iteration 3 reached 75.0 m². The geometry gate (20–600 m²) does not enforce the stated range. Check
   whether those iterations' fuel numbers are physical.
2. **O_GR design CL flip-flops** between exactly 0.6238746855 and 0.7156362038 over iterations 17–20. These are
   identical SU2 cl_cruise values, so they look like reused or stale SU2 results rather than new solves.
   Possibly related to B271. They also produce 2 of the 3 "followed" not-actionable items.
3. **SU2 config error in O_GR iteration 19.** The aerodynamics analyst's `update_config_entries LIFT_COEFFICIENT
   "0.58"` made `SU2_CFD` exit with code 1 (config error). The key is not protected, but SU2 rejects it.
4. **Mean and median disagree in the converged-only rows.** The followed group's converged-only mean fuel change is
   positive (+17.8 kg, n = 54) while the median is −538.8 kg. A few large swings dominate the mean, including the
   flawed N_IF 18 (+17,837 kg) and N_IF 20 (−19,673 kg).
5. **The role guard still refuses the designated networked recorder.** In N_GR, 116 of 146 calls are refused,
   including calls from agent_1, the only N_GR recorder.
6. **The ladder script calls aviary sweep actionable**, but this run's prompt says "follows geometry". This has no
   effect here (0 sweep items) but should be fixed before reuse.
7. **The script's refusal count reads one attempt per iteration.** Counting all attempt files adds 12 N_IF calls
   and 1 O_SP call.
8. **No numeric targets in two combinations.** O_SP produced 0 scorable items (it recommends GTOW or payload
   changes with no parameter), and N_IF's items are all direction-only. Their "followed" rates are not comparable
   with the sequential combinations.

