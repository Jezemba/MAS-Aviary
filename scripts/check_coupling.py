"""Check that each coupled value reached the tool that consumes it, and each recommendation its next link.

    python scripts/check_coupling.py logs/stat_results/<sweep dir> [more dirs] [--json]

Needs a sweep run with MAS_STEP_LOG=1: tool_calls_attemptN.jsonl holds the arguments each tool RECEIVED,
after every injection, including the framework's own calls. Compares the value a tool produced with the value
the next tool received.

Each check is PASS, FAIL or NOT_EXERCISED. NOT_EXERCISED means this link never took that path (no reshape, for
example, leaves "the mesh follows the wing" untested), which is not evidence that it works.

This asks whether the information ARRIVES. Whether an agent uses a recommendation, or an orchestrator passes it
on, is a coordination question and is reported only as information. Reads files only. Exit 1 on any FAIL.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PASS, FAIL, NOT_EXERCISED = "PASS", "FAIL", "NOT_EXERCISED"
MISSION_MACH, MISSION_ALT_FT = 0.78, 33000.0
DEFAULT_FN_DES = 5900.0
MAIN_WING = "Wing1"
WING_KEYS = {"Aircraft.Wing.AREA": "area", "Aircraft.Wing.ASPECT_RATIO": "aspect_ratio",
             "Aircraft.Wing.SWEEP": "sweep", "Aircraft.Wing.TAPER_RATIO": "taper_ratio"}


def find(data: Any, key: str) -> Any:
    """The first value under ``key`` anywhere in nested data, or None."""
    if isinstance(data, dict):
        if data.get(key) is not None:
            return data[key]
        data = list(data.values())
    if isinstance(data, list):
        for item in data:
            value = find(item, key)
            if value is not None:
                return value
    return None


def number(value: Any) -> float | None:
    """A number, also from the text form agents often send ('2857'); None otherwise (B260)."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def close(a: Any, b: Any, rel: float) -> bool:
    a, b = number(a), number(b)
    return a is not None and b is not None and abs(a - b) <= rel * max(abs(b), 1e-12)


def ok(row: dict) -> bool:
    result = row.get("result")
    if row.get("error"):
        return False
    # An MCP tool error comes back as the result text with no error field (l2c orch_iter 2026-11-10: an
    # "Unknown session_id" reply was read as the link's first wing measurement, all None).
    if isinstance(result, str) and result.lstrip().startswith("Error calling tool"):
        return False
    # l20 orch_graph links 2 and 6 (2026-11-12): mass-mcp answers a failed estimate with status "error" and every
    # mass 0.0; that is not a wing mass (the framework ignored it and the mission flew the previous estimate).
    return not (isinstance(result, dict) and (result.get("success") is False or result.get("error_code")
                                              or result.get("status") == "error"))


