"""Turn a MAS-Aviary run (output dir + combination) into the viewer's bundle.

    <bundle>/run.json              run inputs, per-link summary, link-level metric series
    <bundle>/links/<NNN>.json      one link: step timeline, communication, per-step metrics,
                                   parameters at N and N+1, recommendation, image references
    <bundle>/img/<NNN>_*.png       aircraft and SU2 images; trajectory goes in the link json

Everything shown comes from the run's files (and the snapshot of external artifacts made by
snapshot.py). A value the files do not have is null, never filled in. A link is rebuilt only
when one of its source files changed, so a live run can be re-built cheaply every few seconds.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone

from run_viewer.extract import images

BUNDLE_VERSION = 4
LLM_TEXT_MAX = 8000
ARG_TEXT_MAX = 2000
OBS_TEXT_MAX = 4000

# src/tools/knowledge_base.py PROMPT_BUDGET_TOKENS and src/llm/context_budget.py
PROMPT_BUDGET_TOKENS = 30000
HARD_PROMPT_LIMIT_TOKENS = 40960

COMBO_LABELS = {
    "mdo_f25_sequential_staged_pipeline": ("S_SP", "Sequential & Staged Pipeline"),
    "mdo_f25_sequential_iterative_feedback": ("S_IF", "Sequential & Iterative Feedback"),
    "mdo_f25_sequential_graph_routed": ("S_GR", "Sequential & Graph Routed"),
    "mdo_f25_orchestrated_staged_pipeline": ("O_SP", "Orchestrated & Staged Pipeline"),
    "mdo_f25_orchestrated_iterative_feedback": ("O_IF", "Orchestrated & Iterative Feedback"),
    "mdo_f25_orchestrated_graph_routed": ("O_GR", "Orchestrated & Graph Routed"),
    "mdo_f25_networked_iterative_feedback": ("N_IF", "Networked & Iterative Feedback"),
    "mdo_f25_networked_graph_routed": ("N_GR", "Networked & Graph Routed"),
}

# Communication tools (src/tools/orchestrator_tools.py, networked_tools.py, recommendation_tools.py)
ORCHESTRATOR_TOOLS = {"create_agent", "assign_task", "message_orchestrator", "reply_to_agent",
                      "give_tools", "set_complexity", "list_graph_roles", "list_available_tools"}
BOARD_TOOLS = ("read_blackboard", "write_blackboard", "read_todos", "claim_todo", "wait_for_board",
               "mark_todo_done", "mark_todo_failed", "mark_task_done", "spawn_peer")
SIGNAL_TOOLS = {"record_recommendation", "respond_to_recommendation", "request_complexity",
                "set_complexity"}

# Parameter groups for the N / N+1 panels: (group, label, unit, where it is read from)
PARAMS = [
    ("geometry", "Wing area", "m²", ("aviary", "Aircraft.Wing.AREA")),
    ("geometry", "Aspect ratio", "", ("aviary", "Aircraft.Wing.ASPECT_RATIO")),
    ("geometry", "Sweep", "deg", ("aviary", "Aircraft.Wing.SWEEP")),
    ("geometry", "Taper ratio", "", ("aviary", "Aircraft.Wing.TAPER_RATIO")),
    ("geometry", "Span", "m", ("aviary", "Aircraft.Wing.SPAN")),
    ("geometry", "Fus. length", "m", ("aviary", "Aircraft.Fuselage.LENGTH")),
    ("geometry", "Fus. height", "m", ("aviary", "Aircraft.Fuselage.MAX_HEIGHT")),
    ("geometry", "Fus. width", "m", ("aviary", "Aircraft.Fuselage.MAX_WIDTH")),
    ("mission", "Range", "nmi", ("mission", "range_nmi")),
    ("mission", "Passengers", "", ("mission", "num_passengers")),
    ("mission", "Cruise Mach", "", ("mission", "cruise_mach")),
    ("mission", "Altitude", "ft", ("mission", "cruise_altitude_ft")),
    ("aero", "CL (SU2)", "", ("aero", "cl_cruise")),
    ("aero", "CD (SU2)", "", ("aero", "cd_cruise")),
    ("aero", "Design CL", "", ("aviary", "Mission.Design.LIFT_COEFFICIENT")),
    ("aero", "Drag factor", "", ("aviary", "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR")),
    ("engine", "Scale factor", "", ("aviary", "Aircraft.Engine.SCALE_FACTOR")),
    ("engine", "T4 max", "°R", ("engine", "T4_MAX")),
    ("engine", "BPR", "", ("engine", "splitter.BPR")),
    ("engine", "Fan PR", "", ("engine", "fan.PR")),
    ("engine", "LPC PR", "", ("engine", "lpc.PR")),
    ("engine", "HPC PR", "", ("engine", "hpc.PR")),
    ("engine", "Cruise SFC", "", ("propulsion", "sfc_cruise")),
    ("engine", "Net thrust", "lbf", ("propulsion", "fn_lbf")),
]

PHYSICS = [("fuel_burned_kg", "Fuel burned", "kg"), ("gtow_kg", "GTOW", "kg"),
           ("wing_mass_kg", "Wing mass", "kg"), ("optimality_gap_pct", "Optimality gap", "%"),
           ("cl_cruise", "CL (SU2)", ""), ("cd_cruise", "CD (SU2)", ""),
           ("sfc_cruise", "Cruise SFC", ""), ("converged", "Mission converged", "0/1")]
GENERAL = [("duration_min", "Link wall time", "min"), ("steps", "Agent steps", ""),
           ("tokens_total", "Tokens (prompt + output)", ""), ("output_tokens", "Output tokens", ""),
           ("tool_calls", "Tool calls", ""), ("mcp_refusals", "Refused MCP calls", ""),
           ("total_turns", "Agent turns", "")]


# ---------------------------------------------------------------------------- reading


def _load_json(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _load_jsonl(path: str) -> list[dict]:
    rows = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass  # a live run can leave a half-written last line
    except OSError:
        pass
    return rows


def _attempts(link_dir: str) -> list[str]:
    files = glob.glob(os.path.join(link_dir, "step_log_attempt*.jsonl"))
    return sorted(files, key=lambda p: int(re.search(r"attempt(\d+)", p).group(1)))


def _clip(text, limit: int):
    if text is None:
        return None
    if not isinstance(text, str):
        text = json.dumps(text, default=str)
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more chars]"


def _num(v):
    if isinstance(v, bool):
        return int(v)
    return v if isinstance(v, (int, float)) else None


def _utc(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _fresh(out: str, inputs: list[str]) -> bool:
    """True when out exists and is newer than every input (so it need not be re-made)."""
    try:
        t = os.path.getmtime(out)
        return all(os.path.getmtime(p) <= t for p in inputs if p and os.path.exists(p))
    except OSError:
        return False


def fingerprint(paths: list[str]) -> str:
    h = hashlib.sha1(str(BUNDLE_VERSION).encode())
    for p in sorted(paths):
        try:
            st = os.stat(p)
            h.update(f"{p}:{st.st_size}:{int(st.st_mtime)}".encode())
        except OSError:
            h.update(f"{p}:missing".encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------- steps


def _observation_json(obs: str | None):
    if not obs:
        return None
    try:
        return json.loads(obs)
    except (ValueError, TypeError):
        return None


def build_steps(step_rows: list[dict]) -> list[dict]:
    """One event per agent step, in start-time order (networked peers interleave)."""
    steps = []
    for row in step_rows:
        if row.get("kind", "step") != "step":
            continue
        gens = row.get("generations") or []
        last = gens[-1] if gens else {}
        prompt = last.get("prompt") or {}
        steps.append({
            "agent": row.get("agent") or "unknown",
            "agent_step": row.get("agent_step"),
            "start": row.get("start_time"),
            "end": row.get("end_time"),
            "llm_output": _clip(last.get("visible") if last.get("visible") is not None
                                else last.get("raw_text"), LLM_TEXT_MAX),
            "generation_attempts": len(gens),
            "prompt_tokens": prompt.get("prompt_tokens"),
            "prompt_tokens_spent": sum((g.get("prompt") or {}).get("prompt_tokens") or 0 for g in gens),
            "output_tokens": sum(g.get("output_tokens") or 0 for g in gens),
            "tool_calls": [{"name": t.get("name"), "arguments": _clip(t.get("arguments"), ARG_TEXT_MAX)}
                           for t in row.get("tool_calls") or []],
            "_raw_args": [t.get("arguments") for t in row.get("tool_calls") or []],
            "observation": _clip(row.get("observations"), OBS_TEXT_MAX),
            "_obs_json": _observation_json(row.get("observations")),
            "error": _clip(row.get("error"), 1000),
            "is_final_answer": bool(row.get("is_final_answer")),
        })
    steps.sort(key=lambda s: (s["start"] or 0, s["end"] or 0))
    for i, s in enumerate(steps):
        s["i"] = i
    return steps


def _arg(raw, key):
    return raw.get(key) if isinstance(raw, dict) else None


def build_comm(steps: list[dict], org: str) -> list[dict]:
    """Rolling communication list: orchestrator messages, or blackboard / TODO traffic."""
    out = []
    for s in steps:
        for call, raw in zip(s["tool_calls"], s["_raw_args"]):
            name = call["name"]
            obs = s["_obs_json"] if isinstance(s["_obs_json"], dict) else {}
            entry = {"i": s["i"], "t": s["start"], "agent": s["agent"], "tool": name}
            if org == "orchestrated" and name in ORCHESTRATOR_TOOLS | SIGNAL_TOOLS:
                if name == "message_orchestrator":
                    entry.update(src=s["agent"], dst="orchestrator", text=_arg(raw, "message"))
                elif name in ("reply_to_agent", "assign_task", "give_tools"):
                    text = _arg(raw, "message") or _arg(raw, "task") or _arg(raw, "tools")
                    entry.update(src=s["agent"], dst=_arg(raw, "agent_name"), text=text)
                elif name == "create_agent":
                    entry.update(src=s["agent"], dst=_arg(raw, "name"), text=_arg(raw, "persona"))
                else:
                    entry.update(src=s["agent"], dst=None,
                                 text=_arg(raw, "reason") or _arg(raw, "recommended_change")
                                 or _arg(raw, "how_addressed"))
            elif org == "networked" and (name in BOARD_TOOLS or name in SIGNAL_TOOLS):
                todo = _arg(raw, "todo_name")
                text = (_arg(raw, "result") or _arg(raw, "reason") or _arg(raw, "summary")
                        or _arg(raw, "value") or _arg(raw, "recommended_change")
                        or _arg(raw, "how_addressed") or _arg(raw, "entry_type"))
                entry.update(src=s["agent"], dst="board", todo=todo, text=text,
                             ok=obs.get("success") if isinstance(obs.get("success"), bool) else None)
            else:
                continue
            entry["text"] = _clip(entry.get("text"), 600)
            out.append(entry)
    return out


# ---------------------------------------------------------------------------- turns / route


def build_turns(result: dict | None, design: dict | None) -> list[dict]:
    """Agent turns in order (result.json messages) with graph-route / stage metadata."""
    turns = []
    for m in (result or {}).get("messages") or []:
        meta = m.get("metadata") or {}
        end = m.get("timestamp")
        dur = m.get("duration_seconds") or 0
        route = meta.get("graph_route") if isinstance(meta.get("graph_route"), dict) else None
        turns.append({
            "agent": m.get("agent_name"),
            "turn": m.get("turn_number"),
            "start": (end - dur) if isinstance(end, (int, float)) else None,
            "end": end,
            "completion_met": meta.get("completion_met"),
            "attempt_number": meta.get("attempt_number"),
            "aspiration_met": meta.get("aspiration_met"),
            "route": {k: route.get(k) for k in ("node", "next", "level", "next_level", "rule", "reason")}
            if route else None,
            "error": bool(m.get("error")),
        })
    return turns


def network_route_events(design: dict | None) -> list[dict]:
    """Networked graph-routed: the framework's routing log (level changes, reopened roles)."""
    gr = (design or {}).get("graph_route") or {}
    return [{k: e.get(k) for k in ("event", "from", "to", "reason", "reopened")} for e in gr.get("log") or []]


