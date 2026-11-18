"""What the COUNTED mission of a link actually flew, and the bug signatures of the re-run bug checks.

The counted mission is the run_simulation whose fuel is the link's fuel (extract.mission_summaries: the last
successful mission in the knowledge base). Every coupled value is read from the inputs aviary flew in that mission,
never from design.json (which holds the link's LAST values, flown or not: 11_bug_checks.md case 5, surprise 2).

Where the flown inputs come from, in order:
  1. get_results on the same aviary session after the counted run_simulation, before any other call that changes the
     session (aviary-mcp a5ef294 reports the inputs it flew with in design_parameters.aircraft_params);
  2. otherwise the session's starting parameters (result.json initial_params, else carry_over.aviary_session_start)
     overlaid with every SUCCESSFUL set_aircraft_parameters on that session before the run.
A parameter the session never received stays empty and its source says so (aviary then used its own default).
Each value has a source column (src.<column>).

Bug signatures (one boolean column each; empty = could not be decided, the matching .detail column says why). The
reference detectors are ws8 /data/Jessica/l20_bugchecks/case{1,3,5}/ (scan_signatures.py, case3_scan.py,
case3_target_cl.py); the expected iterations are in analysis/link20/reports/11_bug_checks.md.
  flag.untrimmed_su2          the solve whose CL was flown had no target CL, or su2-mcp reported su2_unchecked, or
                              its CD is below the induced drag su2-mcp computed (cases 1-2)
  flag.agent_target_cl        that solve's lift target was typed by the agent, not computed by the framework (3.1 B)
  flag.mac_default            the flown drag factor was computed with no MAC captured in the attempt (4.0 m default,
                              data_plane._friction_inputs; 3.1 A)
  flag.engine_scale_bench_ref the flown engine scale is a pyCycle Fn / 5,936 lbf (bench reference) although the
                              mission flew an external deck (3.1 C)
  flag.stale_mesh             the solve whose CL was flown ran on a mesh made before a later reshape, or of another
                              wing than the flown one (B271)
  flag.su2_not_flown          SU2 solves ran but the flown CL is none of them (or the wing changed and no solve ran)
                              (B272, aero part)
  flag.wing_mass_stale        the flown wing mass is not mass-mcp's current estimate of the flown wing (B272)
  flag.carried_engine_replaced the flown deck was built from another engine than the carried one with this link's
                              set_inputs (B270)
  flag.engine_scale_stale     the flown engine scale is not the last one the link tried to set (a refused set call
                              left the earlier scale; case 5, N_GR 19)
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

# Aviary inputs that carry a coupled value or a design variable into the mission.
FLOWN_PARAMS = ["Aircraft.Wing.AREA", "Aircraft.Wing.ASPECT_RATIO", "Aircraft.Wing.SWEEP",
                "Aircraft.Wing.TAPER_RATIO", "Aircraft.Fuselage.LENGTH", "Aircraft.Fuselage.MAX_HEIGHT",
                "Aircraft.Fuselage.MAX_WIDTH", "Aircraft.Engine.SCALE_FACTOR", "Mission.Design.LIFT_COEFFICIENT",
                "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR", "Aircraft.Wing.MASS", "Aircraft.Wing.MASS_SCALER"]
# Further flown values (not aviary aircraft parameters): the SU2 CL/CD behind the flown CL, the mission's takeoff
# mass, and the engine deck the run flew.
EXTRA_COLUMNS = ["aero.cl_cruise", "aero.cd_cruise", "structures.mtom_kg", "engine_deck_key"]
FLAGS = ["untrimmed_su2", "agent_target_cl", "mac_default", "engine_scale_bench_ref", "stale_mesh", "su2_not_flown",
         "wing_mass_stale", "carried_engine_replaced", "engine_scale_stale"]

# MAS-Aviary src/tools/coupling.py BENCH_ENGINE_CRUISE_FN_LBF (the bench deck's M0.78/FL330 thrust); the data plane
# divides pyCycle's Fn by it when it thinks no designed deck is attached (_inject_engine_scale).
BENCH_ENGINE_CRUISE_FN_LBF = 5936.0
ENGINE_KEYS = ["T4_MAX", "splitter.BPR", "fan.PR", "lpc.PR", "hpc.PR"]
# Calls that change an aviary session's inputs: a get_results after one of these no longer reports the flown inputs.
SESSION_CHANGING = ("set_aircraft_parameters", "configure_mission", "set_engine_deck", "set_mission_parameters")
# create_su2_session / configure_from_cpacs arguments the data plane injects (not the agent's).
SU2_INJECTED = {"target_cl", "initial_mesh", "mesh_file_name", "aspect_ratio", "ref_area", "ref_length",
                "oswald_efficiency", "overrides"}
FRAMEWORK_AGENTS = {"unknown", "framework", None, ""}


# ---------------------------------------------------------------- small helpers

def num(v):
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def close(a, b, rel) -> bool:
    a, b = num(a), num(b)
    return a is not None and b is not None and abs(a - b) <= rel * max(abs(b), 1e-12)


def call_ok(result, error=None) -> bool:
    """The same success rule as the reference scanner (scan_signatures.ok)."""
    if error:
        return False
    if isinstance(result, str) and result.lstrip().startswith("Error calling tool"):
        return False
    return not (isinstance(result, dict) and (result.get("success") is False or result.get("error_code")
                                              or result.get("status") == "error"))


def payload_len(v):
    if isinstance(v, str) and v.startswith("<") and v.endswith(" chars>"):
        try:
            return int(v[1:-7])
        except ValueError:
            return None
    return None


def b64len(nbytes) -> int:
    return 4 * math.ceil(nbytes / 3)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def load_rows(link_dir: Path, attempt: int, kb: list[dict]) -> tuple[list[dict], str]:
    """The calls of one attempt, in order: tool_calls_attempt<k>.jsonl (full arguments and results), else the
    knowledge base entries of that attempt. Each row: {tool, agent, args, result, ok}."""
    path = link_dir / f"tool_calls_attempt{attempt}.jsonl"
    if path.is_file():
        rows = []
        for r in _read_jsonl(path):
            res = r.get("result")
            rows.append({"tool": r.get("tool"), "agent": r.get("agent"), "args": r.get("args") or {},
                         "result": res, "ok": call_ok(res, r.get("error"))})
        return rows, path.name
    rows = [{"tool": e.get("tool"), "agent": e.get("agent"), "args": e.get("inputs") or {},
             "result": e.get("outputs"), "ok": e.get("status") == "success" and call_ok(e.get("outputs"))}
            for e in kb if int(e.get("attempt") or 1) == attempt]
    return rows, "knowledge_base.jsonl"


def _res(r) -> dict:
    return r["result"] if isinstance(r.get("result"), dict) else {}


def _fuel(r):
    return (_res(r).get("summary") or {}).get("fuel_burned_kg")


def deck_key(deck_path) -> str | None:
    if not deck_path:
        return None
    return os.path.basename(str(deck_path)).replace("deck_", "").replace(".deck", "")


def pycycle_defaults(rows: list[dict]) -> dict:
    """pyCycle's design-input defaults as get_design_inputs reports them (empty if no such call)."""
    out = {}
    for r in rows:
        if r["tool"] == "get_design_inputs" and isinstance(r.get("result"), dict):
            for e in r["result"].get("design_inputs") or []:
                if isinstance(e, dict) and e.get("name") in ENGINE_KEYS and num(e.get("default")) is not None:
                    out[e["name"]] = num(e["default"])
    return out


