"""Build one tidy table (one row per combination x link) for the 20-link DLR-F25 study.

Read-only on the run folders. Every column is read from a saved file; nothing is interpolated.
A value that a link did not record stays empty (NaN / None) instead of being approximated.

Sources (see results/HANDOFF_METRICS_20LINK.md section 3):
    result.json, knowledge_base.jsonl, step_log_attempt<k>.jsonl (highest k = counted attempt),
    design.json, carry_over.json, plus the folder's stat_progress.json.

Usage (on aipexws8, from the Avion venv):
    LINK20_STUDY is not read here: pass the study explicitly.
    python -m analysis.link20.extract --study final --out /data/Jessica/<somewhere>/out
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path

import pandas as pd

from analysis.link20 import flown

HOME = Path.home()

# (dataset, root folder, combinations to take from it). The older 7-link networked graph-routed run in
# v3combos was hit by B187 on links 2-7 and is not used (handoff 3.1).
SOURCES_20261028 = [
    ("v2full", HOME / "Jessica/Avion/MAS-Aviary/logs/stat_results/v2full_qwen_nothink_aipexws8", [
        "mdo_f25_sequential_staged_pipeline", "mdo_f25_sequential_iterative_feedback",
        "mdo_f25_sequential_graph_routed", "mdo_f25_orchestrated_staged_pipeline",
        "mdo_f25_orchestrated_iterative_feedback"]),
    ("v3combos", HOME / "Jessica/avion_analysis/data/v3combos_from_ws3/stat_results", [
        "mdo_f25_orchestrated_graph_routed", "mdo_f25_networked_iterative_feedback"]),
    ("v4net", HOME / "Jessica/Avion/MAS-Aviary/logs/stat_results/v4net_graph_qwen_nothink_aipexws8", [
        "mdo_f25_networked_graph_routed"]),
]

# The re-run after the B3 / B231 / B241 ... B271 fixes (Avion cb20c60 .. 2e0b5e8), launched 2026-11-10. Each
# combination ran on its own machine; the run folders were copied unchanged to aipexws8 /data on 2026-11-12.
L20_20261112_ROOT = Path("/data/Jessica/l20_20261112/runs")
SOURCES_20261112 = [
    ("l20_seq_staged", L20_20261112_ROOT / "l20_seq_staged", ["mdo_f25_sequential_staged_pipeline"]),
    ("l20_seq_iter", L20_20261112_ROOT / "l20_seq_iter", ["mdo_f25_sequential_iterative_feedback"]),
    ("l20_seq_graph", L20_20261112_ROOT / "l20_seq_graph", ["mdo_f25_sequential_graph_routed"]),
    ("l20_orch_staged_iter", L20_20261112_ROOT / "l20_orch_staged_iter", [
        "mdo_f25_orchestrated_staged_pipeline", "mdo_f25_orchestrated_iterative_feedback"]),
    ("l20_orch_graph", L20_20261112_ROOT / "l20_orch_graph", ["mdo_f25_orchestrated_graph_routed"]),
    ("l20c_net_iter", L20_20261112_ROOT / "l20c_net_iter", ["mdo_f25_networked_iterative_feedback"]),
    ("l20c_net_graph", L20_20261112_ROOT / "l20c_net_graph", ["mdo_f25_networked_graph_routed"]),
]
# The finished re-run (all 8 combinations, 20 iterations each; the networked chains are the l20c relaunch after
# B268/B269), assembled unchanged on aipexws8 /data/Jessica/l20_final on 2026-11-17: the five non-networked runs from
# the 2026-11-12 copy (already finished then), the two networked runs from /data/avion_study/code, console logs in logs/.
L20_FINAL = Path("/data/Jessica/l20_final")
SOURCES_FINAL = [(ds, L20_FINAL / "runs" / root.name, combos) for ds, root, combos in SOURCES_20261112]
# The 2-iteration validation of the final pinned code (MAS-Aviary 140865a, 2026-11-18;
# .claude/planning/FINAL_RERUN_VALIDATION1_2026-11-18.md). On aipexws8: the ws8 runs in place, the ws5 / ws6 runs as the
# ws8 copies made by the validation check, the ws3 run (S_IF) as its ws8 copy. O_IF and N_IF were still running when
# copied/checked (link 2 partial; extract_link marks a link without result.json by run_status).
FINAL_VAL = Path("/data/Jessica/final_val")
_VAL_COPIES = FINAL_VAL / "analysis_check" / "copies"


def _launcher_results(base: Path, tag: str) -> Path:
    """A run tag's stat_results folder in the final launch layout: <base>/runs/<tag>/MAS-Aviary/logs/stat_results/<tag>."""
    return base / "runs" / tag / "MAS-Aviary" / "logs" / "stat_results" / tag


