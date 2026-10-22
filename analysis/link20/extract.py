"""Build one tidy table (one row per combination x link) for the 20-link DLR-F25 study.

Read-only on the run folders. Every column is read from a saved file; nothing is interpolated.
A value that a link did not record stays empty (NaN / None) instead of being approximated.

Sources (see results/HANDOFF_METRICS_20LINK.md section 3):
    result.json, knowledge_base.jsonl, step_log_attempt<k>.jsonl (highest k = counted attempt),
    design.json, carry_over.json, plus the folder's stat_progress.json.

Usage (on aipexws8, from the Avion venv):
    python -m analysis.link20.extract --out ~/Jessica/avion_analysis/out
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

HOME = Path.home()

# (dataset, root folder, combinations to take from it). The older 7-link networked graph-routed run in
# v3combos was hit by B187 on links 2-7 and is not used (handoff 3.1).
SOURCES = [
    ("v2full", HOME / "Jessica/Avion/MAS-Aviary/logs/stat_results/v2full_qwen_nothink_aipexws8", [
        "mdo_f25_sequential_staged_pipeline", "mdo_f25_sequential_iterative_feedback",
        "mdo_f25_sequential_graph_routed", "mdo_f25_orchestrated_staged_pipeline",
        "mdo_f25_orchestrated_iterative_feedback"]),
    ("v3combos", HOME / "Jessica/avion_analysis/data/v3combos_from_ws3/stat_results", [
        "mdo_f25_orchestrated_graph_routed", "mdo_f25_networked_iterative_feedback"]),
    ("v4net", HOME / "Jessica/Avion/MAS-Aviary/logs/stat_results/v4net_graph_qwen_nothink_aipexws8", [
        "mdo_f25_networked_graph_routed"]),
]

# Same floor the runner uses to call a mission "zero fuel" (scripts/stat_batch_runner.py).
ZERO_FUEL_FLOOR_KG = 100.0

# B187: an unsized pyCycle run reports the seeded thrust 5,900 lbf and the engine shrinks to ~0.994.
B187_FN_LBF = 5900.0
B187_SCALE = 0.9939353099730406

# Pipeline order of the roles, used to count backward moves on a route.
ROLE_ORDER = ["geometry_engineer", "aerodynamics_analyst", "structures_analyst", "propulsion_analyst",
              "mission_architect", "simulation_executor", "mdo_integrator"]

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


def mission_summaries(kb: list[dict]) -> list[dict]:
    """Every successful run_simulation / get_results that returned a fuel figure, in KB order.

    Mirrors measured_fuel_from_kb in scripts/stat_batch_runner.py (the last one is the link's fuel).
    """
    out = []
    for e in kb:
        if e.get("tool") not in ("run_simulation", "get_results") or e.get("status") != "success":
            continue
        outputs = e.get("outputs") or {}
        for holder in (outputs.get("summary"), outputs.get("outputs"), outputs):
            if isinstance(holder, dict) and holder.get("fuel_burned_kg") is not None:
                out.append({"seq": e.get("seq"), "tool": e["tool"], **{
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
    code = entry.get("error_code") or (entry.get("outputs") or {}).get("error_code")
    return str(code) if code else "unspecified"


def backward_moves(route: list[str]) -> int:
    idx = [ROLE_ORDER.index(r) for r in route if r in ROLE_ORDER]
    return sum(1 for a, b in zip(idx, idx[1:]) if b < a)


def extract_link(dataset: str, combo: str, link: int, link_dir: Path, progress: dict) -> dict:
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
    missions = mission_summaries(kb)
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
    sims = Counter(e.get("status") for e in kb if e.get("tool") == "run_simulation")
    row.update({f"run_simulation_{s}": sims.get(s, 0) for s in ("success", "failed", "refused")})

    # --- general ---
    out_tok = Counter()
    for s in steps:
        out_tok[s.get("agent")] += sum(g.get("output_tokens") or 0 for g in s.get("generations") or [])
    row.update({
        "duration_s": res.get("duration_seconds"), "total_turns": res.get("total_turns"),
        "total_tokens_recorded": res.get("total_tokens"), "output_tokens": sum(out_tok.values()),
        "agent_steps": len(steps), "n_agents_stepping": len({s.get("agent") for s in steps}),
        "attempt": res.get("attempt") if res else fail.get("attempts"), "step_log_attempts": attempt_k,
        "max_prompt_tokens": res.get("max_prompt_tokens"),
    })

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
    stage_names = [m.get("stage_name") for m in meta if m.get("stage_name")]
    row["stages_redone"] = len(stage_names) - len(set(stage_names)) if stage_names else None
    route = otm.get("states_visited") or []
    row["route_length"] = len(route) if route else None
    row["backward_moves"] = backward_moves(route) if route else None
    row["graph_stop_reason"] = next((m.get("graph_stop_reason") for m in meta if m.get("graph_stop_reason")), None)
    complexity = [s for s in signals if s.get("kind") in ("complexity", "complexity_set")]
    row["n_complexity_signals"] = len(complexity)
    row["n_complexity_set"] = sum(1 for s in complexity if s.get("kind") == "complexity_set")
    gr = design.get("graph_route") or {}
    if gr:
        log = gr.get("log") or []
        row["graph_level"] = gr.get("level")
        row["graph_role_runs"] = gr.get("role_runs")
        row["graph_stop_reason"] = row["graph_stop_reason"] or gr.get("stop_reason")
        row["graph_level_changes"] = sum(1 for e in log if e.get("event") == "level")
        row["graph_reopens"] = sum(1 for e in log if e.get("event") == "reopen")
    for k in ("orchestrator_turns", "worker_turns", "agents_spawned", "blackboard_reads_total",
              "blackboard_writes_total", "claim_conflicts"):
        row[k] = otm.get(k)
    row["claim_violations_total"] = res.get("claim_violations_total")

    # --- design parameters the link left ---
    end = res.get("chain_end_params") or {}
    for p in CHAIN_PARAMS:
        row[p] = end.get(p)
    eng = (design.get("design_space") or {}).get("engine") or {}
    for p in ENGINE_PARAMS:
        row[f"engine.{p}"] = eng.get(p)
    applied = design.get("mission_applied") or {}
    for p in APPLIED_PARAMS:
        row[p] = applied.get(p)
    aero, struct, prop = design.get("aero") or {}, design.get("structures") or {}, design.get("propulsion") or {}
    row.update({"aero.cl_cruise": aero.get("cl_cruise"), "aero.cd_cruise": aero.get("cd_cruise"),
                "structures.mtom_kg": struct.get("mtom_kg"), "propulsion.sfc_cruise": prop.get("sfc_cruise"),
                "propulsion.fn_lbf": prop.get("fn_lbf")})

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


def build_table(sources=SOURCES) -> pd.DataFrame:
    rows = []
    for dataset, root, combos in sources:
        progress = read_json(root / "stat_progress.json") or {}
        for combo in combos:
            for link_dir in sorted(root.glob(f"repeat_*/{combo}")):
                link = int(link_dir.parent.name.split("_")[1]) + 1
                rows.append(extract_link(dataset, combo, link, link_dir, progress))
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    df = build_table()
    df.to_csv(args.out / "links.csv", index=False)
    print(f"wrote {len(df)} rows to {args.out / 'links.csv'}")


if __name__ == "__main__":
    main()