# ---------------------------------------------------------------- the counted mission and its inputs

def counted_mission(rows: list[dict], fuel) -> int | None:
    """Row index of the run_simulation that produced the link's fuel (the last one with exactly that fuel)."""
    m = None
    for i, r in enumerate(rows):
        if r["tool"] == "run_simulation" and r["ok"] and _fuel(r) is not None and close(_fuel(r), fuel, 1e-12):
            m = i
    return m


def flown_inputs(rows: list[dict], m: int, start_params: dict | None, start_src: str, log: str) -> dict:
    """{param: (value, source)} for FLOWN_PARAMS, plus the mission config, for the run_simulation at row m."""
    sid = rows[m]["args"].get("session_id")
    gr = None
    for i in range(m + 1, len(rows)):
        r = rows[i]
        if r["args"].get("session_id") != sid:
            continue
        if r["tool"] == "run_simulation" or (r["ok"] and r["tool"] in SESSION_CHANGING):
            break
        if r["tool"] == "get_results" and r["ok"]:
            dp = _res(r).get("design_parameters") or {}
            if isinstance(dp.get("aircraft_params"), dict) and dp["aircraft_params"]:
                gr = (i, dp)
                break
    out: dict = {}
    if gr is not None:
        i, dp = gr
        ap = dp["aircraft_params"]
        for p in FLOWN_PARAMS:
            out[p] = (ap.get(p), f"get_results {log} row {i}" if p in ap else
                      f"not set on the flown session (aviary default; get_results {log} row {i})")
        mc = dp.get("mission_config") or {}
        tom = mc.get("takeoff_mass_kg")
        out["structures.mtom_kg"] = (tom, f"get_results {log} row {i} mission_config.takeoff_mass_kg"
                                     if tom is not None else f"no takeoff_mass_kg in get_results {log} row {i}")
        out["_inputs_from"] = f"get_results row {i}"
        return out
    # No get_results that still describes the flight: session start + the successful sets before the run.
    state = {p: (v, start_src) for p, v in (start_params or {}).items()}
    for i, r in enumerate(rows[:m]):
        if r["tool"] == "set_aircraft_parameters" and r["ok"] and r["args"].get("session_id") == sid:
            params = r["args"].get("parameters") or {}
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except ValueError:
                    params = {}
            for p, v in params.items():
                state[p] = (v, f"set_aircraft_parameters {log} row {i}")
    for p in FLOWN_PARAMS:
        out[p] = state.get(p, (None, "not set on the flown session (aviary default; no get_results after the run)"))
    tom = None
    for i, r in enumerate(rows[:m]):
        if r["tool"] == "configure_mission" and r["ok"] and r["args"].get("session_id") == sid \
                and r["args"].get("takeoff_mass_kg") is not None:
            tom = (r["args"]["takeoff_mass_kg"], f"configure_mission {log} row {i}")
    out["structures.mtom_kg"] = tom or (None, "not recorded (no get_results after the run, no configure_mission "
                                              "with takeoff_mass_kg in the attempt)")
    out["_inputs_from"] = "session start + successful set_aircraft_parameters"
    return out