SOURCES_FINAL_VAL = [
    ("val_seq_staged", _launcher_results(_VAL_COPIES / "ws5", "val_seq_staged"), ["mdo_f25_sequential_staged_pipeline"]),
    ("val_seq_iter", _launcher_results(FINAL_VAL / "ws3_copy", "val_seq_iter"),
     ["mdo_f25_sequential_iterative_feedback"]),
    ("val_seq_graph", _launcher_results(_VAL_COPIES / "ws5", "val_seq_graph"), ["mdo_f25_sequential_graph_routed"]),
    ("val_orch_staged", _launcher_results(_VAL_COPIES / "ws6", "val_orch_staged"),
     ["mdo_f25_orchestrated_staged_pipeline"]),
    ("val_orch_iter", _launcher_results(_VAL_COPIES / "ws6", "val_orch_iter"),
     ["mdo_f25_orchestrated_iterative_feedback"]),
    ("val_orch_graph", _launcher_results(FINAL_VAL, "val_orch_graph"), ["mdo_f25_orchestrated_graph_routed"]),
    ("val_net_iter", _launcher_results(FINAL_VAL, "val_net_iter"), ["mdo_f25_networked_iterative_feedback"]),
    ("val_net_graph", _launcher_results(FINAL_VAL, "val_net_graph"), ["mdo_f25_networked_graph_routed"]),
]

# The final 20-iteration run: a placeholder until launch. Its root is given at analysis time with
# LINK20_FINAL_RERUN_ROOT (the folder holding the run tags, e.g. /data/Jessica/final_run or the copy of the runs
# on aipexws8); the run tags and combinations are discovered there. Unset or empty: the study is refused.
FINAL_RERUN_ROOT_ENV = "LINK20_FINAL_RERUN_ROOT"


def discover_sources(root: Path) -> list:
    """(dataset, stat_results folder, combinations) for every run tag under root, in the launcher layout
    (<root>/runs/<tag>/MAS-Aviary/logs/stat_results/<tag>, or <root>/<tag>/MAS-Aviary/logs/stat_results/<tag>) or as
    plain stat_results folders (<root>/<tag>/repeat_*/<combination>, the l20_final copy layout)."""
    root = Path(root)
    found = {}
    patterns = ("runs/*/MAS-Aviary/logs/stat_results/*", "*/MAS-Aviary/logs/stat_results/*", "runs/*", "*")
    candidates = [p for pat in patterns
                  for p in sorted(root.glob(pat)) if p.is_dir()]
    for d in candidates:
        combos = sorted({c.name for c in d.glob("repeat_*/mdo_f25_*") if c.is_dir()})
        if combos and d.name not in found:
            found[d.name] = (d.name, d, combos)
    return list(found.values())


def _final_rerun_sources() -> list:
    root = os.environ.get(FINAL_RERUN_ROOT_ENV)
    if not root:
        raise SystemExit(f"Study 'final_rerun' needs {FINAL_RERUN_ROOT_ENV} = the final run's root folder (set at launch).")
    sources = discover_sources(Path(root))
    if not sources:
        raise SystemExit(f"{FINAL_RERUN_ROOT_ENV}={root}: no run folder with repeat_*/mdo_f25_* found.")
    return sources


class _Studies(dict):
    """Study name -> sources. A callable entry (the final run's placeholder) is resolved when it is read."""

    def __getitem__(self, key):
        value = super().__getitem__(key)
        return value() if callable(value) else value


STUDIES = _Studies({"20261028": SOURCES_20261028, "20261112": SOURCES_20261112, "final": SOURCES_FINAL,
                    "final_val": SOURCES_FINAL_VAL, "final_rerun": _final_rerun_sources})
