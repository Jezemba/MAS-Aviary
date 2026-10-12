"""Graph-routed v2: the routing rules (Jessica, 2026-10-12; .claude/planning/graph_routed_v2/).

Pure decisions, no I/O: given what one node visit did (its knowledge-base tool records, the agent's
DESIGN_STATE, the coupling registry), say which node runs next, at which complexity level, and why.
The rules read STRUCTURED results only -- error codes, refusal payloads, the aviary failure block, the
coupling registry -- never free text, except the integrator's recommendation, which is text by nature.

Nodes run in the sequential order. Entering a node re-runs every node downstream of it (a Gauss-Seidel
sweep), so a geometry change cannot leave stale aero or mass behind.

Levels decide which nodes may be (re)run: SIMPLE = mission, simulation, review on the carried results;
MODERATE adds aero, mass and propulsion; COMPLEX adds geometry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

GEOMETRY, AERO, MASS, PROPULSION, MISSION, SIMULATION, REVIEW, COMPLETE = (
    "geometry_engineer", "aerodynamics_analyst", "structures_analyst", "propulsion_analyst",
    "mission_architect", "simulation_executor", "mdo_integrator", "COMPLETE")
NODES = [GEOMETRY, AERO, MASS, PROPULSION, MISSION, SIMULATION, REVIEW]
SIMPLE, MODERATE, COMPLEX = "simple", "moderate", "complex"
LEVELS = [SIMPLE, MODERATE, COMPLEX]
LEVEL_OF_NODE = {GEOMETRY: COMPLEX, AERO: MODERATE, MASS: MODERATE, PROPULSION: MODERATE,
                 MISSION: SIMPLE, SIMULATION: SIMPLE, REVIEW: SIMPLE}
ENTRY_OF_LEVEL = {SIMPLE: MISSION, MODERATE: AERO, COMPLEX: GEOMETRY}
# Node executions allowed per link at each level (today's resource budgets, kept -- D6).
BUDGET = {SIMPLE: 8, MODERATE: 15, COMPLEX: 25}
RETRIES_PER_VISIT = 3                     # rule 1 cap (D6)
ENGINE_SCALE_BOUND = 2.0                  # rule 4 -> rule 6: past this the drag, not the engine, is suspect

# The tool whose success means the node did its job.
KEY_TOOL = {GEOMETRY: "generate_volume_mesh", AERO: "run_su2_solver", MASS: "estimate_mass",
            PROPULSION: "run_cycle", MISSION: "set_aircraft_parameters", SIMULATION: "run_simulation"}
PRODUCER_NODE = {"tigl": GEOMETRY, "su2": AERO, "mass": MASS, "pycycle": PROPULSION, "aviary": MISSION}
# Error codes whose fix is the geometry (rules 2 and 8).
GEOMETRY_CODES = {"NO_MESH", "MORPH_NOT_EXPORTED", "STALE_GEOMETRY", "GEOMETRY_NOT_APPLIED", "AERO_NONPHYSICAL"}
# Aviary parameters the framework sets, by the discipline that produces them (rule 5, READ_ONLY_PARAMETER).
PARAM_PRODUCER = [(re.compile(r"LIFT_COEFFICIENT|DRAG_COEFF", re.I), AERO),
                  (re.compile(r"MASS_SCALER", re.I), MASS),
                  (re.compile(r"Engine\.SCALE_FACTOR|SCALED_SLS_THRUST", re.I), PROPULSION),
                  (re.compile(r"Aircraft\.(Wing|Fuselage|HorizontalTail|VerticalTail)\.", re.I), GEOMETRY)]
# Words in a recommended change, by the discipline that produces the variable (rules 10-13). Geometry first:
# "increase aspect ratio to cut induced drag" is a geometry change, whatever role it was addressed to.
RECOMMENDATION_WORDS = [
    (GEOMETRY, r"aspect[ _-]?ratio|wing[ _-]?area|\bspan\b|sweep|taper|planform|fuselage|chord|dihedral|"
               r"Wing1|set_high_level_parameters|\bmorph|geometry|airfoil|aerofoil|thickness|leading[ _-]?edge|"
               r"\bcamber|\btwist|incidence"),
    (PROPULSION, r"\bBPR\b|bypass|fan[ ._]?PR|\bLPC\b|\bHPC\b|\bT4|Fn_DES|thrust|\bengine\b|\bTSFC\b|\bSFC\b|cycle|pycycle"),
    (MASS, r"\bmass\b|material|structur|\bOEM\b|\bMTOM\b|wing[ _]?weight|composite|aluminium|aluminum"),
    (AERO, r"\bmesh\b|\btrim|\bCFD\b|\bSU2\b|\bAOA\b|angle of attack|\bCL\b|\bCD\b|lift coefficient"),
    (MISSION, r"\bmission\b|cruise[ _]?(mach|altitude)|\brange\b|altitude|reserve"),
]
_ROLE_NODE = {"geometry": GEOMETRY, "aero": AERO, "struct": MASS, "mass": MASS, "propul": PROPULSION,
              "mission": MISSION, "simul": SIMULATION}
_COMPLEXITY_FIELD = re.compile(r"""["']?\bCOMPLEXITY\b["']?\s*[:=]\s*["']?\s*\**\s*(simple|moderate|complex)\b""", re.I)
_ZERO_FUEL_FLOOR_KG = 100.0


@dataclass
class Decision:
    next_node: str
    level: str
    rule: str
    reason: str


def higher(a: str, b: str) -> str:
    return a if LEVELS.index(a) >= LEVELS.index(b) else b


def downstream_of(node: str) -> list[str]:
    return NODES[NODES.index(node):]


def complexity_request(text: str | None) -> str | None:
    """An agent's explicit `COMPLEXITY: <level>` field (that field only -- never a stray word)."""
    m = _COMPLEXITY_FIELD.search(text or "")
    return m.group(1).lower() if m else None