# ---------------------------------------------------------------------------- params


def _aviary_params(design: dict | None, result: dict | None, end: bool) -> dict:
    """Aviary parameter values: what was applied to the mission, else the chain record."""
    vals = dict((design or {}).get("mission_applied") or {})
    # initial_params: the exact values the link started from (the chain anchor is rounded)
    keys = ("chain_end_params",) if end else ("chain_start_params", "initial_params")
    for key in keys:
        for k, v in ((result or {}).get(key) or {}).items():
            vals.setdefault(k, v)
    return vals


def param_state(design: dict | None, result: dict | None, end: bool, anchor: dict | None = None) -> dict:
    """Grouped parameter values at one point; missing values are None."""
    design = design or {}
    aviary = _aviary_params(design, result, end)
    if not aviary and anchor:
        aviary = dict(anchor.get("anchor_params") or {})
    sources = {
        "aviary": aviary,
        "mission": design.get("mission") or (result or {}).get("flown_mission") or {},
        "aero": design.get("aero") or {},
        "engine": (design.get("design_space") or {}).get("engine") or {},
        "propulsion": design.get("propulsion") or {},
    }
    groups = {"geometry": [], "mission": [], "aero": [], "engine": []}
    for group, label, unit, (src, key) in PARAMS:
        groups[group].append({"key": key, "label": label, "unit": unit,
                              "value": _num(sources[src].get(key))})
    return groups