# Each study's own baseline mission (the unchanged anchor design, flown once): report scripts compare against this.
# 20261028: flown 2026-10-22 (~/Jessica/avion_analysis/baseline/baseline_run1.json); re-run: flown 2026-11-12
# (/data/Jessica/l20_20261112/baseline/baseline_run1.json), same anchor and mission, aviary-mcp a5ef294.
# A new study needs its own entry here (and in STUDIES); nothing falls back to another study's baseline.
_BASE_OLD = {"fuel_kg": 18197.0, "gtow_kg": 91660.6, "wing_mass_kg": 8815.2}
_BASE_NEW = {"fuel_kg": 16390.5, "gtow_kg": 89682.6, "wing_mass_kg": 8664.2}
# The final pinned code's coupled baseline (B1 decision: the unchanged anchor flown through the coupled chain), flown
# twice bit-identically on 2026-11-18 on the final pins (MAS-Aviary 2741cf2 + the closed fixed point ca77050, tigl-mcp
# b5ae37a, aviary-mcp 36b3a53, mass-mcp a3429f6, su2-mcp 76fdaf8, pycycle-mcp 001456b): ws8
# /data/Jessica/final_fix/baseline3/results/flights/B3_coupled_anchor_{1,2}.json (fuel 15,719.17623923756 kg, GTOW
# 90,309.99671587195 kg, wing 7,958.785 kg, converged). Was baseline2 B2_coupled_anchor_{1,2} (15,719.17609579942 /
# 90,310.00549723483 kg): +0.00014 kg fuel, -0.0088 kg GTOW from closing the mass <-> engine fixed point exactly.
# The final run uses the same pinned code; if its pins change, re-fly the baseline and give final_rerun its own entry.
_BASE_FINAL_CODE = {"fuel_kg": 15719.18, "gtow_kg": 90310.0, "wing_mass_kg": 7958.8}
BASELINES = {"20261028": _BASE_OLD, "20261112": _BASE_NEW, "final": _BASE_NEW,
             "final_val": _BASE_FINAL_CODE, "final_rerun": _BASE_FINAL_CODE}
# Baseline values above are rounded to 0.1 kg; a baseline mission file must agree to within this.
BASELINE_FILE_TOL_KG = 0.05


def require_study(study: str | None) -> str:
    """The study name, refused unless it is given and known (there is no default study)."""
    if not study:
        raise SystemExit("No study selected: set LINK20_STUDY (or pass --study) to one of "
                         f"{sorted(STUDIES)}. There is no default; each study has its own runs and baseline.")
    if study not in STUDIES or study not in BASELINES:
        raise SystemExit(f"Unknown study {study!r}: known studies {sorted(STUDIES)}")
    return study


def env_study() -> str:
    """The study the report scripts read: LINK20_STUDY, required."""
    return require_study(os.environ.get("LINK20_STUDY"))


def __getattr__(name: str):
    # STUDY / SOURCES / BASELINE are the env-selected study's (report scripts import them); reading them without
    # LINK20_STUDY set stops with an error instead of silently using another study's runs or baseline.
    if name == "STUDY":
        return env_study()
    if name == "SOURCES":
        return STUDIES[env_study()]
    if name == "BASELINE":
        return BASELINES[env_study()]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def baseline_fuel_from_file(path: Path) -> float:
    """The fuel of a baseline mission file: baseline_mission.py's (run_simulation.summary.fuel_burned_kg) or the
    coupled-baseline driver's flight record (top-level fuel_burned_kg, refused unless it converged)."""
    doc = json.loads(Path(path).read_text())
    if "run_simulation" in doc:
        return float(doc["run_simulation"]["summary"]["fuel_burned_kg"])
    if doc.get("fuel_burned_kg") is not None:
        if doc.get("converged") is not True:
            raise SystemExit(f"Baseline flight {path} did not converge (converged={doc.get('converged')!r}).")
        return float(doc["fuel_burned_kg"])
    raise SystemExit(f"{path} is not a baseline mission file (no run_simulation summary, no fuel_burned_kg).")


def checked_baseline_fuel(path: Path, study: str) -> float:
    """A baseline mission file's fuel, refused unless it is the given study's baseline (BASELINES)."""
    study = require_study(study)
    fuel = baseline_fuel_from_file(path)
    want = BASELINES[study]["fuel_kg"]
    if abs(fuel - want) > BASELINE_FILE_TOL_KG:
        raise SystemExit(f"Baseline file {path} has fuel {fuel:.3f} kg, but study {study!r}'s baseline is "
                         f"{want:.1f} kg: wrong baseline file for this study.")
    return fuel


def check_links_study(links: pd.DataFrame, study: str, path=None) -> None:
    """Refuse a links.csv that was not extracted for the given study (its `study` column)."""
    study = require_study(study)
    if "study" not in links.columns:
        raise SystemExit(f"{path or 'links table'} has no `study` column: re-extract it with "
                         "`python -m analysis.link20.extract --study ...`.")
    found = set(links["study"].dropna().astype(str))
    if found != {study}:
        raise SystemExit(f"{path or 'links table'} is from study {sorted(found)}, not {study!r}.")