def _records_for(records: list, tool: str) -> list:
    return [r for r in records if r.get("tool") == tool]


def _ok(record: dict) -> bool:
    out = record.get("outputs") or {}
    return (record.get("status") == "success" and out.get("success") is not False
            and out.get("valid") is not False and not out.get("error_code"))


def _codes(records: list) -> list[tuple[str, dict]]:
    return [((r.get("outputs") or {}).get("error_code"), r) for r in records if (r.get("outputs") or {}).get("error_code")]


def _flown(record: dict) -> float | None:
    out = record.get("outputs") or {}
    summary = out.get("summary") if isinstance(out.get("summary"), dict) else out
    try:
        fuel = float(summary.get("fuel_burned_kg"))
    except (TypeError, ValueError):
        return None
    return fuel if fuel > _ZERO_FUEL_FLOOR_KG else None


def _upstream_from_errors(records: list) -> Decision | None:
    """Rules 2, 5, 8: an error code that names the discipline at fault."""
    for code, rec in _codes(records):
        if code in GEOMETRY_CODES:
            rule = "8" if code == "GEOMETRY_NOT_APPLIED" else "2"
            return Decision(GEOMETRY, COMPLEX, rule, f"{rec.get('tool')} returned {code}: the geometry has to be redone")
        if code == "MISSING_REQUIRED_PARAMETERS":
            missing = (rec.get("outputs") or {}).get("missing") or []
            producers = [PRODUCER_NODE.get(str(m.get("producer"))) for m in missing if isinstance(m, dict)]
            producers = [p for p in producers if p]
            if producers:
                node = min(producers, key=NODES.index)
                names = ", ".join(str(m.get("variable")) for m in missing if isinstance(m, dict))
                return Decision(node, LEVEL_OF_NODE[node], "5",
                                f"{rec.get('tool')} is missing {names}, produced by {node}")
        if code == "READ_ONLY_PARAMETER":
            text = f"{(rec.get('outputs') or {}).get('error', '')} {rec.get('note', '')}"
            for pattern, node in PARAM_PRODUCER:
                if pattern.search(text):
                    return Decision(node, LEVEL_OF_NODE[node], "5",
                                    f"{rec.get('tool')} tried to set a value {node} produces: {text.strip()[:160]}")
    return None


def route_after_simulation(records: list, registry: dict, failure: dict | None, level: str) -> Decision:
    """SIMULATION: flown -> REVIEW; otherwise rules 4, 6, 7, 9 (and 2/5/8 from error codes)."""
    sims = _records_for(records, "run_simulation")
    if any(_flown(r) is not None for r in sims):
        return Decision(REVIEW, level, "flow", "the mission flew")
    upstream = _upstream_from_errors(records)
    if upstream:
        return Decision(upstream.next_node, higher(level, upstream.level), upstream.rule, upstream.reason)
    if not sims:
        return Decision(SIMULATION, level, "1", "run_simulation was not called")
    from src.tools import coupling

    cl, cd = registry.get("aero.cl_cruise"), registry.get("aero.cd_cruise")
    target, mtom, fn = registry.get("aero.target_cl"), registry.get("mass.mtom_kg"), registry.get("prop.fn_lbf")
    if cl is None or cd is None or (target and abs(float(cl) - float(target)) > 0.1 * float(target)):
        return Decision(AERO, higher(level, MODERATE), "6",
                        f"the mission did not fly and the aero is not trimmed to the cruise lift (CL {cl}, target {target})")
    if mtom and fn:
        needed = coupling.fn_des_from_drag(float(mtom), float(cl), coupling.total_cruise_cd(float(cd)))
        scale = coupling.fn_to_aviary_engine_scale(float(fn))
        if scale > ENGINE_SCALE_BOUND:
            return Decision(AERO, higher(level, MODERATE), "6",
                            f"the mission did not fly and the engine is already at scale {scale:.2f}: the drag is suspect")
        if float(fn) < 0.95 * needed:
            return Decision(PROPULSION, higher(level, MODERATE), "4",
                            f"the mission did not fly: thrust {float(fn):.0f} lbf < {needed:.0f} lbf needed for this drag")
    worst = ((failure or {}).get("violated_constraints") or [{}])[0] if failure else {}
    if "mass_residual" in str((worst or {}).get("constraint", "")):
        return Decision(MASS, higher(level, MODERATE), "7",
                        "the mission did not fly and the mass balance is the worst residual (weight snowball)")
    return Decision(MISSION, level, "9", "the mission did not fly and nothing upstream is implicated: set it up again")