# ---------------------------------------------------------------------------- metrics


def kb_physics_timeline(kb: list[dict]) -> list[dict]:
    """Each run_simulation result in the link, with its time."""
    out = []
    for row in kb:
        if row.get("tool") != "run_simulation":
            continue
        o = row.get("outputs") or {}
        summ = o.get("summary") if isinstance(o.get("summary"), dict) else {}
        flown = o.get("flown")
        out.append({"t": _utc(row.get("time_utc")), "seq": row.get("seq"), "status": row.get("status"),
                    "flown": flown, "converged": o.get("converged"),
                    "fuel_burned_kg": _num(summ.get("fuel_burned_kg")) if flown is not False else None,
                    "gtow_kg": _num(summ.get("gtow_kg")) if flown is not False else None})
    return out


def step_metrics(steps: list[dict], turns: list[dict], kb: list[dict], org: str, handler: str) -> list[dict]:
    """Running values at the end of each step; same formulas as the repo where it has one."""
    physics = kb_physics_timeline(kb)
    refusals_t = sorted(_utc(r.get("time_utc")) or 0 for r in kb
                        if r.get("status") not in (None, "success") and r.get("server"))
    t0 = min((s["start"] for s in steps if s["start"]), default=None)
    counts = {"tokens_total": 0, "output_tokens": 0, "tool_calls": 0, "steps": 0}
    tool_counts: dict[str, int] = {}
    claim_conflicts = 0
    per_agent_steps: dict[str, int] = {}
    out = []
    for s in steps:
        t = s["end"] or s["start"] or 0
        counts["steps"] += 1
        counts["tokens_total"] += s["prompt_tokens_spent"] + s["output_tokens"]
        counts["output_tokens"] += s["output_tokens"]
        per_agent_steps[s["agent"]] = per_agent_steps.get(s["agent"], 0) + 1
        for call in s["tool_calls"]:
            if call["name"] == "final_answer":
                continue
            counts["tool_calls"] += 1
            tool_counts[call["name"]] = tool_counts.get(call["name"], 0) + 1
            if call["name"] == "claim_todo" and isinstance(s["_obs_json"], dict) \
                    and s["_obs_json"].get("success") is False:
                claim_conflicts += 1
        m = {"i": s["i"], "elapsed_s": round(t - t0, 1) if t0 else None, **counts,
             "context_tokens": s["prompt_tokens"],
             "mcp_refusals": sum(1 for rt in refusals_t if rt <= t)}
        sims = [p for p in physics if (p["t"] or 0) <= t]
        flown = [p for p in sims if p["fuel_burned_kg"] is not None]
        m["simulations"] = len(sims)
        m["fuel_burned_kg"] = flown[-1]["fuel_burned_kg"] if flown else None
        m["gtow_kg"] = flown[-1]["gtow_kg"] if flown else None
        done = [tn for tn in turns if tn["start"] is not None and tn["start"] <= t]
        if org == "orchestrated":
            m["orchestrator_steps"] = per_agent_steps.get("orchestrator", 0)
            m["orchestrator_calls"] = sum(tool_counts.get(k, 0) for k in ORCHESTRATOR_TOOLS)
            m["messages_to_orchestrator"] = tool_counts.get("message_orchestrator", 0)
            m["replies_from_orchestrator"] = tool_counts.get("reply_to_agent", 0)
            m["tasks_assigned"] = tool_counts.get("assign_task", 0)
            m["orchestrator_turns"] = sum(1 for tn in done if tn["agent"] == "orchestrator")
            m["worker_turns"] = sum(1 for tn in done if tn["agent"] != "orchestrator")
        if org == "networked":
            for k in BOARD_TOOLS:
                m[k] = tool_counts.get(k, 0)
            m["claim_conflicts"] = claim_conflicts
        if handler == "graph_routed":
            seq = [tn["route"]["node"] if tn["route"] else tn["agent"] for tn in done]
            seen, revisits = set(), 0
            for node in seq:
                revisits += node in seen
                seen.add(node)
            m["states_visited"] = len(seen)
            m["total_transitions"] = max(len(seq) - 1, 0)
            m["misroute_rate"] = round(revisits / (len(seq) - 1), 4) if len(seq) > 1 else None
        if handler == "staged_pipeline":
            stages = [tn for tn in done if tn["agent"] != "orchestrator"]
            met = [tn for tn in stages if tn["completion_met"] is True]
            m["stages_run"] = len(stages)
            m["completion_rate"] = round(len(met) / len(stages), 4) if stages else None
        if handler == "iterative_feedback":
            per = {}
            for tn in done:
                if tn["agent"] != "orchestrator":
                    per[tn["agent"]] = per.get(tn["agent"], 0) + 1
            m["max_attempts_used"] = max(per.values(), default=0)
            m["mean_attempts"] = round(sum(per.values()) / len(per), 3) if per else None
        out.append(m)
    return out


