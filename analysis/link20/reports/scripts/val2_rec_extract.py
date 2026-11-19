"""Validation 2 (final_val2, 8 combinations x 2 iterations): dump every recommendation-related event per run.

Read-only on the run folders. For each of the 16 run directories this writes, under --out:

    <combo>_it<k>_story.txt   time-ordered story: framework coordination calls (record_recommendation,
                              respond_to_recommendation, assign_task, set_complexity, create_agent, blackboard,
                              todos, final_answer) with arguments and observations, the start of each agent turn
                              with its task / user message, the design-changing MCP calls (morph, aviary
                              parameters, cycle inputs, mission) and every run_simulation with its fuel
    <combo>_it<k>_prompts.jsonl   one row per agent turn (a new turn = the step log's agent changes): agent, time,
                              the whole prompt of the turn's first generation ("full") and only what the agent was
                              given ("inbound" = system + user messages); val2_recommendations.py tests on
                              "inbound" whether a recommendation reached the agent
    <combo>_it<k>_events.jsonl    the same events as the story, machine-readable
    runs.json                 run directory, chain feedback (what iteration 2 was told), coordination_signals

Usage (aipexws8, nice 15, from the analysis checkout):
    PYTHONPATH=. python analysis/link20/reports/scripts/val2_rec_extract.py --out <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

R = Path("/data/Jessica/final_val2")
C = R / "analysis_check/copies"


def _sr(base: Path, tag: str) -> Path:
    return base / f"runs/{tag}/MAS-Aviary/logs/stat_results/{tag}"


# combo code -> (tag, stat_results dir); the combo order is the study's (plots.COMBOS)
RUNS = {
    "S_SP": ("val2_seq_staged", _sr(C / "ws5", "val2_seq_staged")),
    "S_IF": ("val2_seq_iter", _sr(R / "ws3_copy", "val2_seq_iter")),
    "S_GR": ("val2_seq_graph", _sr(C / "ws5", "val2_seq_graph")),
    "O_SP": ("val2_orch_staged", _sr(C / "ws6", "val2_orch_staged")),
    "O_IF": ("val2_orch_iter", _sr(C / "ws6", "val2_orch_iter")),
    "O_GR": ("val2_orch_graph", _sr(R, "val2_orch_graph")),
    "N_IF": ("val2_net_iter", _sr(R, "val2_net_iter")),
    "N_GR": ("val2_net_graph", _sr(R, "val2_net_graph")),
}

FRAMEWORK = {"record_recommendation", "respond_to_recommendation", "assign_task", "set_complexity", "create_agent",
             "write_blackboard", "read_blackboard", "claim_todo", "mark_todo_done", "mark_todo_failed",
             "final_answer", "spawn_peer", "wait_for_board"}
QUIET_OBS = {"read_blackboard", "wait_for_board"}      # observations are long board dumps; arguments suffice
DESIGN_MCP = {"set_high_level_parameters", "set_aircraft_parameters", "set_inputs", "configure_mission",
              "run_simulation", "estimate_mass"}
GEO_KEYS = ["Aircraft.Wing.AREA", "Aircraft.Wing.ASPECT_RATIO", "Aircraft.Wing.SWEEP", "Aircraft.Wing.TAPER_RATIO",
            "Aircraft.Engine.SCALE_FACTOR", "Aircraft.Fuselage.LENGTH", "Aircraft.Fuselage.MAX_WIDTH",
            "Aircraft.Fuselage.MAX_HEIGHT", "Mission.Design.LIFT_COEFFICIENT", "Mission.Design.RANGE",
            "Mission.Design.CRUISE_MACH", "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR", "Aircraft.Wing.MASS_SCALER"]


def jl(p: Path) -> list[dict]:
    return [json.loads(line) for line in open(p) if line.strip()] if p.is_file() else []


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    return str(content)


def mcp_line(r: dict) -> str | None:
    tool, a = r.get("tool"), r.get("args") or {}
    res = r.get("result") if isinstance(r.get("result"), dict) else {}
    err = res.get("error_code") or (None if res.get("success", True) is not False else "success:false")
    if r.get("error"):
        err = "ERR " + str(r["error"])[:120]
    if tool == "set_high_level_parameters":
        np_ = res.get("new_parameters") or {}
        line = f"morph {a.get('component_uid')} updates={json.dumps(a.get('updates'))} -> " \
               f"{json.dumps({k: round(v, 4) for k, v in np_.items() if isinstance(v, float)})}"
    elif tool == "set_aircraft_parameters":
        p = a.get("parameters") or {}
        line = "aviary " + json.dumps({k.split('.', 1)[1]: p[k] for k in GEO_KEYS if k in p}) + f" ({len(p)} keys)"
    elif tool == "set_inputs":
        line = "cycle inputs " + json.dumps(a.get("values"))[:300]
    elif tool == "configure_mission":
        line = "mission " + json.dumps({k: v for k, v in a.items() if k != "session_id"})[:300]
    elif tool == "estimate_mass":
        mb = res.get("mass_breakdown") or {}
        line = f"estimate_mass sized={mb.get('mTOM_sized_kg')} wing={mb.get('wing_mass_kg')}"
    elif tool == "run_simulation":
        s = res.get("summary") if isinstance(res.get("summary"), dict) else {}
        line = f"MISSION fuel={s.get('fuel_burned_kg')} gtow={s.get('gtow_kg')} conv={res.get('converged')} " \
               f"fp={res.get('flown_design_fingerprint')}"
    else:
        return None
    return line + (f"  [{err}: {str(res.get('error') or res.get('message') or '')[:160]}]" if err else "")


def extract(combo: str, it: int, d: Path, out: Path) -> dict:
    steps = jl(d / "step_log_attempt1.jsonl")
    calls = jl(d / "tool_calls_attempt1.jsonl")
    events, prompts = [], []
    prev_agent = None
    for s in steps:
        if s.get("kind") != "step":
            continue
        ag = s.get("agent")
        gens = s.get("generations") or []
        if ag != prev_agent and gens:
            msgs = ((gens[0].get("prompt") or {}).get("messages")) or []
            sys_t = "\n".join(_text(m.get("content")) for m in msgs if m.get("role") == "system")
            users = [_text(m.get("content")) for m in msgs if m.get("role") == "user"]
            full = "\n\n".join(_text(m.get("content")) for m in msgs)
            prompts.append({"agent": ag, "time": s.get("start_time") or s.get("time"), "seq": s.get("seq"),
                            "system": sys_t, "user_first": users[0] if users else "",
                            "user_last": users[-1] if users else "", "full": full,
                            # what the agent was GIVEN (system + user), without its own assistant turns and tool
                            # observations
                            "inbound": sys_t + "\n\n" + "\n\n".join(users)})
            events.append({"time": s.get("start_time") or s.get("time"), "src": "turn", "agent": ag,
                           "text": (users[-1] if users else "")})
        prev_agent = ag
        for tc in s.get("tool_calls") or []:
            name = tc.get("name")
            if name not in FRAMEWORK:
                continue
            obs = "" if name in QUIET_OBS else str(s.get("observations") or "")
            events.append({"time": s.get("time"), "src": "fw", "agent": ag, "tool": name,
                           "args": tc.get("arguments"), "obs": obs[:1500]})
        # visible text before the tool call (reasoning, when the model wrote any)
        for g in gens:
            vis = (g.get("visible") or "")
            pre = vis.split("<tool_call>")[0].strip()
            if pre:
                events.append({"time": s.get("time"), "src": "said", "agent": ag, "text": pre[:1500]})
    for r in calls:
        if r.get("tool") in DESIGN_MCP:
            line = mcp_line(r)
            if line:
                res = r.get("result") if isinstance(r.get("result"), dict) else {}
                summ = res.get("summary") if isinstance(res.get("summary"), dict) else {}
                events.append({"time": r.get("time"), "src": "mcp", "agent": r.get("agent"), "tool": r.get("tool"),
                               "text": line, "args": r.get("args"), "fuel": summ.get("fuel_burned_kg"),
                               "converged": res.get("converged"), "success": res.get("success"),
                               "fp": res.get("flown_design_fingerprint")})
    events.sort(key=lambda e: (e["time"] or 0))
    stem = f"{combo}_it{it}"
    with open(out / f"{stem}_events.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e, default=str) + "\n")
    with open(out / f"{stem}_prompts.jsonl", "w") as f:
        for p in prompts:
            f.write(json.dumps(p) + "\n")
    t0 = events[0]["time"] if events else 0
    with open(out / f"{stem}_story.txt", "w") as f:
        for e in events:
            ts = f"{(e['time'] - t0) / 60:7.1f}m"
            if e["src"] == "turn":
                f.write(f"\n{ts} ===== TURN {e['agent']} =====\n    USER: {e['text'][:2500]}\n")
            elif e["src"] == "fw":
                f.write(f"{ts} [{e['agent']}] {e['tool']} {json.dumps(e['args'])[:2000]}\n")
                if e["obs"]:
                    f.write(f"           -> {e['obs'][:600]}\n")
            elif e["src"] == "said":
                f.write(f"{ts} [{e['agent']}] SAID: {e['text'][:800]}\n")
            else:
                f.write(f"{ts} [{e['agent']}] {e['text']}\n")
    design = json.load(open(d / "design.json")) if (d / "design.json").is_file() else {}
    result = json.load(open(d / "result.json")) if (d / "result.json").is_file() else {}
    return {"combo": combo, "it": it, "dir": str(d), "chain_feedback": result.get("chain_feedback"),
            "chain_integrator_text": result.get("chain_integrator_text"),
            "fuel_measured_kg": result.get("fuel_measured_kg"),
            "coordination_signals": design.get("coordination_signals") or [],
            "coordination_stop": design.get("coordination_stop"), "t0": t0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    runs = []
    for combo, (tag, base) in RUNS.items():
        for it in (1, 2):
            rd = sorted((base / f"repeat_{it - 1:03d}").glob("mdo_f25_*"))
            assert len(rd) == 1, (combo, it, rd)
            runs.append(extract(combo, it, rd[0], out))
            print(combo, it, rd[0])
    json.dump(runs, open(out / "runs.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