def route_after_node(node: str, records: list, output: str, level: str, retries: int,
                     registry: dict | None = None, failure: dict | None = None,
                     requested_level: str | None = None) -> Decision:
    """The next node after one visit of `node` (not REVIEW, see route_after_review)."""
    registry = registry or {}
    if node == SIMULATION:
        decision = route_after_simulation(records, registry, failure, level)
    else:
        key = _records_for(records, KEY_TOOL[node])
        upstream = _upstream_from_errors(records)
        if key and _ok(key[-1]):
            decision = Decision(NODES[NODES.index(node) + 1], level, "flow", f"{KEY_TOOL[node]} succeeded")
        elif upstream:
            decision = Decision(upstream.next_node, higher(level, upstream.level), upstream.rule, upstream.reason)
        else:
            what = (key[-1].get("note") or (key[-1].get("outputs") or {}).get("error") or "it failed") if key else \
                f"{KEY_TOOL[node]} was not called"
            decision = Decision(node, level, "1", f"{KEY_TOOL[node]} did not succeed ({str(what)[:200]})")
    if decision.next_node == node and retries + 1 >= RETRIES_PER_VISIT:
        # Rule 1's cap, for every node: after RETRIES_PER_VISIT attempts the sweep moves on.
        decision = Decision(NODES[NODES.index(node) + 1], decision.level, "1-cap",
                            f"{KEY_TOOL[node]} did not succeed after {RETRIES_PER_VISIT} attempts; moving on")
    # B180: a request_complexity tool call made during this visit; the text field only as the fallback.
    asked = requested_level or complexity_request(output)
    if asked and LEVELS.index(asked) > LEVELS.index(decision.level):
        entry = ENTRY_OF_LEVEL[asked]
        # Escalating to a level whose entry is upstream of here: go back there and sweep forward.
        if NODES.index(entry) <= NODES.index(node):
            return Decision(entry, asked, "escalate", f"{node} reclassified the task as {asked}")
        return Decision(decision.next_node, asked, decision.rule, decision.reason + f"; {node} reclassified as {asked}")
    return decision


def recommendation_target(rec: dict) -> str | None:
    """The node that PRODUCES the variable the recommendation changes; the named role only as a fallback."""
    change = str(rec.get("RECOMMENDED_CHANGE") or "")
    for node, pattern in RECOMMENDATION_WORDS:
        if re.search(pattern, change, re.I):
            return node
    to = str(rec.get("RECOMMENDATION_TO") or "").lower()
    return next((n for key, n in _ROLE_NODE.items() if key in to), None)


def route_after_review(output: str, level: str, recorded: dict | None = None,
                       requested_level: str | None = None) -> Decision:
    """REVIEW: COMPLETE when the integrator is satisfied or gave nothing to act on; else rules 10-13."""
    from src.coordination.completion_signal import signals_completion
    from src.coordination.prompt_assembly import parse_recommendation

    # B180: what the integrator recorded with record_recommendation during this visit wins; its text is the
    # fallback for an integrator that never called the tool.
    if recorded:
        if recorded.get("verdict") == "COMPLETE":
            return Decision(COMPLETE, level, "complete", "the integrator recorded the verdict COMPLETE")
        rec = {"RECOMMENDATION_TO": recorded.get("recommendation_to"),
               "RECOMMENDED_CHANGE": recorded.get("recommended_change")}
    else:
        verdict = re.search(r"VERDICT\W*[:=]\s*\W*(COMPLETE|CONTINUE|RETRY)", output or "", re.I)
        if (verdict and verdict.group(1).upper() == "COMPLETE") or signals_completion(output or "", "TASK_COMPLETE"):
            return Decision(COMPLETE, level, "complete", "the integrator's verdict is COMPLETE")
        rec = parse_recommendation(output)
    target = recommendation_target(rec) if rec else None
    if not target:
        return Decision(COMPLETE, level, "complete", "the integrator gave no recommendation to act on")
    asked = requested_level or complexity_request(output)
    new_level = higher(level, LEVEL_OF_NODE[target])
    if asked:
        new_level = higher(new_level, asked)
    rule = {MISSION: "10", PROPULSION: "11", MASS: "11", AERO: "12", GEOMETRY: "13"}.get(target, "10")
    return Decision(target, new_level, rule,
                    f"the integrator recommends: {str(rec.get('RECOMMENDED_CHANGE', ''))[:200]} -- produced by {target}")