# ---------------------------------------------------------------- geometry / mesh tracking (B271, B272)

def _wing_from_after(res):
    a = ((res or {}).get("geometry_morph") or {}).get("after")
    if a:
        return {"area": a.get("reference_area"), "ar": a.get("aspect_ratio"), "sweep": a.get("sweep_deg")}
    return None


def _wing_from_get(res):
    p = (res or {}).get("parameters") or {}
    if p.get("area") is not None:
        return {"area": p.get("area"), "ar": p.get("aspect_ratio"), "sweep": p.get("sweep")}
    return None


def _same_wing(w, flown, rel=0.002):
    if not w or not flown:
        return None
    for k, fk in (("area", "Aircraft.Wing.AREA"), ("ar", "Aircraft.Wing.ASPECT_RATIO")):
        if w.get(k) is not None and flown.get(fk) is not None and not close(flown[fk], w[k], rel):
            return False
    return True


def _geometry_track(rows):
    """Per tigl session: the Wing1 shape and a reshape counter; meshes and exports with the shape they carry."""
    geo, version, meshes, exports, reshapes = {}, {}, [], [], []
    for i, r in enumerate(rows):
        t, a, res = r["tool"], r["args"], _res(r)
        if not r["ok"]:
            continue
        sid = a.get("session_id")
        if t == "open_cpacs":
            s = res.get("session_id")
            version[s], geo[s] = 0, None
        elif t == "get_high_level_parameters" and a.get("component_uid") == "Wing1":
            w = _wing_from_get(res)
            if w:
                geo[sid] = w
        elif t == "set_high_level_parameters":
            version[sid] = version.get(sid, 0) + 1
            reshapes.append((i, sid))
            if a.get("component_uid") == "Wing1":
                w = _wing_from_after(res)
                if w:
                    geo[sid] = w
        elif t == "generate_volume_mesh":
            nb = res.get("mesh_size_bytes")
            meshes.append((i, sid, geo.get(sid), b64len(nb) if nb else None))
        elif t == "export_cpacs":
            exports.append((i, sid, geo.get(sid), a.get("output_path")))
        elif t == "close_cpacs" and res.get("cpacs_file_path"):
            # tigl-mcp's close_cpacs exports the session it closes (cpacs_autoexport_<sid>.xml)
            exports.append((i, sid, geo.get(sid), res.get("cpacs_file_path")))
    return meshes, exports, reshapes


# ---------------------------------------------------------------- one link