# Same floor the runner uses to call a mission "zero fuel" (scripts/stat_batch_runner.py).
ZERO_FUEL_FLOOR_KG = 100.0

# B187: an unsized pyCycle run reports the seeded thrust 5,900 lbf and the engine shrinks to ~0.994.
B187_FN_LBF = 5900.0
B187_SCALE = 0.9939353099730406

# Pipeline order of the roles, used to count backward moves on a route.
ROLE_ORDER = ["geometry_engineer", "aerodynamics_analyst", "structures_analyst", "propulsion_analyst",
              "mission_architect", "simulation_executor", "mdo_integrator"]

LEVELS = {"simple": 1, "moderate": 2, "complex": 3}

OD_METRICS = ["misroute_rate", "ambidexterity_score", "completion_rate", "propagation_time", "cost_per_agent",
              "convergence_score", "orchestrator_overhead_ratio", "information_ratio", "blackboard_utilization",
              "duplicate_work_rate", "escalation_length", "stage_independence_score"]

CHAIN_PARAMS = ["Aircraft.Wing.AREA", "Aircraft.Wing.ASPECT_RATIO", "Aircraft.Wing.SWEEP",
                "Aircraft.Wing.TAPER_RATIO", "Aircraft.Fuselage.LENGTH", "Aircraft.Fuselage.MAX_HEIGHT",
                "Aircraft.Fuselage.MAX_WIDTH", "Aircraft.Engine.SCALE_FACTOR"]
ENGINE_PARAMS = ["T4_MAX", "splitter.BPR", "fan.PR", "lpc.PR", "hpc.PR"]
APPLIED_PARAMS = ["Mission.Design.LIFT_COEFFICIENT", "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR",
                  "Aircraft.Wing.MASS_SCALER"]

# Coordination tools that live only in the step logs (not in the knowledge base).
BOARD_TOOLS = ["read_todos", "claim_todo", "mark_todo_done", "mark_todo_failed", "release_todo",
               "wait_for_board", "read_blackboard", "write_blackboard", "spawn_peer", "request_complexity"]
ORCH_TOOLS = ["create_agent", "assign_task", "message_orchestrator", "reply_to_agent", "set_complexity"]
SIGNAL_TOOLS = ["record_recommendation", "respond_to_recommendation"]


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def counted_step_log(link_dir: Path) -> tuple[int, Path | None]:
    """The highest step_log_attempt<k>.jsonl is the attempt that counted."""
    logs = [(int(m.group(1)), p) for p in link_dir.glob("step_log_attempt*.jsonl")
            if (m := re.fullmatch(r"step_log_attempt(\d+)\.jsonl", p.name))]
    return max(logs) if logs else (0, None)


def mission_summaries(kb: list[dict], converged_only: bool = False) -> list[dict]:
    """Every successful run_simulation / get_results that returned a fuel figure, in KB order.

    Mirrors measured_fuel_from_kb in scripts/stat_batch_runner.py (the last one is the link's fuel).
    converged_only (contract flops_drag_b324, MAS-Aviary B314): a mission counts only when it converged, and a
    get_results only after a counted run_simulation on the same session (the runner's rule since B314).
    """
    out = []
    counted_session: dict = {}
    for e in kb:
        if e.get("tool") not in ("run_simulation", "get_results"):
            continue
        outputs = e.get("outputs") if isinstance(e.get("outputs"), dict) else {}
        if converged_only:
            sid = (e.get("inputs") or {}).get("session_id")
            ok = (e.get("status") == "success" and outputs.get("success") is not False
                  and outputs.get("converged") is not False)
            if e["tool"] == "run_simulation" and e.get("status") != "refused" \
                    and error_code(e) not in flown.NOT_A_MISSION_CODES:     # B359: no mission ran
                # a run that reached aviary decides what the session's results are, whether it counted or not
                counted_session[sid] = ok and outputs.get("converged") is True
            if not ok or not counted_session.get(sid):
                continue
        elif e.get("status") != "success":
            continue
        for holder in (outputs.get("summary"), outputs.get("outputs"), outputs):
            if isinstance(holder, dict) and holder.get("fuel_burned_kg") is not None:
                out.append({"seq": e.get("seq"), "tool": e["tool"], "attempt": int(e.get("attempt") or 1), **{
                    k: holder.get(k) for k in ("fuel_burned_kg", "gtow_kg", "wing_mass_kg")}})
                break
    return out