STEP_METRIC_LABELS = {
    "orchestrator_calls": "Orchestrator tool calls", "orchestrator_steps": "Orchestrator steps",
    "messages_to_orchestrator": "Messages to orchestrator", "replies_from_orchestrator": "Replies from orchestrator",
    "tasks_assigned": "Tasks assigned", "orchestrator_turns": "Orchestrator turns", "worker_turns": "Worker turns",
    "read_blackboard": "read_blackboard", "write_blackboard": "write_blackboard", "read_todos": "read_todos",
    "claim_todo": "claim_todo", "wait_for_board": "wait_for_board", "mark_todo_done": "mark_todo_done",
    "mark_todo_failed": "mark_todo_failed (releases the claim)", "mark_task_done": "mark_task_done",
    "spawn_peer": "spawn_peer", "claim_conflicts": "Claims refused (held by another peer)",
    "states_visited": "States visited", "total_transitions": "Transitions", "misroute_rate": "Misroute rate",
    "stages_run": "Stages run", "completion_rate": "Stage completion rate",
    "max_attempts_used": "Max attempts (one agent)", "mean_attempts": "Mean attempts per agent",
}


def link_summary(result: dict | None, design: dict | None, steps: list[dict], kb: list[dict]) -> dict:
    """Link-level values for the across-links plots. None where the files have no value."""
    result = result or {}
    ev = result.get("eval_classification") or {}
    design = design or {}
    flown = result.get("fuel_measured_kg") is not None or ev.get("fuel_burned_kg") is not None
    s = {
        "status": result.get("status") or ("running" if steps else "missing"),
        "flown": flown if result else None,
        "fuel_burned_kg": _num(ev.get("fuel_burned_kg")) if flown else None,
        "gtow_kg": _num(ev.get("gtow_kg")) if flown else None,
        "wing_mass_kg": _num(ev.get("wing_mass_kg")) if flown else None,
        "optimality_gap_pct": _num(ev.get("optimality_gap_pct")),
        "converged": _num(ev.get("converged")),
        "cl_cruise": _num((design.get("aero") or {}).get("cl_cruise")),
        "cd_cruise": _num((design.get("aero") or {}).get("cd_cruise")),
        "sfc_cruise": _num((design.get("propulsion") or {}).get("sfc_cruise")),
        "duration_min": round(result["duration_seconds"] / 60, 2) if _num(result.get("duration_seconds")) else None,
        "steps": len(steps),
        "tokens_total": sum(x["prompt_tokens_spent"] + x["output_tokens"] for x in steps),
        "output_tokens": sum(x["output_tokens"] for x in steps),
        "tool_calls": sum(1 for x in steps for c in x["tool_calls"] if c["name"] != "final_answer"),
        "mcp_refusals": sum(1 for r in kb if r.get("server") and r.get("status") not in (None, "success")),
        "total_turns": _num(result.get("total_turns")),
        "eval_result": ev.get("result"),
    }
    coord = {}
    for k, v in (result.get("org_theory_metrics") or {}).items():
        if isinstance(v, bool) or isinstance(v, (int, float)):
            coord[k] = _num(v)
        elif isinstance(v, list) and k in ("states_visited", "states_revisited"):
            coord[k + "_count"] = len(v)
    s["coordination"] = coord
    s["cross_strategy"] = {k: _num(v) for k, v in (result.get("cross_strategy_metrics") or {}).items()}
    return s