def analyse(rows: list[dict], m: int, flown: dict, carry: dict | None, steps: list[dict], defaults: dict,
            log: str, kb_attempt: list[dict] | None = None) -> dict:
    """Extra flown columns (SU2 CL/CD, deck) and the bug-signature flags for the counted mission at row m."""
    out: dict = {}
    carry = carry or {}
    prev = carry.get("previous_design") or {}
    carried_engine = ((prev.get("design_space") or {}).get("engine")) or {}
    carried_wing = ((prev.get("geometry") or {}).get("wing")) or {}
    carried_aero = prev.get("aero") or {}
    carried_mass = (prev.get("structures") or {}).get("wing_mass_kg")
    fv = {p: flown[p][0] for p in FLOWN_PARAMS}
    sid = rows[m]["args"].get("session_id")
    before = rows[:m]
    flags, detail = {}, {}

    # --- engine deck the run flew ---
    eng = _res(rows[m]).get("engine") or {}
    key = deck_key(eng.get("deck"))
    out["engine_deck_key"] = (key, f"run_simulation {log} row {m} engine.deck" if key else
                              f"run_simulation {log} row {m} reports no engine deck")

    # --- the SU2 solve whose CL was flown ---
    cl = num(fv["Mission.Design.LIFT_COEFFICIENT"])
    solves = [(i, r) for i, r in enumerate(before) if r["tool"] == "run_su2_solver" and r["ok"]]
    prod = None
    for i, r in solves:
        if cl is not None and close((_res(r).get("final_coefficients") or {}).get("CL"), cl, 1e-8):
            prod = (i, r)
    if prod:
        i, r = prod
        fc, da = _res(r).get("final_coefficients") or {}, _res(r).get("drag_assessment") or {}
        out["aero.cl_cruise"] = (fc.get("CL"), f"run_su2_solver {log} row {i} final_coefficients.CL")
        if da.get("cd_reported") is not None:
            out["aero.cd_cruise"] = (da["cd_reported"], f"run_su2_solver {log} row {i} drag_assessment.cd_reported")
        else:
            out["aero.cd_cruise"] = (fc.get("CD"), f"run_su2_solver {log} row {i} final_coefficients.CD")
    elif cl is None:
        out["aero.cl_cruise"] = (None, "no Mission.Design.LIFT_COEFFICIENT flown (aviary default polar)")
        out["aero.cd_cruise"] = (None, "no Mission.Design.LIFT_COEFFICIENT flown (aviary default polar)")
    elif close(cl, carried_aero.get("cl_cruise"), 1e-8):
        out["aero.cl_cruise"] = (carried_aero.get("cl_cruise"), "carried: carry_over previous_design.aero.cl_cruise")
        out["aero.cd_cruise"] = (carried_aero.get("cd_cruise"), "carried: carry_over previous_design.aero.cd_cruise")
    else:
        out["aero.cl_cruise"] = (None, "flown CL matches no SU2 solve of this attempt nor the carried aero")
        out["aero.cd_cruise"] = (None, "flown CL matches no SU2 solve of this attempt nor the carried aero")

    # --- the configuration of the producing solve's session ---
    cfg = None
    if prod:
        psess = prod[1]["args"].get("session_id")
        for i, r in enumerate(rows[:prod[0]]):
            if not r["ok"]:
                continue
            if r["tool"] == "create_su2_session" and _res(r).get("session_id") == psess:
                cfg = (i, r)
            elif r["tool"] == "configure_from_cpacs" and r["args"].get("session_id") == psess:
                cfg = (i, r)

    # untrimmed SU2 (cases 1-2)
    if not prod:
        flags["untrimmed_su2"] = False
        detail["untrimmed_su2"] = "no SU2 solve of this attempt was flown"
    else:
        da = _res(prod[1]).get("drag_assessment") or {}
        why = []
        if cfg is None:
            why.append("session configuration not found")
        elif cfg[1]["args"].get("target_cl") is None:
            why.append(f"no target_cl in {cfg[1]['tool']} row {cfg[0]} (fixed AOA)")
        if da.get("source") == "su2_unchecked":
            why.append("drag source su2_unchecked")
        cd_rep = num(da.get("cd_reported"))
        floor = num(da.get("cd_induced_floor"))
        if cd_rep is not None and floor is not None and cd_rep < floor:
            why.append(f"CD {cd_rep:.6g} below induced drag {floor:.6g}")
        flags["untrimmed_su2"] = bool(why)
        detail["untrimmed_su2"] = f"solve row {prod[0]}: " + ("; ".join(why) if why else "trimmed")

    # agent-typed target CL (case 3.1 B)
    if cfg is None:
        flags["agent_target_cl"] = None if prod else False
        detail["agent_target_cl"] = "session configuration not found" if prod else "no SU2 solve of this attempt flown"
    elif cfg[1]["args"].get("target_cl") is None:
        flags["agent_target_cl"] = False
        detail["agent_target_cl"] = "no target CL"
    elif cfg[1].get("agent") in FRAMEWORK_AGENTS:
        flags["agent_target_cl"] = False
        detail["agent_target_cl"] = f"framework pre-init {cfg[1]['tool']} row {cfg[0]}"
    else:
        flags["agent_target_cl"], detail["agent_target_cl"] = _typed_target(cfg[1], steps, rows, cfg[0], kb_attempt or [])

    # MAC default (case 3.1 A): the set call that delivered the flown drag factor, and a MAC capture before it
    df = fv["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"]
    df_row = None
    for i, r in enumerate(before):
        if r["tool"] == "set_aircraft_parameters" and r["ok"] and r["args"].get("session_id") == sid:
            v = (r["args"].get("parameters") or {}).get("Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR")
            if v is not None and close(v, df, 1e-12):
                df_row = i
    if df is None:
        flags["mac_default"], detail["mac_default"] = None, "no drag factor flown"
    elif df_row is None:
        flags["mac_default"], detail["mac_default"] = None, "flown drag factor not set in this attempt (carried)"
    else:
        cap = None
        for i, r in enumerate(rows[:df_row]):
            if not r["ok"]:
                continue
            res, a = _res(r), r["args"]
            if r["tool"] == "get_wing_summary" and res.get("mac_length") is not None \
                    and (a.get("wing_uid") or a.get("component_uid") or "Wing1") == "Wing1":
                cap = i
            elif r["tool"] == "set_high_level_parameters" and a.get("component_uid") == "Wing1":
                gm = res.get("geometry_morph") or {}
                if gm.get("rebuilt") and (gm.get("after") or {}).get("mac_length") is not None:
                    cap = i
        flags["mac_default"] = cap is None
        detail["mac_default"] = (f"drag factor set at row {df_row}; " +
                                 (f"MAC captured at row {cap}" if cap is not None else "no MAC captured before it"))

    # engine scale on the bench reference although a deck flew (case 3.1 C)
    scale = num(fv["Aircraft.Engine.SCALE_FACTOR"])
    fns = [num(((_res(r).get("outputs") or {}).get("perf.Fn"))) for r in before
           if r["tool"] == "run_cycle" and r["ok"]]
    fns = [f for f in fns if f]
    ref = None
    for r in before:
        if r["tool"] == "generate_engine_deck" and r["ok"] and _res(r).get("design_key") == key:
            ref = num(_res(r).get("reference_fn_des_lbf"))
    if scale is None:
        flags["engine_scale_bench_ref"], detail["engine_scale_bench_ref"] = None, "no engine scale flown"
    elif not eng.get("external_deck"):
        flags["engine_scale_bench_ref"] = False
        detail["engine_scale_bench_ref"] = "bench deck flown (the bench reference is right)"
    else:
        bench = [f for f in fns if close(scale * BENCH_ENGINE_CRUISE_FN_LBF, f, 1e-9)]
        deck = [f for f in fns if ref is not None and close(scale * ref, f, 1e-9)]
        flags["engine_scale_bench_ref"] = bool(bench) and not deck
        detail["engine_scale_bench_ref"] = (
            f"scale {scale:.6f}; deck {key} reference {ref}; " +
            (f"= Fn {bench[-1]:.2f} / {BENCH_ENGINE_CRUISE_FN_LBF:.0f}" if bench else
             f"= Fn {deck[-1]:.2f} / {ref:.0f}" if deck else "matches no run_cycle Fn of this attempt"))

    # stale mesh (B271) / SU2 not flown (B272 aero)
    meshes, exports, reshapes = _geometry_track(rows)
    flown_wing = {k: fv[k] for k in ("Aircraft.Wing.AREA", "Aircraft.Wing.ASPECT_RATIO")}
    stale, notflown = [], []
    if prod:
        si, sr = prod
        sess = sr["args"].get("session_id")
        load = None
        for i, r in enumerate(rows[:si]):
            if not r["ok"]:
                continue
            if r["tool"] == "create_su2_session" and _res(r).get("session_id") == sess:
                pl = payload_len(r["args"].get("initial_mesh"))
                if pl is not None:
                    load = (i, pl)
            if r["tool"] == "set_mesh" and r["args"].get("session_id") == sess:
                pl = payload_len(r["args"].get("mesh_base64"))
                if pl is not None:
                    load = (i, pl)
        mesh = None
        if load:
            cands = [x for x in meshes if x[0] < load[0]]
            exact = [x for x in cands if x[3] == load[1]]
            mesh = (exact or cands or [None])[-1]
        if mesh:
            later = [x for x in reshapes if x[1] == mesh[1] and mesh[0] < x[0] < si]
            if later:
                stale.append(f"mesh row {mesh[0]} made before reshape row(s) {[x[0] for x in later]}, solve row {si}")
            if _same_wing(mesh[2], flown_wing) is False:
                stale.append(f"mesh row {mesh[0]} is of another wing than the flown one")
        if not stale and [x for x in reshapes if si < x[0] < m]:
            stale.append(f"reshape after the producing solve (row {si}), before the mission")
        detail["stale_mesh"] = "; ".join(stale) if stale else (
            f"solve row {si} on mesh row {mesh[0]}" if mesh else f"solve row {si}; mesh load not found")
        flags["stale_mesh"] = bool(stale)
        flags["su2_not_flown"], detail["su2_not_flown"] = False, f"flown CL from solve row {si}"
    else:
        flags["stale_mesh"], detail["stale_mesh"] = False, "no SU2 solve of this attempt was flown"
        if solves:
            notflown.append(f"{len(solves)} SU2 solve(s) ran but the flown CL ({cl}) is none of them")
        elif carried_wing and not all(close(fv.get(k), carried_wing.get(k), 0.002)
                                      for k in ("Aircraft.Wing.AREA", "Aircraft.Wing.ASPECT_RATIO")
                                      if carried_wing.get(k) is not None):
            notflown.append("no solve, but the flown wing is not the carried one")
        flags["su2_not_flown"] = bool(notflown)
        detail["su2_not_flown"] = "; ".join(notflown) if notflown else "no solve; carried aero on the carried wing"

    # wing mass (B272)
    mass_flown = num(fv["Aircraft.Wing.MASS"])
    est = [(i, r) for i, r in enumerate(before) if r["tool"] == "estimate_mass" and r["ok"]]
    why = []
    if est:
        ei, er = est[-1]
        mw = (((_res(er).get("mass_breakdown") or {}).get("components")) or {}).get("mWing_kg")
        if not close(mass_flown, mw, 1e-3):
            why.append(f"flown wing mass {mass_flown} != mass-mcp {mw} (row {ei})")
        used = os.path.basename(er["args"].get("cpacs_file_path") or "")
        exps = [x for x in exports if x[0] < ei]
        exps = [x for x in exps if os.path.basename(x[3] or "") == used] or exps
        if exps:
            if _same_wing(exps[-1][2], flown_wing) is False:
                why.append(f"estimate row {ei} read a geometry other than the flown wing")
        else:
            if [x for x in reshapes if x[0] < ei]:
                why.append(f"estimate row {ei} read the start file after a reshape")
            if carried_wing and _same_wing({"area": carried_wing.get("Aircraft.Wing.AREA"),
                                            "ar": carried_wing.get("Aircraft.Wing.ASPECT_RATIO")},
                                           flown_wing) is False:
                why.append(f"estimate row {ei} of the start geometry, flown wing differs")
    else:
        if carried_wing and _same_wing({"area": carried_wing.get("Aircraft.Wing.AREA"),
                                        "ar": carried_wing.get("Aircraft.Wing.ASPECT_RATIO")}, flown_wing) is False:
            why.append("no estimate, wing changed vs carried design")
        if carried_mass is not None and not close(mass_flown, carried_mass, 1e-3):
            why.append(f"no estimate; flown mass {mass_flown} != carried {carried_mass}")
    flags["wing_mass_stale"] = bool(why)
    detail["wing_mass_stale"] = "; ".join(why) if why else "flown wing mass is mass-mcp's current one"

    # carried engine replaced (B270)
    intended = {k: num(v) for k, v in carried_engine.items() if k in ENGINE_KEYS}
    for r in before:
        if r["tool"] == "set_inputs" and r["ok"]:
            for k, v in (r["args"].get("values") or {}).items():
                if k in ENGINE_KEYS:
                    intended[k] = num(v)
    gen = None
    for i, r in enumerate(before):
        if r["tool"] == "generate_engine_deck" and r["ok"] and key and _res(r).get("design_key") == key:
            gen = (i, r)
    if not intended:
        flags["carried_engine_replaced"], detail["carried_engine_replaced"] = False, "no carried or set engine"
    elif gen is None:
        flags["carried_engine_replaced"] = False
        detail["carried_engine_replaced"] = f"deck {key} not generated in this attempt (not checked)"
    elif not all(k in defaults for k in ENGINE_KEYS):
        flags["carried_engine_replaced"] = None
        detail["carried_engine_replaced"] = "pyCycle defaults unknown (no get_design_inputs in the study)"
    else:
        gi, gr = gen
        psid = gr["args"].get("session_id")
        eff = dict(defaults)
        for r in rows[:gi]:
            if r["tool"] == "set_inputs" and r["ok"] and r["args"].get("session_id") == psid:
                for k, v in (r["args"].get("values") or {}).items():
                    if k in ENGINE_KEYS:
                        eff[k] = num(v)
        diff = {k: (eff.get(k), intended[k]) for k in ENGINE_KEYS
                if intended.get(k) is not None and not close(eff.get(k), intended[k], 1e-3)}
        flags["carried_engine_replaced"] = bool(diff)
        detail["carried_engine_replaced"] = (f"deck {key} (row {gi}) built from {eff}; intended {intended}"
                                             if diff else f"deck {key} (row {gi}) matches the intended engine")

    # engine scale left stale by a refused set (case 5, D)
    attempted = None
    for i, r in enumerate(before):
        if r["tool"] == "set_aircraft_parameters" and r["args"].get("session_id") == sid:
            sf = (r["args"].get("parameters") or {}).get("Aircraft.Engine.SCALE_FACTOR")
            if sf is not None:
                attempted = (i, num(sf), r["ok"])
    if attempted is None or scale is None:
        flags["engine_scale_stale"] = False
        detail["engine_scale_stale"] = "no engine scale set in this attempt" if attempted is None else "no scale flown"
    else:
        flags["engine_scale_stale"] = not close(scale, attempted[1], 1e-4)
        detail["engine_scale_stale"] = (f"flown {scale}; last attempted {attempted[1]} at row {attempted[0]} "
                                        f"({'succeeded' if attempted[2] else 'refused/failed'})")

    for f in FLAGS:
        out[f"flag.{f}"] = flags.get(f)
        out[f"flag.{f}.detail"] = detail.get(f)
    return out


