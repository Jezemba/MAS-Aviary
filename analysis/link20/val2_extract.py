"""Validation 2 (final_val2, 8 combinations x 2 iterations): per-mission and per-tool-call tables for the val2 figures.

Reads each link's tool_calls_attempt1.jsonl (every call with full arguments and results, framework calls included as
agent "unknown") and result.json. Read-only on run data.

Writes into --out:
  missions.csv  one row per successful run_simulation (a "flown mission"), with the flown design and the coupled
                values the mission used (see MISSION_COLUMNS below for where each value comes from)
  events.csv    one row per coupled tool call (geometry morph/measure, estimate_mass, get_cruise_point, pyCycle
                set_inputs, set_aircraft_parameters SCALE_FACTOR / wing mass, configure_mission, run_simulation)
  links.csv     one row per link (combination x iteration): result.json fuel, the counted mission, GTOW
  steps.csv     consecutive flown missions (k -> k+1, within and across iterations): which design parameters changed
                and the change in every coupled value

Where the flown values come from (per mission at row m, aviary session s):
  aviary inputs      the get_results after the run on session s (design_parameters.aircraft_params) when there is one
                     before the next session-changing call; else the session's state rebuilt from the successful
                     set_aircraft_parameters calls (their "applied" old/new values)
  engine cycle       the pyCycle session of the last successful generate_engine_deck before m: get_design_inputs
                     current values, then its successful set_inputs in order; keys never set = pyCycle defaults
  Fn_DES             the same pyCycle session's last set_inputs Fn_DES
  cruise L/D         the last successful get_cruise_point before m (the point Fn_DES is sized on); its fingerprint is
                     kept so a point that is not the flown design (N_IF it2, W1) is visible
  sized MTOM         configure_mission takeoff_mass_kg on session s (the mass the mission takes off at)
  mass-mcp           the last successful estimate_mass before m: mTOM_sized_kg and components.mWing_kg
  fuel, GTOW, wing   run_simulation summary

Usage (aipexws8, Avion venv, nice 15):
    python -m analysis.link20.val2_extract --out /data/Jessica/final_val2/figures/data
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

R = Path("/data/Jessica/final_val2")
_C = R / "analysis_check" / "copies"


def _sr(base: Path, tag: str) -> Path:
    return base / "runs" / tag / "MAS-Aviary" / "logs" / "stat_results" / tag


# combo code -> (run tag, stat_results dir, launcher combo name). Same mapping as analysis_check/scripts/dirs.sh.
SOURCES = {
    "S_SP": ("val2_seq_staged", _sr(_C / "ws5", "val2_seq_staged"), "mdo_f25_sequential_staged_pipeline"),
    "S_IF": ("val2_seq_iter", _sr(R / "ws3_copy", "val2_seq_iter"), "mdo_f25_sequential_iterative_feedback"),
    "S_GR": ("val2_seq_graph", _sr(_C / "ws5", "val2_seq_graph"), "mdo_f25_sequential_graph_routed"),
    "O_SP": ("val2_orch_staged", _sr(_C / "ws6", "val2_orch_staged"), "mdo_f25_orchestrated_staged_pipeline"),
    "O_IF": ("val2_orch_iter", _sr(_C / "ws6", "val2_orch_iter"), "mdo_f25_orchestrated_iterative_feedback"),
    "O_GR": ("val2_orch_graph", _sr(R, "val2_orch_graph"), "mdo_f25_orchestrated_graph_routed"),
    "N_IF": ("val2_net_iter", _sr(R, "val2_net_iter"), "mdo_f25_networked_iterative_feedback"),
    "N_GR": ("val2_net_graph", _sr(R, "val2_net_graph"), "mdo_f25_networked_graph_routed"),
}
COMBO_ORDER = list(SOURCES)
STRUCT = {"S": "sequential", "O": "orchestrated", "N": "networked"}
HANDLER = {"SP": "staged_pipeline", "IF": "iterative_feedback", "GR": "graph_routed"}

BASELINE_FUEL_KG = 15719.18          # coupled baseline (final code), extract.BASELINES["final_val"]
BASELINE_GTOW_KG = 90310.0

# Design parameters an agent can set (aviary aircraft inputs + pyCycle cycle inputs), short name -> source key.
AVIARY_DESIGN = {
    "wing_area_m2": "Aircraft.Wing.AREA", "wing_AR": "Aircraft.Wing.ASPECT_RATIO",
    "wing_sweep_deg": "Aircraft.Wing.SWEEP", "wing_taper": "Aircraft.Wing.TAPER_RATIO",
    "wing_tc": "Aircraft.Wing.THICKNESS_TO_CHORD",
    "htail_area_m2": "Aircraft.HorizontalTail.AREA", "htail_AR": "Aircraft.HorizontalTail.ASPECT_RATIO",
    "htail_taper": "Aircraft.HorizontalTail.TAPER_RATIO", "htail_tc": "Aircraft.HorizontalTail.THICKNESS_TO_CHORD",
    "vtail_area_m2": "Aircraft.VerticalTail.AREA", "vtail_AR": "Aircraft.VerticalTail.ASPECT_RATIO",
    "vtail_taper": "Aircraft.VerticalTail.TAPER_RATIO", "vtail_tc": "Aircraft.VerticalTail.THICKNESS_TO_CHORD",
    "fus_length_m": "Aircraft.Fuselage.LENGTH", "fus_height_m": "Aircraft.Fuselage.MAX_HEIGHT",
    "fus_width_m": "Aircraft.Fuselage.MAX_WIDTH",
}
CYCLE_DESIGN = {"T4_MAX_degR": "T4_MAX", "T4_TAKEOFF_degR": "T4_TAKEOFF", "T4_CLIMB_degR": "T4_CLIMB",
                "BPR": "splitter.BPR", "fan_PR": "fan.PR", "lpc_PR": "lpc.PR", "hpc_PR": "hpc.PR"}
DESIGN_PARAMS = list(AVIARY_DESIGN) + list(CYCLE_DESIGN)
COUPLED = ["wing_mass_kg", "mass_mcp_wing_kg", "mass_mcp_mtom_sized_kg", "mtom_sized_kg", "cruise_LD", "fn_des_lbf",
           "engine_scale", "gtow_kg", "fuel_kg"]
# A design parameter counts as changed between two missions when it moved by more than this (relative). Agents round
# aviary inputs to 4 decimals and a wing morph moves area / sweep / t/c by < 0.05 % as a side effect.
CHANGE_REL = 1e-3


def num(v):
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def ok(r) -> bool:
    res = r.get("result")
    if r.get("error"):
        return False
    if isinstance(res, str) and res.lstrip().startswith("Error calling tool"):
        return False
    return not (isinstance(res, dict) and (res.get("success") is False or res.get("error_code")
                                           or res.get("status") == "error"))


def res_of(r) -> dict:
    return r["result"] if isinstance(r.get("result"), dict) else {}


def params_of(r) -> dict:
    p = (r.get("args") or {}).get("parameters") or {}
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except ValueError:
            p = {}
    return p if isinstance(p, dict) else {}


def read_rows(link_dir: Path) -> list[dict]:
    out = []
    for line in (link_dir / "tool_calls_attempt1.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def cycle_defaults(rows: list[dict]) -> dict:
    out = {}
    for r in rows:
        if r.get("tool") == "get_design_inputs" and ok(r):
            for e in res_of(r).get("design_inputs") or []:
                if isinstance(e, dict) and num(e.get("default")) is not None:
                    out[e["name"]] = num(e["default"])
    return out


def link_tables(combo: str, it: int, link_dir: Path, defaults: dict) -> tuple[list[dict], list[dict], dict]:
    rows = read_rows(link_dir)
    result = json.loads((link_dir / "result.json").read_text())
    t0 = num(rows[0].get("time")) if rows else None
    aviary: dict[str, dict] = {}       # aviary session -> {key: value}
    tom: dict[str, float] = {}         # aviary session -> configure_mission takeoff_mass_kg
    cycle: dict[str, dict] = {}        # pyCycle session -> {key: value}
    last_cp = None                     # last get_cruise_point result
    last_mass = None                   # last estimate_mass (mTOM_sized, mWing)
    last_deck_session = None
    last_setinputs_session = None
    geo: dict = {}                     # last measured / morphed Wing1 (area, AR, sweep, taper)
    missions, events = [], []
    for i, r in enumerate(rows):
        tool, a, res, good = r.get("tool"), r.get("args") or {}, res_of(r), ok(r)
        sid = a.get("session_id")
        ev = {"combo": combo, "iteration": it, "row": i, "t_min": (num(r.get("time")) - t0) / 60.0
              if t0 is not None and num(r.get("time")) is not None else None,
              "agent": r.get("agent"), "tool": tool, "ok": good}
        if not good:
            continue
        if tool == "set_high_level_parameters":
            after = (res.get("geometry_morph") or {}).get("after") or {}
            if a.get("component_uid") == "Wing1" and after:
                geo = {"area": after.get("reference_area"), "AR": after.get("aspect_ratio"),
                       "sweep": after.get("sweep_deg"), "taper": after.get("taper_ratio")}
            events.append({**ev, "lane": "geometry", "component": a.get("component_uid"),
                           "updates": json.dumps(a.get("updates"), sort_keys=True),
                           "no_op": (res.get("geometry_morph") or {}).get("no_op"),
                           **{f"geo_{k}": v for k, v in geo.items()}})
        elif tool == "get_high_level_parameters" and a.get("component_uid") == "Wing1":
            p = res.get("parameters") or {}
            geo = {"area": p.get("area"), "AR": p.get("aspect_ratio"), "sweep": p.get("sweep"),
                   "taper": p.get("taper_ratio")}
            events.append({**ev, "lane": "geometry", "component": "Wing1",
                           **{f"geo_{k}": v for k, v in geo.items()}})
        elif tool == "estimate_mass":
            mb = res.get("mass_breakdown") or {}
            last_mass = {"mtom_sized_kg": num(mb.get("mTOM_sized_kg")),
                         "mass_wing_kg": num((mb.get("components") or {}).get("mWing_kg"))}
            events.append({**ev, "lane": "mass", **last_mass, "engine_scale_arg": num(a.get("engine_scale_factor"))})
        elif tool == "get_cruise_point":
            cp = res.get("cruise_point") or {}
            last_cp = {"cruise_LD": num(cp.get("lift_to_drag")), "cruise_mass_kg": num(cp.get("mass_kg")),
                       "cruise_fp": cp.get("design_fingerprint")}
            events.append({**ev, "lane": "cruise_point", **last_cp})
        elif tool == "get_design_inputs":
            st = cycle.setdefault(sid, {})
            for e in res.get("design_inputs") or []:
                if isinstance(e, dict) and num(e.get("current_value")) is not None:
                    st[e["name"]] = num(e["current_value"])
        elif tool == "set_inputs":
            vals = a.get("values") or {}
            st = cycle.setdefault(sid, {})
            for k, v in vals.items():
                if num(v) is not None:
                    st[k] = num(v)
            last_setinputs_session = sid
            if "Fn_DES" in vals:
                events.append({**ev, "lane": "pycycle", "fn_des_lbf": num(vals["Fn_DES"]),
                               **{k: num(vals.get(src)) for k, src in CYCLE_DESIGN.items()}})
        elif tool == "generate_engine_deck":
            last_deck_session = sid
        elif tool == "set_aircraft_parameters":
            st = aviary.setdefault(sid, {})
            for e in res.get("applied") or []:          # fill what the session held before this call
                if isinstance(e, dict) and e.get("name") not in st and num(e.get("old_value")) is not None:
                    st[e["name"]] = num(e["old_value"])
            p = params_of(r)
            for k, v in p.items():
                if num(v) is not None:
                    st[k] = num(v)
            if "Aircraft.Engine.SCALE_FACTOR" in p or "Aircraft.Wing.MASS" in p:
                events.append({**ev, "lane": "aviary_inputs",
                               "engine_scale": num(p.get("Aircraft.Engine.SCALE_FACTOR")),
                               "wing_mass_kg": num(p.get("Aircraft.Wing.MASS")),
                               "wing_AR": num(p.get("Aircraft.Wing.ASPECT_RATIO"))})
        elif tool == "configure_mission":
            if num(a.get("takeoff_mass_kg")) is not None:
                tom[sid] = num(a["takeoff_mass_kg"])
                events.append({**ev, "lane": "aviary_inputs", "takeoff_mass_kg": tom[sid]})
        elif tool == "get_results":
            ap = ((res.get("design_parameters") or {}).get("aircraft_params")) or {}
            if ap:
                aviary.setdefault(sid, {}).update({k: num(v) for k, v in ap.items() if num(v) is not None})
                # a get_results directly after a mission (no session change in between) is the flown state:
                # refresh the matching mission row
                for m in reversed(missions):
                    if m["_sid"] == sid and m["_open"]:
                        for k, src in AVIARY_DESIGN.items():
                            if num(ap.get(src)) is not None:
                                m[k] = num(ap[src])
                        if num(ap.get("Aircraft.Engine.SCALE_FACTOR")) is not None:
                            m["engine_scale"] = num(ap["Aircraft.Engine.SCALE_FACTOR"])
                        cl, cd = num(res.get("cruise_cl_avg")), num(res.get("cruise_cd_avg"))
                        m["mission_LD"] = cl / cd if cl and cd else None
                        m["sfc_cruise"] = num(res.get("cruise_sfc_avg_lb_per_hr_lbf"))
                        m["inputs_from"] = f"get_results row {i}"
                        tomk = num(((res.get("design_parameters") or {}).get("mission_config") or {})
                                   .get("takeoff_mass_kg"))
                        if tomk is not None:
                            m["mtom_sized_kg"] = tomk
                        m["_open"] = False
                        break
        elif tool == "run_simulation":
            summ = res.get("summary") or {}
            fuel = num(summ.get("fuel_burned_kg"))
            if fuel is None:
                continue
            for m in missions:                       # a new mission closes the earlier ones
                if m["_sid"] == sid:
                    m["_open"] = False
            st = aviary.get(sid, {})
            cs = last_deck_session or last_setinputs_session
            cst = {**defaults, **cycle.get(cs, {})}
            fp = res.get("flown_design_fingerprint")
            mission = {"combo": combo, "iteration": it, "row": i, "agent": r.get("agent"),
                       "t_min": ev["t_min"], "converged": bool(res.get("converged")),
                       "fuel_kg": fuel, "gtow_kg": num(summ.get("gtow_kg")),
                       "wing_mass_kg": num(summ.get("wing_mass_kg")),
                       "engine_scale": num(st.get("Aircraft.Engine.SCALE_FACTOR"))
                       or num((res.get("engine") or {}).get("scale_factor")),
                       "engine_mass_kg": num((res.get("engine") or {}).get("engine_mass_kg")),
                       "mtom_sized_kg": tom.get(sid),
                       "fn_des_lbf": cst.get("Fn_DES"),
                       "cruise_LD": (last_cp or {}).get("cruise_LD"),
                       "cruise_point_mass_kg": (last_cp or {}).get("cruise_mass_kg"),
                       "cruise_point_is_flown": (last_cp or {}).get("cruise_fp") == fp if last_cp else None,
                       "mass_mcp_mtom_sized_kg": (last_mass or {}).get("mtom_sized_kg"),
                       "mass_mcp_wing_kg": (last_mass or {}).get("mass_wing_kg"),
                       "flown_fp": fp, "inputs_from": "session state (set_aircraft_parameters)",
                       "mission_LD": None, "sfc_cruise": None, "_sid": sid, "_open": True}
            for k, src in AVIARY_DESIGN.items():
                mission[k] = num(st.get(src))
            for k, src in CYCLE_DESIGN.items():
                mission[k] = cst.get(src)
            missions.append(mission)
            events.append({**ev, "lane": "mission", "fuel_kg": fuel, "gtow_kg": mission["gtow_kg"],
                           "wing_mass_kg": mission["wing_mass_kg"], "engine_scale": mission["engine_scale"]})
    for k, m in enumerate(missions, 1):
        m["mission"] = k
        m.pop("_sid"), m.pop("_open")
    counted = None
    rf = num(result.get("fuel_measured_kg"))
    for m in missions:
        if rf is not None and abs(m["fuel_kg"] - rf) <= 1e-9 * rf:
            counted = m["mission"]
    link = {"combo": combo, "iteration": it, "fuel_kg": rf, "status": result.get("status"),
            "n_missions": len(missions), "counted_mission": counted,
            "gtow_kg": next((m["gtow_kg"] for m in missions if m["mission"] == counted), None),
            "duration_min": (num(result.get("duration_seconds")) or 0) / 60.0, "n_rows": len(rows)}
    return missions, events, link


def describe_change(prev: dict, cur: dict) -> tuple[list[str], str]:
    changed = []
    for p in DESIGN_PARAMS:
        a, b = num(prev.get(p)), num(cur.get(p))
        if a is None or b is None:
            continue
        if abs(b - a) > CHANGE_REL * max(abs(a), 1e-12):
            changed.append(p)
    if changed:
        label = ", ".join(f"{p} {fmt(prev[p])}→{fmt(cur[p])}" for p in changed)
    elif prev["iteration"] != cur["iteration"]:
        label = "no design change (iteration 2 carries the mass basis)"
    else:
        label = "no design change (re-flight)"
    return changed, label


def fmt(v) -> str:
    v = float(v)
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 10:
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{v:.3f}".rstrip("0").rstrip(".")


def steps_table(missions: pd.DataFrame) -> pd.DataFrame:
    out = []
    for combo in COMBO_ORDER:
        g = missions[missions.combo == combo].sort_values(["iteration", "mission"]).to_dict("records")
        for prev, cur in zip(g, g[1:]):
            changed, label = describe_change(prev, cur)
            row = {"combo": combo, "from_iteration": prev["iteration"], "from_mission": prev["mission"],
                   "to_iteration": cur["iteration"], "to_mission": cur["mission"],
                   "across_iterations": prev["iteration"] != cur["iteration"],
                   "n_params_changed": len(changed), "params_changed": ";".join(changed), "change_label": label}
            for p in DESIGN_PARAMS:
                row[f"{p}_from"], row[f"{p}_to"] = prev.get(p), cur.get(p)
            for c in COUPLED + ["mission_LD"]:
                a, b = num(prev.get(c)), num(cur.get(c))
                row[f"{c}_from"], row[f"{c}_to"] = a, b
                row[f"d_{c}"] = (b - a) if a is not None and b is not None else None
                row[f"pct_{c}"] = 100.0 * (b - a) / a if a not in (None, 0) and b is not None else None
            out.append(row)
    return pd.DataFrame(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    all_m, all_e, all_l = [], [], []
    defaults = {}
    for combo, (_tag, root, name) in SOURCES.items():
        for it in (1, 2):
            d = root / f"repeat_{it - 1:03d}" / name
            defaults.update(cycle_defaults(read_rows(d)))
    for combo, (_tag, root, name) in SOURCES.items():
        for it in (1, 2):
            d = root / f"repeat_{it - 1:03d}" / name
            if not (d / "result.json").is_file():
                print("MISSING", combo, it, d)
                continue
            m, e, link = link_tables(combo, it, d, defaults)
            all_m += m
            all_e += e
            all_l.append({**link, "link_dir": str(d)})
    missions = pd.DataFrame(all_m)
    missions["seq"] = missions.groupby("combo").cumcount() + 1
    missions.to_csv(args.out / "missions.csv", index=False)
    pd.DataFrame(all_e).to_csv(args.out / "events.csv", index=False)
    pd.DataFrame(all_l).to_csv(args.out / "links.csv", index=False)
    steps_table(missions).to_csv(args.out / "steps.csv", index=False)
    print("pyCycle defaults:", defaults)
    pd.set_option("display.width", 250, "display.max_columns", 40)
    print(pd.DataFrame(all_l).drop(columns=["link_dir"]))
    print(missions[["combo", "iteration", "mission", "fuel_kg", "gtow_kg", "wing_AR", "wing_area_m2", "wing_mass_kg",
                    "mtom_sized_kg", "cruise_LD", "fn_des_lbf", "engine_scale", "cruise_point_is_flown",
                    "inputs_from"]].to_string())


if __name__ == "__main__":
    main()