def b187_hits(kb: list[dict]) -> list[str]:
    hits = []
    for e in kb:
        if e.get("tool") == "run_cycle" and e.get("status") == "success":
            fn = ((e.get("outputs") or {}).get("outputs") or {}).get("perf.Fn")
            if isinstance(fn, (int, float)) and abs(fn - B187_FN_LBF) < 1.0:
                hits.append(f"run_cycle Fn={fn:.1f} (seq {e.get('seq')})")
        if e.get("tool") == "set_aircraft_parameters" and e.get("status") == "success":
            sf = ((e.get("inputs") or {}).get("parameters") or {}).get("Aircraft.Engine.SCALE_FACTOR")
            if isinstance(sf, (int, float)) and abs(sf - B187_SCALE) < 5e-4:
                hits.append(f"SCALE_FACTOR={sf:.4f} (seq {e.get('seq')})")
    return hits


def b214_hits(kb: list[dict]) -> list[str]:
    return [f"export_cpacs -> {p} (seq {e.get('seq')})" for e in kb
            if e.get("tool") == "export_cpacs"
            and "tests/fixtures/" in (p := str((e.get("inputs") or {}).get("output_path", "")))]


def error_code(entry: dict) -> str:
    outputs = entry.get("outputs") if isinstance(entry.get("outputs"), dict) else {}
    code = entry.get("error_code") or outputs.get("error_code")
    return str(code) if code else "unspecified"


def backward_moves(route: list[str]) -> int | None:
    idx = [ROLE_ORDER.index(r) for r in route if r in ROLE_ORDER]
    if not idx:
        return None
    return sum(1 for a, b in zip(idx, idx[1:]) if b < a)