def _model_calls(steps: list[dict], tool: str) -> list[dict]:
    """The model's raw arguments of every call to `tool` in the step log, in order."""
    out = []
    for s in steps:
        for tc in s.get("tool_calls") or []:
            if not isinstance(tc, dict) or tc.get("name") != tool:
                continue
            a = tc.get("arguments") or {}
            if isinstance(a, str):
                try:
                    a = json.loads(a)
                except ValueError:
                    a = {}
            out.append(a if isinstance(a, dict) else {})
    return out


def _typed_value(a: dict):
    # Only the target_cl ARGUMENT pins the lift target: a TARGET_CL inside overrides is dropped by su2-mcp
    # (removed_invalid_options) and the data plane still injects its own target_cl (O_GR 17, KB seq 27).
    return num(a.get("target_cl"))


def _typed_target(cfg_row: dict, steps: list[dict], rows: list[dict], cfg_i: int,
                  kb_attempt: list[dict]) -> tuple[bool | None, str]:
    """Did the model type the target_cl of this create_su2_session / configure_from_cpacs call?

    The step log holds the model's raw arguments, before the data plane (which fills target_cl only when the caller
    gave none, data_plane._inject_target_cl, and rewrites cpacs_file_path to the exported geometry). The call is
    matched on the arguments the data plane does not touch; when those candidates disagree, by its position among the
    model's calls to the same tool (only if the step log and the tool log hold the same number of them)."""
    resolved = num(cfg_row["args"].get("target_cl"))
    calls = _model_calls(steps, cfg_row["tool"])
    ignore = SU2_INJECTED | {"cpacs_file_path"}
    cands = [a for a in calls
             if all(json.dumps(cfg_row["args"].get(k), sort_keys=True) == json.dumps(v, sort_keys=True)
                    for k, v in a.items() if k not in ignore)]

    def verdict(a: dict, how: str) -> tuple[bool | None, str]:
        t = _typed_value(a)
        if t is None:
            return False, f"framework target_cl {resolved} ({how}: agent typed none)"
        if close(t, resolved, 1e-12):
            return True, f"agent typed target_cl {t} ({how})"
        return None, f"agent typed {t} but the call ran with {resolved} ({how})"

    if not cands:
        return None, "model call not found in the step log"
    results = {verdict(a, "")[0] for a in cands}
    if len(results) == 1:
        return verdict(cands[-1], f"{len(cands)} matching model call(s)")
    typed_eq = [a for a in cands if verdict(a, "")[0] is True]
    if not typed_eq:
        # The data plane never replaces a typed target_cl, so a call that ran with a value no model call typed got
        # it from the framework.
        return False, (f"framework target_cl {resolved} (none of {len(cands)} matching model calls typed it: "
                       f"{[_typed_value(a) for a in cands]})")
    if not [a for a in cands if _typed_value(a) is None]:
        return True, f"agent typed target_cl {resolved} ({len(cands)} matching model calls, none untyped)"
    model_rows = [i for i, r in enumerate(rows) if r["tool"] == cfg_row["tool"] and r.get("agent") not in FRAMEWORK_AGENTS]
    if cfg_i not in model_rows:
        return None, "configuring call is not a model call"
    k = model_rows.index(cfg_i)
    if len(model_rows) == len(calls):
        return verdict(calls[k], "matched by position")
    # Calls the framework refused never reach the tool log but are in the step log and in the knowledge base
    # (status refused): align model calls with KB entries, and executed KB entries with the tool log.
    kb_tool = [e for e in kb_attempt if e.get("tool") == cfg_row["tool"] and e.get("agent") not in FRAMEWORK_AGENTS]
    executed = [j for j, e in enumerate(kb_tool) if e.get("status") != "refused"]
    if len(kb_tool) == len(calls) and len(executed) == len(model_rows):
        return verdict(calls[executed[k]], "matched by position via the knowledge base")
    return None, (f"{len(cands)} matching model calls disagree and positions cannot be aligned ({len(model_rows)} "
                  f"tool rows, {len(calls)} model calls, {len(kb_tool)} KB entries / {len(executed)} executed)")


