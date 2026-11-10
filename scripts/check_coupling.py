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
    return not (isinstance(result, dict) and (result.get("success") is False or result.get("error_code")))


class Link:
    """One finished link: its ordered tool calls and its result files."""

    def __init__(self, directory: Path):
        self.dir = directory
        self.result = json.loads((directory / "result.json").read_text())
        design = directory / "design.json"
        self.design = json.loads(design.read_text()) if design.is_file() else {}
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
            seen[agent] = " ".join(json.dumps(s.get("model_input_messages", ""), default=str) + str(s.get("task", ""))
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


def check_su2_to_mission(link: Link, mission: int) -> tuple:
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
    if org == "networked":
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