def extract_link(dataset: str, combo: str, link: int, link_dir: Path, progress: dict,
                 pycycle_defaults: dict | None = None) -> dict:
    structure, handler = combo.removeprefix("mdo_f25_").split("_", 1)
    key = f"r{link - 1:03d}_{combo}"
    res = read_json(link_dir / "result.json")
    design = read_json(link_dir / "design.json") or {}
    carry = read_json(link_dir / "carry_over.json")
    kb = read_jsonl(link_dir / "knowledge_base.jsonl")
    attempt_k, step_path = counted_step_log(link_dir)
    steps = [s for s in read_jsonl(step_path) if s.get("kind") == "step"] if step_path else []

    if key in progress.get("failed", {}):
        run_status = "failed"
    elif key in progress.get("completed", {}):
        run_status = "completed"
    else:
        run_status = "in_progress"
    fail = progress.get("failed", {}).get(key) or {}

    row: dict = {"dataset": dataset, "combination": combo, "structure": structure, "handler": handler,
                 "link": link, "run_status": run_status, "fail_error": fail.get("error"),
                 "has_result": res is not None}
    res = res or {}
    ec = res.get("eval_classification") or {}

    # --- fuel and constraints (measured from the knowledge base) ---
    design_flown = read_json(link_dir / "design_flown.json") or {}
    contract, contract_why = flown.detect_contract(design, design_flown, kb)
    row["contract"], row["contract.detail"] = contract, contract_why
    missions = mission_summaries(kb, converged_only=contract == flown.CONTRACT_FLOPS)
    last = missions[-1] if missions else {}
    fuel = last.get("fuel_burned_kg")
    flew = fuel is not None and float(fuel) > ZERO_FUEL_FLOOR_KG
    row.update({
        "fuel_source": res.get("fuel_source"), "eval_result": ec.get("result"),
        "flew": flew, "fuel_kg": float(fuel) if flew else None,
        "gtow_kg": float(last["gtow_kg"]) if flew and last.get("gtow_kg") is not None else None,
        "wing_mass_kg": float(last["wing_mass_kg"]) if flew and last.get("wing_mass_kg") is not None else None,
        "first_fuel_kg": float(missions[0]["fuel_burned_kg"]) if missions else None,
        "n_missions_flown": sum(1 for m in missions if float(m["fuel_burned_kg"]) > ZERO_FUEL_FLOOR_KG),
        "fuel_measured_kg_result": res.get("fuel_measured_kg"),
        "chain_start_fuel_kg": res.get("chain_start_fuel_kg"),
        "ec_gtow_kg": ec.get("gtow_kg"), "ec_converged": ec.get("converged"),
    })
    # B359: RUN_IN_PROGRESS / RUN_STILL_STOPPING / INVALID_TIMEOUT replies are not missions: counted apart, never as
    # failed or refused (they never occur before the final re-run, so older studies count as before).
    sims = Counter(e.get("status") for e in kb if e.get("tool") == "run_simulation"
                   and error_code(e) not in flown.NOT_A_MISSION_CODES)
    row.update({f"run_simulation_{s}": sims.get(s, 0) for s in ("success", "failed", "refused")})
    row["run_simulation_not_run"] = sum(1 for e in kb if e.get("tool") == "run_simulation"
                                        and error_code(e) in flown.NOT_A_MISSION_CODES)

    # --- general ---
    out_tok = Counter()
    for s in steps:
        out_tok[s.get("agent")] += sum(g.get("output_tokens") or 0 for g in s.get("generations") or [])
    row.update({
        "duration_s": res.get("duration_seconds"), "total_turns": res.get("total_turns"),
        "total_tokens_recorded": res.get("total_tokens"), "output_tokens": sum(out_tok.values()),
        "agent_steps": len(steps), "n_agents_stepping": len({s.get("agent") for s in steps}),
        "orchestrator_steps": sum(1 for s in steps if s.get("agent") == "orchestrator"),
        "attempt": res.get("attempt") if res else fail.get("attempts"), "step_log_attempts": attempt_k,
        "max_prompt_tokens": res.get("max_prompt_tokens"),
    })

    # --- where the time goes ---
    # Wall time of each agent step (one model generation plus the tool call it makes), credited to the tool the
    # step called; a step calling several tools is split evenly. Per-tool durations in result.json are all 0 and
    # no CPU time is recorded, so model and tool time inside a step can't be separated here.
    by_tool = Counter()
    for s in steps:
        if s.get("start_time") is None or s.get("end_time") is None:
            continue
        dt = float(s["end_time"]) - float(s["start_time"])
        names = [tc.get("name") for tc in s.get("tool_calls") or [] if isinstance(tc, dict)] or ["(no tool call)"]
        for n in names:
            by_tool[n] += dt / len(names)
    row["step_time_s"] = sum(by_tool.values())
    row["step_time_by_tool"] = json.dumps({k: round(v, 3) for k, v in by_tool.items()})
    row["tool_servers"] = json.dumps({e["tool"]: e.get("server") for e in kb if e.get("server")})

    # --- tool calls ---
    row["kb_tool_calls"] = sum(1 for e in kb if e.get("agent") != "framework")
    refused = [e for e in kb if e.get("status") == "refused"]
    row["kb_refused"] = len(refused)
    row["kb_failed"] = sum(1 for e in kb if e.get("status") == "failed")
    row["refused_by_code"] = json.dumps(dict(Counter(error_code(e) for e in refused)))
    step_tools = Counter(tc.get("name") for s in steps for tc in s.get("tool_calls") or []
                         if isinstance(tc, dict))
    for t in BOARD_TOOLS + ORCH_TOOLS + SIGNAL_TOOLS:
        row[f"n_{t}"] = step_tools.get(t, 0)

    # --- recommendations and verdicts ---
    signals = design.get("coordination_signals") or []
    recs = [s for s in signals if s.get("kind") == "recommendation"]
    row["n_recommendations"] = len(recs)
    row["n_responses"] = sum(1 for s in signals if s.get("kind") == "response")
    verdicts = Counter(str(s.get("verdict")).upper() for s in recs)
    for v in ("COMPLETE", "CONTINUE", "RETRY"):
        row[f"verdict_{v.lower()}"] = verdicts.get(v, 0)
    row["last_verdict"] = recs[-1].get("verdict") if recs else None
    row["last_rec_to"] = recs[-1].get("recommendation_to") if recs else None
    row["last_rec_change"] = recs[-1].get("recommended_change") if recs else None
    inherited = (carry or {}).get("recommendation") or {}
    row["inherited_rec_to"] = inherited.get("recommendation_to")
    row["inherited_rec_change"] = inherited.get("recommended_change")
    row["inherited_rec_verdict"] = inherited.get("verdict")

    # --- handler-specific ---
    msgs = res.get("messages") or []
    meta = [m.get("metadata") or {} for m in msgs]
    row["n_retry_messages"] = sum(1 for m in msgs if m.get("is_retry"))
    row["stuck_detected"] = sum(1 for m in meta if m.get("stuck_detected"))
    row["early_abort"] = sum(1 for m in meta if m.get("early_abort"))
    otm = res.get("org_theory_metrics") or {}
    row["mean_attempts_to_success"] = otm.get("mean_attempts_to_success")
    row["stage_count"] = otm.get("stage_count")
    psc = otm.get("per_stage_completion") or []
    row["stages_not_met"] = sum(1 for s in psc if not s.get("met")) if psc else None
    # Staged stages run more than once (the orchestrator's own turns are not stages).
    stage_names = [m.get("stage_name") for m in meta if m.get("stage_name") not in (None, "orchestrator")]
    row["stages_redone"] = len(stage_names) - len(set(stage_names)) if stage_names else None
    row["graph_stop_reason"] = next((m.get("graph_stop_reason") for m in meta if m.get("graph_stop_reason")), None)
    complexity = [s for s in signals if s.get("kind") in ("complexity", "complexity_set")]
    row["n_complexity_signals"] = len(complexity)
    row["n_complexity_set"] = sum(1 for s in complexity if s.get("kind") == "complexity_set")
    # Sequential / orchestrated graph-routed: one graph_route record per role run, in message metadata.
    turns = [m["graph_route"] for m in meta if isinstance(m.get("graph_route"), dict)]
    gr = design.get("graph_route") or {}
    if turns:
        nodes = [t.get("node") for t in turns]
        row["route_length"] = len(nodes)
        row["backward_moves"] = backward_moves(nodes)
        row["graph_level"] = turns[-1].get("next_level") or turns[-1].get("level")
        row["graph_level_changes"] = sum(1 for t in turns if LEVELS.get(t.get("next_level"), 0) > LEVELS.get(t.get("level"), 0))
    elif gr:
        # Networked graph-routed: the router's own record in design.json (peers, not roles, take turns).
        log = gr.get("log") or []
        row["route_length"] = gr.get("role_runs")
        row["graph_level"] = gr.get("level")
        row["graph_stop_reason"] = row["graph_stop_reason"] or gr.get("stop_reason")
        row["graph_level_changes"] = sum(1 for e in log if e.get("event") == "level"
                                         and LEVELS.get(e.get("to"), 0) > LEVELS.get(e.get("from"), 0))
        row["graph_reopens"] = sum(1 for e in log if e.get("event") == "reopen")
    claims = [o for s in steps for tc in s.get("tool_calls") or [] if tc.get("name") == "claim_todo"
              for o in re.findall(r'"success":\s*(true|false),\s*"todo_name"', str(s.get("observations")))]
    row["n_claim_granted"] = claims.count("true")
    row["n_claim_refused"] = claims.count("false")
    for k in ("orchestrator_turns", "worker_turns", "agents_spawned", "blackboard_reads_total",
              "blackboard_writes_total", "claim_conflicts"):
        row[k] = otm.get(k)
    # Organisational-design metrics as the run recorded them (src/logging/org_theory_metrics.py; every
    # definition predates the earliest run, so all combinations share them).
    for k in OD_METRICS:
        row[f"od.{k}"] = otm.get(k)
    # Staged completion over worker stages only: orchestrated staged pipeline also lists the orchestrator's
    # own turns as stages, scored by a heuristic that always passes.
    worker_stages = [s for s in psc if s.get("stage") != "orchestrator"]
    row["od.completion_rate_workers"] = (sum(1 for s in worker_stages if s.get("met")) / len(worker_stages)
                                         if worker_stages else None)
    # Networked analogue of the misroute rate: the recorded one counts peers (agent_1, agent_2), not roles.
    # Here, the share of granted claims that take a TODO already claimed earlier in the link.
    granted = [n for s in steps for tc in s.get("tool_calls") or [] if tc.get("name") == "claim_todo"
               for n in re.findall(r'"success":\s*true,\s*"todo_name":\s*"([^"]+)"', str(s.get("observations")))]
    row["od.todo_revisit_rate"] = (sum(1 for i, n in enumerate(granted) if n in granted[:i]) / (len(granted) - 1)
                                   if structure == "networked" and len(granted) > 1 else None)
    row["claim_violations_total"] = res.get("claim_violations_total")

    # --- what the counted mission flew (D1) ---
    # Every coupled column (wing, fuselage, engine scale, CL, drag factor, wing mass, SU2 CL/CD, takeoff mass,
    # engine deck) is the input of the counted run_simulation, with its source in src.<column>; plus one flag per
    # bug signature of 11_bug_checks.md (analysis/link20/flown.py).
    def steps_of(k: int) -> list[dict]:
        p = link_dir / f"step_log_attempt{k}.jsonl"
        return [s for s in read_jsonl(p) if s.get("kind") == "step"]

    row.update(flown.flown_link(link_dir, kb, last, res, carry, steps_of,
                                pycycle_defaults if pycycle_defaults is not None else {}, contract, design_flown))
    # Every refusal / failure code of the link (all attempts), e.g. MISSION_NOT_CONVERGED, TAIL_FROM_GEOMETRY.
    codes = Counter(error_code(e) for e in kb if e.get("status") in ("refused", "failed"))
    row["error_codes"] = json.dumps({c: n for c, n in codes.items() if c not in flown.NOT_A_MISSION_CODES},
                                    sort_keys=True)
    row["not_a_mission_codes"] = json.dumps({c: n for c, n in codes.items() if c in flown.NOT_A_MISSION_CODES},
                                            sort_keys=True)

    # --- what the link LEFT (end state, carried to the next link) and design.json's last values ---
    # Not what flew: a link may change the design after its counted mission (O_IF 13, N_GR 2 ...).
    end = res.get("chain_end_params") or {}
    for p in CHAIN_PARAMS:
        row[f"end.{p}"] = end.get(p)
    eng = (design.get("design_space") or {}).get("engine") or {}
    for p in ENGINE_PARAMS:
        row[f"engine.{p}"] = eng.get(p)
    applied = design.get("mission_applied") or {}
    for p in APPLIED_PARAMS:
        row[f"design_json.{p}"] = applied.get(p)
    aero, struct, prop = design.get("aero") or {}, design.get("structures") or {}, design.get("propulsion") or {}
    row.update({"design_json.aero.cl_cruise": aero.get("cl_cruise"),
                "design_json.aero.cd_cruise": aero.get("cd_cruise"),
                "design_json.structures.mtom_kg": struct.get("mtom_kg"),
                "propulsion.sfc_cruise": prop.get("sfc_cruise"), "propulsion.fn_lbf": prop.get("fn_lbf")})

    # --- checks ---
    b187, b214 = b187_hits(kb), b214_hits(kb)
    row["b187"] = bool(b187)
    # An unsized run_cycle in this link, versus only re-applying a 0.994 scale inherited from the link before.
    row["b187_cycle"] = any(h.startswith("run_cycle") for h in b187)
    row["b187_inherited_only"] = bool(b187) and not row["b187_cycle"]
    row["b187_detail"] = "; ".join(b187[:4])
    row["b214"] = bool(b214)
    row["b214_detail"] = "; ".join(b214[:4])
    return row