def flown_link(link_dir: Path, kb: list[dict], counted: dict, res: dict, carry: dict | None,
               steps_by_attempt, defaults: dict) -> dict:
    """All flown columns (value + src.<column>) and flags for one link. counted = the link's last mission summary
    (extract.mission_summaries), or {} when the link ran no mission."""
    cols = FLOWN_PARAMS + EXTRA_COLUMNS
    row: dict = {}
    uncounted = ""
    if not counted:
        # No successful mission in the knowledge base. A run_simulation may still have returned a fuel figure that
        # the KB recorded as failed (N_IF 12: 8.2e-06 kg, converged false): its inputs are what aviary flew, so they
        # are reported, labelled as not counted (the link did not fly; fuel_kg stays empty).
        for e in kb:
            if e.get("tool") == "run_simulation" and isinstance(e.get("outputs"), dict) \
                    and (e["outputs"].get("summary") or {}).get("fuel_burned_kg") is not None:
                counted = {"attempt": int(e.get("attempt") or 1), "seq": e.get("seq"),
                           "fuel_burned_kg": e["outputs"]["summary"]["fuel_burned_kg"]}
        if counted:
            uncounted = (f"NOT COUNTED (the link did not fly; KB seq {counted['seq']} has status failed): ")
    if not counted:
        for c in cols:
            row[c], row[f"src.{c}"] = None, "no run_simulation returned a fuel figure"
        for f in FLAGS:
            row[f"flag.{f}"], row[f"flag.{f}.detail"] = None, "no mission"
        row["flown_inputs_from"] = None
        return row
    attempt = int(counted.get("attempt") or 1)
    rows, log = load_rows(link_dir, attempt, kb)
    m = counted_mission(rows, counted.get("fuel_burned_kg"))
    if m is None:
        why = f"counted run_simulation (fuel {counted.get('fuel_burned_kg')}) not found in {log}"
        for c in cols:
            row[c], row[f"src.{c}"] = None, why
        for f in FLAGS:
            row[f"flag.{f}"], row[f"flag.{f}.detail"] = None, why
        row["flown_inputs_from"] = None
        return row
    if res.get("initial_params"):
        start, start_src = res["initial_params"], "result.json initial_params (session start)"
    else:
        start = (carry or {}).get("aviary_session_start")
        start_src = "carry_over.aviary_session_start (session start)"
    fl = flown_inputs(rows, m, start, start_src, log)
    extra = analyse(rows, m, fl, carry, steps_by_attempt(attempt), defaults, log,
                    [e for e in kb if int(e.get("attempt") or 1) == attempt])
    for c in FLOWN_PARAMS + ["structures.mtom_kg"]:
        row[c], row[f"src.{c}"] = fl[c]
    for c in ("aero.cl_cruise", "aero.cd_cruise", "engine_deck_key"):
        row[c], row[f"src.{c}"] = extra.pop(c)
    row.update(extra)
    row["flown_inputs_from"] = uncounted + fl["_inputs_from"]
    row["counted_mission_row"] = uncounted + f"{log} row {m}"
    if uncounted:
        for c in cols:
            row[f"src.{c}"] = uncounted + str(row[f"src.{c}"])
    for c in FLOWN_PARAMS + ["aero.cl_cruise", "aero.cd_cruise", "structures.mtom_kg"]:
        if row[c] is not None:
            row[c] = num(row[c])
    return row