class Link:
    """One finished link: its ordered tool calls and its result files."""

    def __init__(self, directory: Path):
        self.dir = directory
        self.result = json.loads((directory / "result.json").read_text())
        # B265: the record of the last FLOWN mission when there is one; design.json may hold a later, unflown design.
        # The coordination record is the link's last word, so it comes from design.json -- the same rule as
        # stat_batch_runner.link_design (not imported: the runner loads torch). l2c orch_graph 2026-11-10: the
        # flown record stopped before the final COMPLETE verdict and the check graded an older recommendation.
        final_path, flown_path = directory / "design.json", directory / "design_flown.json"
        final = json.loads(final_path.read_text()) if final_path.is_file() else {}
        self.design = json.loads(flown_path.read_text()) if flown_path.is_file() else dict(final)
        for key in ("coordination_signals", "graph_route"):
            if key in final:
                self.design[key] = final[key]
        carry = directory / "carry_over.json"
        self.carry_over = json.loads(carry.read_text()) if carry.is_file() else {}
        log = directory / f"tool_calls_attempt{int(self.result.get('attempt') or 1)}.jsonl"
        self.calls = [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else None

    def rows(self, *tools: str, before: int | None = None) -> list[tuple[int, dict]]:
        limit = len(self.calls) if before is None else before
        return [(i, r) for i, r in enumerate(self.calls[:limit]) if r["tool"] in tools and ok(r)]

    def last(self, *tools: str, before: int | None = None) -> tuple[int, dict] | tuple[None, None]:
        rows = self.rows(*tools, before=before)
        return rows[-1] if rows else (None, None)

    def final_mission(self) -> tuple[int, dict] | tuple[None, None]:
        rows = [(i, r) for i, r in self.rows("run_simulation") if find(r.get("result"), "fuel_burned_kg") is not None]
        return rows[-1] if rows else (None, None)

    def flown_parameters(self, before: int) -> dict:
        merged: dict = {}
        for _, row in self.rows("set_aircraft_parameters", before=before):
            merged.update(row["args"].get("parameters") or {})
        return merged

    def starting_wing(self) -> dict | None:
        """The main wing as the link found it: the first measurement, or the first reshape's "before" (B260)."""
        for _, row in self.rows("set_high_level_parameters", "morph_wing", "get_high_level_parameters"):
            if row["args"].get("component_uid", row["args"].get("wing_uid")) != MAIN_WING:
                continue
            before = find(row["result"], "before")
            if before:
                return {"area": before.get("reference_area"), "aspect_ratio": before.get("aspect_ratio"),
                        "sweep": before.get("sweep_deg"), "taper_ratio": before.get("taper_ratio")}
            if row["tool"] == "get_high_level_parameters":
                return {k: find(row["result"], k) for k in ("area", "aspect_ratio", "sweep", "taper_ratio")}
        return None

    def wing_at(self, before: int) -> tuple[dict | None, int | None]:
        """The main wing as last measured before this point, and the index of the last reshape."""
        wing, morphed = None, None
        for i, row in self.rows("set_high_level_parameters", "morph_wing", "get_high_level_parameters", before=before):
            if row["args"].get("component_uid", row["args"].get("wing_uid")) != MAIN_WING:
                continue
            after = find(row["result"], "after")
            if after:
                wing = {"area": after.get("reference_area"), "aspect_ratio": after.get("aspect_ratio"),
                        "sweep": after.get("sweep_deg"), "taper_ratio": after.get("taper_ratio")}
                morphed = i
            elif row["tool"] == "get_high_level_parameters":
                wing = {k: find(row["result"], k) for k in ("area", "aspect_ratio", "sweep", "taper_ratio")}
        return wing, morphed

    def prompts(self) -> dict[str, str]:
        """Everything each agent was shown, from the steps' input messages."""
        trace = self.dir / "trace.json"
        seen: dict[str, str] = {}
        if not trace.is_file():
            return seen
        for agent, value in json.loads(trace.read_text()).items():
            steps = value if isinstance(value, list) else value.get("steps", value.get("trace", []))
            seen[agent] = " ".join(json.dumps(s.get("model_input_messages", ""), default=str, ensure_ascii=False) + str(s.get("task", ""))
                                   for s in steps if isinstance(s, dict))
        return seen


def check_geometry_to_su2(link: Link, mission: int) -> list[tuple]:
    out = []
    solves = link.rows("run_su2_solver", before=mission)
    if not solves:
        return [("geometry -> SU2 references", NOT_EXERCISED, "no SU2 solve in this link"),
                ("geometry -> SU2 mesh", NOT_EXERCISED, "no SU2 solve in this link")]
    index, solve = solves[-1]
    wing, morphed = link.wing_at(index)
    session = solve["args"].get("session_id")
    configured = None
    for i, row in link.rows("create_su2_session", "configure_from_cpacs", before=index):
        if (find(row["result"], "session_id") or row["args"].get("session_id")) == session:
            configured = row["result"]
    area = find(configured, "REF_AREA")
    ratio = find(configured, "aspect_ratio")
    if not wing or area is None:
        out.append(("geometry -> SU2 references", NOT_EXERCISED, "no measured wing or no recorded references"))
    elif close(area, wing["area"], 0.005) and (ratio is None or close(ratio, wing["aspect_ratio"], 0.005)):
        out.append(("geometry -> SU2 references", PASS, f"REF_AREA {area:.3f}, AR {ratio}, wing {wing['area']:.3f}"))
    else:
        out.append(("geometry -> SU2 references", FAIL, f"REF_AREA {area}, AR {ratio}, wing {wing}"))
    if morphed is None:
        out.append(("geometry -> SU2 mesh", NOT_EXERCISED, "the wing was not reshaped before this solve"))
    else:
        meshed, _ = link.last("generate_volume_mesh", before=index)
        loaded = [i for i, r in link.rows("set_mesh", "create_su2_session", before=index)
                  if meshed is not None and i > meshed and (find(r["result"], "session_id") or r["args"].get("session_id")) == session]
        good = meshed is not None and meshed > morphed and bool(loaded)
        out.append(("geometry -> SU2 mesh", PASS if good else FAIL,
                    f"reshape #{morphed}, mesh #{meshed}, loaded into the session {loaded}"))
    return out


def check_geometry_to_mission(link: Link, mission: int) -> tuple:
    wing, _ = link.wing_at(mission)
    if not wing:
        return "geometry -> aviary", NOT_EXERCISED, "the wing was not measured in this link"
    flown = link.flown_parameters(mission)
    wrong = {k: (flown.get(k), wing[short]) for k, short in WING_KEYS.items()
             if wing.get(short) is not None and not close(flown.get(k), wing[short], 0.002)}
    return ("geometry -> aviary", FAIL, f"flown vs measured {wrong}") if wrong else \
        ("geometry -> aviary", PASS, f"area {flown.get('Aircraft.Wing.AREA')}, AR {flown.get('Aircraft.Wing.ASPECT_RATIO')}, "
                                     f"sweep {flown.get('Aircraft.Wing.SWEEP')}")


def flops_contract(link: Link) -> bool:
    """V2: the link ran under the drag model decision of 2026-11-18 (B324): SU2 solved and recorded, not coupled."""
    aero = link.design.get("aero") or {}
    return "injected_into_aviary" in aero or "not coupled" in str(aero.get("drag_coupling") or "")


def check_su2_to_mission(link: Link, mission: int) -> tuple:
    if flops_contract(link):
        return check_su2_recorded(link, mission)
    index, solve = link.last("run_su2_solver", before=mission)
    if solve is None:
        return "SU2 -> aviary", NOT_EXERCISED, "no SU2 solve before the mission (seeded results)"
    flown = link.flown_parameters(mission)
    cl, cd = find(solve["result"], "CL"), find(solve["result"], "cd_reported") or find(solve["result"], "CD")
    factor = flown.get("Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR")
    aero = link.design.get("aero") or {}
    problems = []
    if not close(flown.get("Mission.Design.LIFT_COEFFICIENT"), cl, 1e-6):
        problems.append(f"CL flown {flown.get('Mission.Design.LIFT_COEFFICIENT')}, solved {cl}")
    if not factor:
        problems.append("no drag factor reached aviary")
    if not close(aero.get("cd_cruise"), cd, 1e-6):
        problems.append(f"recorded CD {aero.get('cd_cruise')}, solved {cd}")
    if problems:
        return "SU2 -> aviary", FAIL, "; ".join(problems)
    return "SU2 -> aviary", PASS, f"CL {cl}, CD {cd}, drag factor {factor}"


def check_su2_recorded(link: Link, mission: int) -> tuple:
    """V2, contract B324 (drag model decision 2026-11-18): a current, converged SU2 solve trimmed to the cruise lift
    of the trim mass basis, its CL/CD recorded, and nothing of it injected into aviary (FLOPS drag flies)."""
    name = "SU2 solved + recorded (not coupled)"
    index, solve = link.last("run_su2_solver", before=mission)
    aero = link.design.get("aero") or {}
    if solve is None:
        if (aero.get("provenance") or {}).get("solved_on_this_link") is False:
            return name, NOT_EXERCISED, "no SU2 solve in this link (seeded results)"
        return name, FAIL, "no SU2 solve before the mission"
    problems = []
    cl, cd = find(solve["result"], "CL"), find(solve["result"], "cd_reported") or find(solve["result"], "CD")
    if not close(aero.get("cl_cruise"), cl, 1e-6) or not close(aero.get("cd_cruise"), cd, 1e-6):
        problems.append(f"recorded CL/CD {aero.get('cl_cruise')}/{aero.get('cd_cruise')}, solved {cl}/{cd}")
    if aero.get("solve_converged") is not True or aero.get("solve_trimmed") is not True:
        problems.append(f"solve converged {aero.get('solve_converged')}, trimmed {aero.get('solve_trimmed')}")
    basis = aero.get("solve_trim_basis") or {}
    target = aero.get("solve_target_cl")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from src.tools.coupling import cruise_lift_coefficient

        want = cruise_lift_coefficient(float(basis["mass_kg"]), MISSION_MACH, MISSION_ALT_FT, float(basis["wing_area_m2"]))
        if not close(target, want, 1e-6):
            problems.append(f"target CL {target}, cruise lift of {basis['mass_kg']} kg on {basis['wing_area_m2']} m^2 "
                            f"is {want}")
    except (KeyError, TypeError, ValueError) as exc:
        problems.append(f"no trim basis recorded ({exc})")
    flown = link.flown_parameters(mission)
    injected = {k: flown[k] for k in ("Mission.Design.LIFT_COEFFICIENT", "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR")
                if flown.get(k) is not None}
    injected.update({k: v for k, v in (aero.get("injected_into_aviary") or {}).items() if v is not None})
    if injected:
        problems.append(f"SU2 values reached aviary {injected}")
    if problems:
        return name, FAIL, "; ".join(problems)
    return name, PASS, f"CL {cl} (target {target}), CD {cd} recorded; aviary flies FLOPS drag"


def check_cruise_point_engine(link: Link, mission: int, flight: dict) -> list[tuple]:
    """V2: the engine is sized on aviary's cruise point of the flown design at the trim mass basis (B340/B360)."""
    out = []
    point = link.design.get("aviary_cruise_point") or {}
    flown_fp = find(flight.get("result"), "flown_design_fingerprint")
    if not point:
        return [("cruise point = flown design", FAIL, "no aviary cruise point recorded"),
                ("cruise point -> Fn_DES", FAIL, "no aviary cruise point recorded")]
    out.append(("cruise point = flown design", PASS if point.get("design_fingerprint") == flown_fp else FAIL,
                f"cruise point {point.get('design_fingerprint')}, flown {flown_fp}"))
    _, inputs = link.last("set_inputs", before=mission)
    fn_des = find((inputs or {}).get("args"), "Fn_DES")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from src.tools.coupling import fn_des_from_lift_to_drag

        want = fn_des_from_lift_to_drag(float(point["mass_kg"]), float(point["lift_to_drag"]))
    except (KeyError, TypeError, ValueError) as exc:
        return out + [("cruise point -> Fn_DES", FAIL, f"cruise point incomplete ({exc})")]
    if fn_des is None:
        return out + [("cruise point -> Fn_DES", NOT_EXERCISED, "no set_inputs with Fn_DES before the mission")]
    out.append(("cruise point -> Fn_DES", PASS if close(fn_des, want, 1e-6) else FAIL,
                f"Fn_DES {fn_des}, from {point['mass_kg']} kg / L/D {point['lift_to_drag']}: {want}"))
    return out


def check_mass_basis(link: Link, mission: int) -> tuple:
    """V2 / A8: one mass basis -- the SU2 trim, the cruise point (Fn_DES) and the take-off mass are the same mass.

    A graph-routed link whose SU2 result was SEEDED from the last flown link (graph pre-init, I5; recorded, no fuel
    effect under B324) carries that link's trim mass; its basis is then this link's own (provenance.this_link_trim_mass_kg).
    """
    name = "trim mass basis"
    aero = link.design.get("aero") or {}
    prov = aero.get("provenance") or {}
    seeded = prov.get("solved_on_this_link") is False
    basis = prov.get("this_link_trim_mass_kg") if seeded else (aero.get("solve_trim_basis") or {}).get("mass_kg")
    point = (link.design.get("aviary_cruise_point") or {}).get("mass_kg")
    _, conf = link.last("configure_mission", before=mission)
    takeoff = number(((conf or {}).get("args") or {}).get("takeoff_mass_kg"))
    if basis is None or point is None or takeoff is None:
        return name, FAIL, f"trim {basis}, cruise point {point}, take-off {takeoff}: one is not recorded"
    note = f" (SU2 seeded, trimmed at {prov.get('trim_mass_kg')} kg on the last flown link)" if seeded else ""
    if not close(point, basis, 1e-9):
        return name, FAIL, f"cruise point mass {point} != trim mass {basis}{note}"
    if close(takeoff, basis, 1e-9):
        return name, PASS, f"{basis} kg for trim, cruise point and take-off{note}"
    if abs(takeoff - float(basis)) <= 0.05 + 1e-9:
        # Before V7 (MAS-Aviary fix/validation1-defaults) the take-off mass was sent rounded to 0.1 kg.
        return name, PASS, (f"{basis} kg for trim and cruise point; take-off {takeoff} (rounded to 0.1 kg, pre-V7 "
                            f"code){note}")
    return name, FAIL, f"take-off mass {takeoff} != trim mass {basis}{note}"


def check_tails_tc(link: Link, mission: int) -> tuple:
    """V2 / G1 / J2: aviary flies mass-mcp's measured tails and wing t/c of this design."""
    name = "measured tails + wing t/c -> aviary"
    st = link.design.get("structures") or {}
    tails = st.get("tails") or {}
    tc = st.get("wing_thickness_to_chord")
    if not tails or tc is None:
        return name, FAIL, f"not recorded (tails {bool(tails)}, t/c {tc}): {st.get('tail_error') or st.get('wing_thickness_to_chord_error')}"
    flown = link.flown_parameters(mission)
    want = dict(tails, **{"Aircraft.Wing.THICKNESS_TO_CHORD": tc})
    wrong = {k: (flown.get(k), v) for k, v in want.items() if not close(flown.get(k), v, 1e-6)}
    return (name, FAIL, f"flown vs measured {wrong}") if wrong else (name, PASS, f"8 tail inputs + t/c {tc}")


def check_fuselage(link: Link, mission: int) -> tuple:
    """V2 / V4: the flown fuselage is the measured one (start-of-link session values count as flown)."""
    name = "geometry -> aviary fuselage"
    measured = None
    for _, row in link.rows("get_high_level_parameters", "get_fuselage_summary", "set_high_level_parameters",
                            before=mission):
        args, res = row["args"] or {}, row["result"]
        uid = str(args.get("component_uid") or args.get("fuselage_uid") or "Fuselage1")
        if not uid.lower().startswith("fuselage"):
            continue
        if row["tool"] == "get_high_level_parameters":
            p = find(res, "parameters") or {}
            if p.get("length") is not None:
                measured = (p.get("length"), p.get("max_height"), p.get("max_width"))
        elif row["tool"] == "get_fuselage_summary":
            measured = (find(res, "length"), find(res, "max_height"), find(res, "max_width"))
        else:
            after = find(res, "after") or {}
            if after.get("length") is not None:
                measured = (after.get("length"), after.get("height"), after.get("width"))
    if measured is None:
        return name, NOT_EXERCISED, "the fuselage was not measured in this link"
    flown = dict(link.carry_over.get("aviary_session_start") or {})
    # A session value nobody set this link (pre-session or carried) shows in aviary's get_results (V4: N_IF link 1
    # flew the runner's 37.68 / 4.15 / 3.96 that no set_aircraft_parameters named).
    for _, row in link.rows("get_results"):
        held = find(row["result"], "aircraft_params")
        if isinstance(held, dict):
            flown.update({k: v for k, v in held.items() if k.startswith("Aircraft.Fuselage.")})
    flown.update(link.flown_parameters(mission))
    keys = ("Aircraft.Fuselage.LENGTH", "Aircraft.Fuselage.MAX_HEIGHT", "Aircraft.Fuselage.MAX_WIDTH")
    wrong = {k: (flown.get(k), m) for k, m in zip(keys, measured) if not close(flown.get(k), m, 1e-4)}
    return (name, FAIL, f"flown vs measured {wrong}") if wrong else (name, PASS, f"{measured}")


def check_mass(link: Link, mission: int) -> list[tuple]:
    index, mass = link.last("estimate_mass", before=mission)
    if mass is None:
        return [("mass -> aviary", NOT_EXERCISED, "no estimate_mass before the mission (seeded results)"),
                ("geometry -> mass", NOT_EXERCISED, "no estimate_mass before the mission")]
    flown = link.flown_parameters(mission).get("Aircraft.Wing.MASS")
    wing_kg = find(mass["result"], "mWing_kg")
    out = [("mass -> aviary", PASS if close(flown, wing_kg, 0.001) else FAIL, f"wing mass flown {flown}, estimated {wing_kg}")]
    _, morphed = link.wing_at(mission)
    if morphed is None:
        out.append(("geometry -> mass", NOT_EXERCISED, "the wing was not reshaped"))
    elif index < morphed:
        out.append(("geometry -> mass", FAIL, f"mass #{index} ran before the last reshape #{morphed}"))
    else:
        _, exported = link.last("export_cpacs", before=index)
        path = find(exported["result"], "cpacs_file_path") if exported else None
        used = mass["args"].get("cpacs_file_path")
        # G1: the framework hands mass-mcp a copy of the file (avion_mass_*/<same name>), so it cannot write its
        # breakdown back into the design's file (B260: compared as a path, that copy read as a FAIL).
        copy = bool(used and path) and Path(used).name == Path(path).name and Path(used).parent.name.startswith("avion_mass_")
        # tigl-mcp close_cpacs writes the session it closes to cpacs_autoexport_<session>.xml (cpacs_io.py); mass
        # reading that file after the reshaped session was closed reads the reshaped wing.
        sid = (link.calls[morphed]["args"] or {}).get("session_id")
        closed = any(r["args"].get("session_id") == sid for i, r in link.rows("close_cpacs", before=index)
                     if i > morphed)
        auto = bool(used and sid and closed) and Path(used).name == f"cpacs_autoexport_{sid}.xml"
        copy = copy or auto
        out.append(("geometry -> mass", PASS if used and (used == path or copy) else FAIL,
                    f"mass read {used}{' (framework copy)' if copy else ''}, last export {path}"))
    return out


def check_propulsion(link: Link, mission: int, flight: dict) -> list[tuple]:
    out = []
    _, inputs = link.last("set_inputs", before=mission)
    fn_des = find((inputs or {}).get("args"), "Fn_DES")
    if inputs is None or fn_des is None:
        out.append(("mass -> pycycle", NOT_EXERCISED, "no set_inputs with Fn_DES before the mission"))
    else:
        out.append(("mass -> pycycle", FAIL if close(fn_des, DEFAULT_FN_DES, 1e-6) else PASS,
                    f"Fn_DES {fn_des} (the tool default is {DEFAULT_FN_DES:.0f})"))
    engine = (flight.get("result") or {}).get("engine") or {}
    deck = engine.get("deck")
    if not engine.get("external_deck") or not deck:
        out.append(("pycycle -> aviary", FAIL, f"the mission did not fly a designed engine: {engine}"))
        return out
    flown_scale = link.flown_parameters(mission).get("Aircraft.Engine.SCALE_FACTOR")
    problems = [] if close(engine.get("scale_factor"), flown_scale, 1e-4) else \
        [f"scale factor flown {engine.get('scale_factor')}, set {flown_scale}"]
    deck_json = Path(deck).with_suffix(".json")
    detail = f"deck {Path(deck).name}"
    if deck_json.is_file():
        built = json.loads(deck_json.read_text())
        design = built.get("design_inputs") or {}
        mach, alt = find(design.get("fc.MN"), "value"), find(design.get("fc.alt"), "value")
        if not close(mach, MISSION_MACH, 1e-6) or not close(alt, MISSION_ALT_FT, 1e-6):
            problems.append(f"deck designed at M{mach} / {alt} ft, the mission is M{MISSION_MACH} / {MISSION_ALT_FT:.0f} ft")
        sfc = (link.design.get("propulsion") or {}).get("sfc_cruise")
        tsfc = (built.get("design") or {}).get("TSFC")
        if sfc is not None and not close(tsfc, sfc, 0.005):
            problems.append(f"deck TSFC {tsfc}, cycle SFC {sfc}")
        detail += f", M{mach} / {alt} ft, TSFC {tsfc}"
    else:
        detail += " (deck file not on this machine, so its design point was not read)"
    out.append(("pycycle -> aviary", FAIL, "; ".join(problems)) if problems else ("pycycle -> aviary", PASS, detail))
    return out


def check_chain(link: Link, previous: Link) -> list[tuple]:
    out = []
    want = previous.result.get("chain_cpacs_out")
    opened = link.rows("open_cpacs")
    source = opened[0][1]["args"].get("source") if opened else None
    if want is None:
        out.append(("link -> link geometry file", NOT_EXERCISED, "the previous link exported no geometry"))
    else:
        out.append(("link -> link geometry file", PASS if source == want else FAIL, f"opened {source}, previous ended on {want}"))
    if previous.calls:
        end_mission, _ = previous.final_mission()
        end, _ = previous.wing_at(end_mission if end_mission is not None else len(previous.calls))
        start = link.starting_wing()
        if end and start:
            same = all(close(start[k], end[k], 0.002) for k in ("area", "aspect_ratio", "sweep") if end.get(k) is not None)
            out.append(("link -> link wing", PASS if same else FAIL, f"start {start}, previous end {end}"))
        else:
            out.append(("link -> link wing", NOT_EXERCISED, "the wing was not measured in one of the links"))
    # B265: the aviary inputs this link started from are the ones the previous link last FLEW.
    started = link.carry_over.get("aviary_session_start") or {}
    if previous.calls and started:
        end_mission, _ = previous.final_mission()
        flown = previous.flown_parameters(end_mission) if end_mission is not None else {}
        shared = {k: v for k, v in flown.items() if k in started and number(v) is not None}
        wrong = {k: (started[k], v) for k, v in shared.items() if not close(number(started[k]), number(v), 1e-6)}
        if not shared:
            out.append(("link -> link aviary inputs", NOT_EXERCISED, "the previous link flew no design variable"))
        else:
            out.append(("link -> link aviary inputs", FAIL if wrong else PASS,
                        f"start vs previous flown {wrong}" if wrong else f"{len(shared)} inputs as flown"))
    carried = (previous.design.get("design_space") or {}).get("engine") or {}
    if not carried:
        out.append(("link -> link engine", NOT_EXERCISED, "the previous link set no engine inputs"))
    else:
        rows = link.rows("set_inputs")
        first = (rows[0][1]["args"].get("values") or {}) if rows else {}
        wrong = {k: (first.get(k), v) for k, v in carried.items() if not close(first.get(k), v, 1e-6)}
        out.append(("link -> link engine", FAIL, f"first set_inputs vs previous end {wrong}") if wrong
                   else ("link -> link engine", PASS, f"{carried}"))
    return out


def normalised(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\\n", " ")).strip()


def check_recommendation(link: Link, previous: Link) -> tuple:
    signals = [s for s in previous.design.get("coordination_signals") or [] if s.get("kind") == "recommendation"]
    if not signals:
        return "recommendation -> next link", NOT_EXERCISED, "the previous link recorded no recommendation"
    last = signals[-1]
    if last.get("verdict") == "COMPLETE":
        return "recommendation -> next link", NOT_EXERCISED, "the previous verdict was COMPLETE: nothing to deliver"
    change = normalised(str(last.get("recommended_change") or ""))
    addressed = str(last.get("recommendation_to") or "")
    shown = [agent for agent, text in link.prompts().items() if change and change in normalised(text)]
    org = link.result.get("org_structure")
    if not shown:
        return "recommendation -> next link", FAIL, f"to {addressed}: {change!r} is in no prompt of this link"
    info = f"to {addressed}: seen by {shown}"
    if org == "orchestrated":
        # Delivery ends at the orchestrator; what it passes on is its own decision.
        if "orchestrator" not in shown and addressed not in shown:
            return "recommendation -> next link", FAIL, info
        return "recommendation -> next link", PASS, info + f" (addressed role saw it: {addressed in shown})"
    if org == "networked" or link.result.get("handler") == "graph_routed":
        # Who runs is the network's or the graph router's decision (coordination); delivery is seen by any agent.
        return "recommendation -> next link", PASS, info
    return ("recommendation -> next link", PASS if addressed in shown else FAIL, info)


def check_link(link: Link, previous: Link | None) -> list[tuple]:
    if link.calls is None:
        return [("tool call log", FAIL, "no tool_calls_attemptN.jsonl: run with MAS_STEP_LOG=1")]
    checks: list[tuple] = []
    mission, flight = link.final_mission()
    if flight is None:
        checks.append(("mission", NOT_EXERCISED, "this link flew no mission, so its couplings were not tested"))
    else:
        checks += check_geometry_to_su2(link, mission)
        checks.append(check_geometry_to_mission(link, mission))
        checks.append(check_su2_to_mission(link, mission))
        checks += check_mass(link, mission)
        checks += check_propulsion(link, mission, flight)
        if flops_contract(link):
            # V2: the contract since B324 / G1 / J2 / A8 / B340 (MAS-Aviary integration/final-rerun-2026-11-18).
            checks += check_cruise_point_engine(link, mission, flight)
            checks.append(check_mass_basis(link, mission))
            checks.append(check_tails_tc(link, mission))
        checks.append(check_fuselage(link, mission))
    if previous is not None and previous.calls is not None:
        checks += check_chain(link, previous)
        checks.append(check_recommendation(link, previous))
    return checks


def check_sweep(root: Path) -> dict[str, dict[int, list[tuple]]]:
    combos: dict[str, dict[int, Link]] = {}
    for result in sorted(root.glob("repeat_*/*/result.json")):
        link = Link(result.parent)
        number = int(link.result.get("chain_link", link.result.get("repeat_index", 0)))
        combos.setdefault(result.parent.name, {})[number] = link
    report: dict[str, dict[int, list[tuple]]] = {}
    for combo, links in combos.items():
        report[combo] = {k: check_link(links[k], links.get(k - 1)) for k in sorted(links)}
    return report


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    roots = [Path(a) for a in argv[1:] if not a.startswith("--")]
    if not roots:
        print(__doc__)
        return 2
    failed = 0
    everything = {}
    for root in roots:
        for combo, links in check_sweep(root).items():
            everything[combo] = {k: [list(c) for c in v] for k, v in links.items()}
            for number, checks in links.items():
                failed += sum(1 for c in checks if c[1] == FAIL)
                if not as_json:
                    print(f"\n{combo}  link {number + 1}")
                    for name, status, detail in checks:
                        print(f"  {status:13s} {name:28s} {detail}")
    print(json.dumps(everything, indent=1, default=str) if as_json else f"\n{failed} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