def study_pycycle_defaults(sources) -> dict:
    """pyCycle's design-input defaults, from the first get_design_inputs calls recorded in the study (the B270 check
    compares the flown deck's engine with them). Empty keys stay missing; the check then reports 'unknown'."""
    found: dict = {}
    for _dataset, root, combos in sources:
        for combo in combos:
            for p in sorted(root.glob(f"repeat_*/{combo}/tool_calls_attempt*.jsonl")):
                found.update({k: v for k, v in flown.pycycle_defaults(
                    [{"tool": r.get("tool"), "result": r.get("result")} for r in read_jsonl(p)
                     if r.get("tool") == "get_design_inputs"]).items() if k not in found})
                if all(k in found for k in flown.ENGINE_KEYS):
                    return found
    return found


def build_table(sources, study: str) -> pd.DataFrame:
    study = require_study(study)
    defaults = study_pycycle_defaults(sources)
    rows = []
    for dataset, root, combos in sources:
        progress = read_json(root / "stat_progress.json") or {}
        for combo in combos:
            for link_dir in sorted(root.glob(f"repeat_*/{combo}")):
                link = int(link_dir.parent.name.split("_")[1]) + 1
                rows.append({"study": study, **extract_link(dataset, combo, link, link_dir, progress, defaults)})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--study", choices=sorted(STUDIES), required=True,
                    help="20261028 = the first 20-link study; 20261112 = the re-run after the fixes; "
                         "final = the finished re-run (/data/Jessica/l20_final); final_val = the 2-iteration "
                         "validation of the final code; final_rerun = the final 20-iteration run "
                         "(root from LINK20_FINAL_RERUN_ROOT)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    df = build_table(STUDIES[args.study], args.study)
    df.to_csv(args.out / "links.csv", index=False)
    print(f"wrote {len(df)} rows ({args.study}) to {args.out / 'links.csv'}")


if __name__ == "__main__":
    main()