# ---------------------------------------------------------------------------- images


class ImageEnv:
    def __init__(self, aviary_python: str | None, tigl_python: str | None, fixtures_dir: str | None):
        self.aviary_python = aviary_python
        self.tigl_python = tigl_python
        self.fixtures_dir = fixtures_dir
        self.helpers = os.path.join(os.path.dirname(__file__), "helpers")


def trajectory_for(snapshot_dir: str | None, design: dict | None, env: ImageEnv, cache: str,
                   kb: list[dict] | None = None) -> dict:
    sid = ((design or {}).get("sessions") or {}).get("aviary")
    if not sid:
        return {"status": "no aviary session recorded for this link", "data": None}
    db = os.path.join(snapshot_dir or "", "aviary", f"{sid}.db")
    if not snapshot_dir or not os.path.exists(db):
        if kb is not None and not any(r.get("tool") == "run_simulation" and r.get("status") == "success" for r in kb):
            return {"status": "no mission simulation succeeded in this link", "data": None}
        return {"status": "aviary recorder not saved for this session", "data": None}
    replay = _load_json(os.path.join(snapshot_dir, "aviary", f"{sid}.replay.json"))
    extra = {"replayed": replay} if replay else {}
    if _fresh(cache, [db]):
        return {"status": "ok", "session": sid, "data": _load_json(cache), **extra}
    if not env.aviary_python:
        return {"status": "aviary env not configured", "data": None}
    proc = subprocess.run([env.aviary_python, "-I", os.path.join(env.helpers, "aviary_trajectory.py"),
                           db, cache], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        return {"status": "trajectory extraction failed: " + proc.stderr.strip()[-300:], "data": None}
    return {"status": "ok", "session": sid, "data": _load_json(cache), **extra}


def su2_image(snapshot_dir: str | None, kb: list[dict], out_png: str, title: str) -> dict:
    solves = [r for r in kb if r.get("tool") == "run_su2_solver" and r.get("status") == "success"]
    if not solves:
        return {"status": "no successful SU2 solve in this link", "file": None}
    folders = sorted(glob.glob(os.path.join(snapshot_dir or "", "su2", "*", "surface_flow.csv")))
    if not snapshot_dir or not folders:
        return {"status": f"{len(solves)} SU2 solve(s) ran, but their surface files were not saved", "file": None}
    folder = os.path.dirname(folders[-1])  # highest kb seq = the link's last solve
    coeffs = (solves[-1].get("outputs") or {}).get("final_coefficients") or {}
    inputs = [os.path.join(folder, "surface_flow.csv"), os.path.join(folder, "config.cfg")]
    meta_path = out_png + ".json"
    meta = _load_json(meta_path) or {}
    if _fresh(out_png, inputs) and _fresh(meta_path, inputs) and meta.get("solve") == os.path.basename(folder) \
            and meta.get("v") == images.IMAGE_VERSION:
        info = meta["info"]
    else:
        info = images.render_su2(inputs[0], inputs[1], out_png, title)
        with open(meta_path, "w") as f:
            json.dump({"solve": os.path.basename(folder), "info": info, "v": images.IMAGE_VERSION}, f)
    replay = _load_json(os.path.join(folder, "replay.json"))
    extra = {"replayed": {k: replay.get(k) for k in ("geometry_source", "original", "replay", "replayed_utc")}} \
        if replay else {}
    return {"status": "ok", "file": os.path.basename(out_png), "v": images.IMAGE_VERSION, **extra,
            "solve": os.path.basename(folder),
            "solves_in_link": len(solves), "CL": coeffs.get("CL"), "CD": coeffs.get("CD"), **info}


def aircraft_image(cpacs: str | None, out_png: str, title: str, env: ImageEnv) -> dict:
    if not cpacs or not os.path.exists(cpacs):
        return {"status": "no CPACS file for this geometry", "file": None}
    meta_path = out_png + ".json"
    meta = _load_json(meta_path) or {}
    if _fresh(out_png, [cpacs]) and meta.get("cpacs") == cpacs and meta.get("v") == images.IMAGE_VERSION:
        return {"status": "ok", "file": os.path.basename(out_png), "v": images.IMAGE_VERSION, **meta["info"]}
    if not env.tigl_python:
        return {"status": "tigl env not configured", "file": None}
    with tempfile.NamedTemporaryFile(suffix=".stl") as tmp:
        proc = subprocess.run([env.tigl_python, "-I", os.path.join(env.helpers, "cpacs_to_stl.py"),
                               cpacs, tmp.name], capture_output=True, text=True, timeout=300)
        if proc.returncode != 0 or os.path.getsize(tmp.name) == 0:
            return {"status": "TiGL export failed: " + proc.stderr.strip()[-300:], "file": None}
        info = images.render_aircraft(tmp.name, out_png, title)
    with open(meta_path, "w") as f:
        json.dump({"cpacs": cpacs, "info": info, "v": images.IMAGE_VERSION}, f)
    return {"status": "ok", "file": os.path.basename(out_png), "v": images.IMAGE_VERSION, **info}


# ---------------------------------------------------------------------------- run


class RunBuilder:
    def __init__(self, run_dir: str, combination: str, bundle_dir: str, snapshot_dir: str | None,
                 env: ImageEnv, run_id: str, live: bool = False):
        self.run_dir = run_dir
        self.combination = combination
        self.bundle_dir = bundle_dir
        self.snapshot_dir = snapshot_dir
        self.env = env
        self.run_id = run_id
        self.live = live
        label, title = COMBO_LABELS.get(combination, (combination, combination))
        self.label, self.title = label, title
        org, handler = combination.replace("mdo_f25_", "").split("_", 1)
        self.org, self.handler = org, handler
        os.makedirs(os.path.join(bundle_dir, "links"), exist_ok=True)
        os.makedirs(os.path.join(bundle_dir, "img"), exist_ok=True)
        self.anchor = _load_json(os.path.join(run_dir, "chain_anchor.json"))

    def link_dirs(self) -> list[str]:
        return sorted(glob.glob(os.path.join(self.run_dir, "repeat_*", self.combination)))

    def _link_snapshot(self, link_dir: str) -> str | None:
        if not self.snapshot_dir:
            return None
        repeat = os.path.basename(os.path.dirname(link_dir))
        d = os.path.join(self.snapshot_dir, repeat, self.combination)
        return d if os.path.isdir(d) else None

    def _sources(self, link_dir: str) -> list[str]:
        files = glob.glob(os.path.join(link_dir, "*.json*")) + glob.glob(os.path.join(link_dir, "*.xml"))
        snap = self._link_snapshot(link_dir)
        if snap:
            files += glob.glob(os.path.join(snap, "*", "*"))
            files += glob.glob(os.path.join(snap, "su2", "*", "*"))
        return files

    def build_link(self, idx: int, link_dir: str, prev: dict | None, prev_cpacs: str | None,
                   prev_aircraft: dict | None) -> dict:
        link_no = idx + 1
        design = _load_json(os.path.join(link_dir, "design.json"))
        result = _load_json(os.path.join(link_dir, "result.json"))
        carry = _load_json(os.path.join(link_dir, "carry_over.json"))
        kb = _load_jsonl(os.path.join(link_dir, "knowledge_base.jsonl"))
        attempts = _attempts(link_dir)
        step_rows = _load_jsonl(attempts[-1]) if attempts else []
        steps = build_steps(step_rows)
        turns = build_turns(result, design)
        snap = self._link_snapshot(link_dir)
        n = f"{link_no:03d}"

        # Images. The geometry carries over between links, so a link that did not change it
        # shows the geometry it inherited (and says so).
        cpacs = os.path.join(link_dir, "geometry_end.xml")
        if os.path.exists(cpacs):
            aircraft = aircraft_image(cpacs, os.path.join(self.bundle_dir, "img", f"{n}_aircraft.png"),
                                      f"Link {link_no} geometry", self.env)
            aircraft["changed_this_link"] = True
            cpacs_used = cpacs
        elif prev_aircraft and prev_aircraft.get("file"):
            aircraft = {**prev_aircraft, "changed_this_link": False,
                        "note": "geometry not changed in this link; showing the inherited geometry"}
            cpacs_used = prev_cpacs
        else:
            base = self._baseline_cpacs(carry, result)
            aircraft = aircraft_image(base, os.path.join(self.bundle_dir, "img", f"{n}_aircraft.png"),
                                      f"Link {link_no} geometry (baseline)", self.env)
            aircraft["changed_this_link"] = False
            aircraft["note"] = "geometry not changed in this link; baseline CPACS"
            cpacs_used = base
        su2 = su2_image(snap, kb, os.path.join(self.bundle_dir, "img", f"{n}_su2.png"), f"Link {link_no} SU2 Cp")
        traj = trajectory_for(snap, design, self.env, os.path.join(self.bundle_dir, "img", f"{n}_traj.json"), kb)

        # N = state at the link's start = the previous link's end where we have it.
        if prev and prev.get("_design") is not None:
            start = param_state(prev["_design"], prev.get("_result"), end=True)
        elif carry and carry.get("previous_design"):
            start = param_state(carry["previous_design"], result, end=False)
        else:
            start = param_state(None, result, end=False, anchor=self.anchor)
        end = param_state(design, result, end=True) if (design or result) else None

        rec = None
        if carry and carry.get("recommendation"):
            r = carry["recommendation"]
            rec = {"from": r.get("agent"), "to": r.get("recommendation_to"), "verdict": r.get("verdict"),
                   "change": r.get("recommended_change"),
                   "integrator_state": _clip(carry.get("previous_integrator_design_state"), 1500)}
        signals = [{k: v for k, v in sig.items()} for sig in (design or {}).get("coordination_signals") or []]

        link = {
            "link": link_no,
            "repeat": os.path.basename(os.path.dirname(link_dir)),
            "attempt": len(attempts),
            "summary": link_summary(result, design, steps, kb),
            "steps": [{k: v for k, v in s.items() if not k.startswith("_")} for s in steps],
            "step_metrics": step_metrics(steps, turns, kb, self.org, self.handler),
            "turns": turns,
            "route_events": network_route_events(design) if self.org == "networked" else [],
            "graph_stop_reason": next(((tm.get("metadata") or {}).get("graph_stop_reason")
                                       for tm in (result or {}).get("messages") or []
                                       if (tm.get("metadata") or {}).get("graph_stop_reason")),
                                      ((design or {}).get("graph_route") or {}).get("stop_reason")),
            "comm": build_comm(steps, self.org),
            "physics_timeline": kb_physics_timeline(kb),
            "kb_calls": [{"seq": r.get("seq"), "t": _utc(r.get("time_utc")), "agent": r.get("agent"),
                          "tool": r.get("tool"), "server": r.get("server"), "status": r.get("status"),
                          "error_code": (r.get("outputs") or {}).get("error_code")
                          if isinstance(r.get("outputs"), dict) else None}
                         for r in kb if r.get("server")],
            "params_start": start,
            "params_end": end,
            "recommendation": rec,
            "signals": signals,
            "images": {"aircraft": aircraft, "su2": su2, "trajectory": traj},
            "complete": result is not None,
        }
        with open(os.path.join(self.bundle_dir, "links", f"{n}.json"), "w") as f:
            json.dump(link, f, default=str)
        link["_design"], link["_result"], link["_cpacs"] = design, result, cpacs_used
        return link

    def _baseline_cpacs(self, carry: dict | None, result: dict | None) -> str | None:
        path = (carry or {}).get("baseline_cpacs")
        if not path:
            preinit = next(((m.get("metadata") or {}).get("graph_preinit") for m in (result or {}).get("messages") or []
                            if (m.get("metadata") or {}).get("graph_preinit")), None) or {}
            path = preinit.get("cpacs")
        if path and os.path.exists(path):
            return path
        # The run may have been made on another machine: use the same fixture by name here.
        if self.env.fixtures_dir:
            local = os.path.join(self.env.fixtures_dir, os.path.basename(path or "D150_simple.xml"))
            if os.path.exists(local):
                return local
        return None

    def build(self) -> dict:
        state_path = os.path.join(self.bundle_dir, "_state.json")
        state = _load_json(state_path) or {}
        links, prev = [], None
        for idx, link_dir in enumerate(self.link_dirs()):
            fp = fingerprint(self._sources(link_dir) + ([f"prev:{state.get(str(idx - 1), '')}"] if idx else []))
            cached = _load_json(os.path.join(self.bundle_dir, "links", f"{idx + 1:03d}.json"))
            if cached and state.get(str(idx)) == fp:
                cached["_design"] = _load_json(os.path.join(link_dir, "design.json"))
                cached["_result"] = _load_json(os.path.join(link_dir, "result.json"))
                geo = os.path.join(link_dir, "geometry_end.xml")
                cached["_cpacs"] = geo if os.path.exists(geo) else (prev or {}).get("_cpacs")
                link = cached
            else:
                link = self.build_link(idx, link_dir, prev, (prev or {}).get("_cpacs"),
                                       (prev or {}).get("images", {}).get("aircraft"))
                state[str(idx)] = fp
            links.append(link)
            prev = link
        with open(state_path, "w") as f:
            json.dump(state, f)
        run = self.run_json(links)
        with open(os.path.join(self.bundle_dir, "run.json"), "w") as f:
            json.dump(run, f, default=str)
        return run

    def run_json(self, links: list[dict]) -> dict:
        model = thinking = None
        for link in links:
            tb = ((link.get("_result") or {}).get("token_breakdown") or {})
            model = model or tb.get("model_id")
        first = self.link_dirs()[0] if self.link_dirs() else None
        if first and _attempts(first):
            for row in _load_jsonl(_attempts(first)[-1])[:5]:
                gens = row.get("generations") or []
                if gens:
                    thinking = gens[0].get("thinking_enabled")
                    break
        coord_keys = sorted({k for link in links for k in link["summary"]["coordination"]})
        cross_keys = sorted({k for link in links for k in link["summary"]["cross_strategy"]})
        step_keys = sorted({k for link in links for m in link["step_metrics"][-1:] for k in m
                            if k in STEP_METRIC_LABELS})
        return {
            "bundle_version": BUNDLE_VERSION,
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "combination": self.combination,
            "label": self.label,
            "title": self.title,
            "org": self.org,
            "handler": self.handler,
            "live": self.live,
            "inputs": {"links_planned": (self.anchor or {}).get("n_links"), "links_present": len(links),
                       "model": model, "thinking": thinking, "chain_mode": (self.anchor or {}).get("mode"),
                       "seed": (self.anchor or {}).get("base_seed")},
            "token_budget": {"prompt_budget": PROMPT_BUDGET_TOKENS, "hard_limit": HARD_PROMPT_LIMIT_TOKENS},
            "links": [{"link": lk["link"], "repeat": lk["repeat"], "complete": lk["complete"],
                       **{k: v for k, v in lk["summary"].items() if k not in ("coordination", "cross_strategy")},
                       "coordination": lk["summary"]["coordination"],
                       "cross_strategy": lk["summary"]["cross_strategy"]} for lk in links],
            "metric_defs": {
                "physics": [{"key": k, "label": lb, "unit": u} for k, lb, u in PHYSICS],
                "general": [{"key": k, "label": lb, "unit": u} for k, lb, u in GENERAL],
                "coordination": [{"key": k, "label": k.replace("_", " ")} for k in coord_keys],
                "cross_strategy": [{"key": k, "label": k.replace("_", " ")} for k in cross_keys],
                "step": [{"key": k, "label": STEP_METRIC_LABELS[k]} for k in step_keys],
            },
            "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a run viewer bundle")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--combination", required=True)
    ap.add_argument("--out", required=True, help="bundle dir for this run")
    ap.add_argument("--snapshot", default=None, help="snapshot dir for this run (from snapshot.py)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--aviary-python", default=None)
    ap.add_argument("--tigl-python", default=None)
    ap.add_argument("--fixtures-dir", default=None)
    args = ap.parse_args()
    env = ImageEnv(args.aviary_python, args.tigl_python, args.fixtures_dir)
    run = RunBuilder(args.run_dir, args.combination, args.out, args.snapshot, env, args.run_id).build()
    print(f"{run['run_id']}: {len(run['links'])} links")


if __name__ == "__main__":
    main()
