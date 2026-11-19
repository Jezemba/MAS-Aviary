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
                  (re.compile(r"MASS_SCALER|Wing\.MASS\b", re.I), MASS),   # before the Aircraft.Wing.* geometry rule
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
# aviary-mcp's READ_ONLY_PARAMETER text for a derived parameter: "'Aircraft.Wing.SPAN' is derived (...)".
_DERIVED = re.compile(r"\bis derived\b", re.I)


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


def _out(record: dict) -> dict:
    """B363 (V1): the record's decision digest -- the uncapped flags, metrics, error code and text of the FULL tool
    result -- never its capped ``outputs`` (run_simulation's grown reply lost its ``summary`` there, so no mission was
    ever seen as flown)."""
    from src.tools.knowledge_base import kb_result

    return kb_result(record)


def _ok(record: dict) -> bool:
    out = _out(record)
    return (record.get("status") == "success" and out.get("success") is not False
            and out.get("valid") is not False and not out.get("error_code"))


def _codes(records: list) -> list[tuple[str, dict]]:
    return [(_out(r).get("error_code"), r) for r in records if _out(r).get("error_code")]


def _result_exists(record: dict) -> bool:
    """An ALREADY_DONE refusal that points at a finished result (not "another call is still running", B363)."""
    out = _out(record)
    if out.get("error_code") != "ALREADY_DONE":
        return False
    if "result_exists" in out:
        return out["result_exists"] is True
    # A refusal recorded before B363: one that names the prior result's kb seq points at a finished result; the
    # "already running" refusal has kb_seq None.
    return isinstance(out.get("kb_seq"), int) and not isinstance(out.get("kb_seq"), bool)


def _flown(record: dict) -> float | None:
    out = _out(record)
    # B306 (audit F3): a mission that did not converge (aviary-mcp B288: success false, MISSION_NOT_CONVERGED) or
    # was otherwise not a result is not flown, whatever fuel figure it carries.
    if (record.get("status") not in (None, "success") or out.get("success") is False or out.get("converged") is False
            or out.get("valid") is False or out.get("valid_result") is False):
        return None
    try:
        fuel = float(out.get("fuel_burned_kg"))     # the digest takes it from the reply's summary (B363)
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
            missing = _out(rec).get("missing") or []
            producers = [PRODUCER_NODE.get(str(m.get("producer"))) for m in missing if isinstance(m, dict)]
            producers = [p for p in producers if p]
            if producers:
                node = min(producers, key=NODES.index)
                names = ", ".join(str(m.get("variable")) for m in missing if isinstance(m, dict))
                return Decision(node, LEVEL_OF_NODE[node], "5",
                                f"{rec.get('tool')} is missing {names}, produced by {node}")
        if code == "READ_ONLY_PARAMETER":
            text = f"{_out(rec).get('error', '')} {rec.get('note', '')}"
            if _DERIVED.search(text):
                # B363 (V9): aviary refuses a DERIVED parameter (SPAN = sqrt(AR x AREA), SCALED_SLS_THRUST from the
                # scale): no discipline produces it, the caller only has to leave it out. Routing it to geometry
                # (rule 5) raised N_GR to COMPLEX and re-ran the geometry for a key the agent had copied back from a
                # reply. Not upstream: rule 1 (the same node again, with the refusal as feedback).
                continue
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
    target, fn = registry.get("aero.target_cl"), registry.get("prop.fn_lbf")
    if cl is None or cd is None or (target and abs(float(cl) - float(target)) > 0.1 * float(target)):
        return Decision(AERO, higher(level, MODERATE), "6",
                        f"the mission did not fly and the aero is not trimmed to the cruise lift (CL {cl}, target {target})")
    ld = registry.get("aviary.cruise_l_over_d")
    not_evaluated = ""
    # B340 (review #10): rule 4 uses what Fn_DES uses -- the trim mass basis (carried sized mass, not this link's
    # mass.mtom_kg) and aviary's cruise L/D -- and the scale bound uses the reference of the deck attached to the
    # aviary session (B279), not the bench 5,936 lbf. With a different mass a re-sized engine gave the same Fn_DES and
    # the rule routed to PROPULSION until the budget ran out. Only a CURRENT engine is checked (a stale one is refused
    # upstream by the mission contract).
    from src.tools import data_plane as _dp

    mass, mass_basis = _dp.trim_mass_basis()
    current = _dp.engine_is_current()
    if mass is not None and fn and current:
        reference, ref_basis = _dp.engine_scale_reference()
        scale = coupling.fn_to_aviary_engine_scale(float(fn), reference)
        if scale > ENGINE_SCALE_BOUND:
            return Decision(AERO, higher(level, MODERATE), "6",
                            f"the mission did not fly and the engine is already at scale {scale:.2f} ({ref_basis}): "
                            "the drag is suspect")
        # Drag model decision 2026-11-18: the thrust the drag needs is from aviary's cruise L/D, not SU2 CL/CD.
        # B335: a current engine was sized on a recorded cruise point, so a thrust without a usable L/D is an
        # inconsistent record, not a case to skip: the engine is re-sized.
        try:
            needed = coupling.fn_des_from_lift_to_drag(float(mass), float(ld)) if ld is not None else None
            why = None if needed is not None else "aviary's cruise lift-to-drag is not recorded"
        except (TypeError, ValueError) as exc:
            needed, why = None, f"aviary's cruise lift-to-drag {ld!r} is not usable ({exc})"
        if needed is None:
            return Decision(PROPULSION, higher(level, MODERATE), "4",
                            f"the mission did not fly and the engine thrust {float(fn):.0f} lbf cannot be checked "
                            f"against the drag: {why}, although the engine was sized on it. Size the engine again "
                            "(Fn_DES from aviary's cruise point of the current design)")
        if float(fn) < 0.95 * needed:
            return Decision(PROPULSION, higher(level, MODERATE), "4",
                            f"the mission did not fly: thrust {float(fn):.0f} lbf < {needed:.0f} lbf needed for this drag "
                            f"at {float(mass):.0f} kg ({mass_basis})")
    else:
        # B335: recorded, not skipped silently.
        missing = [n for n, ok in ((f"no trim mass basis ({mass_basis})", mass is not None),
                                   ("no engine thrust (prop.fn_lbf)", bool(fn)),
                                   ("the engine is not current for this design", current)) if not ok]
        not_evaluated = f" (thrust-vs-drag rule 4 not evaluated: {', '.join(missing)})"
    worst = ((failure or {}).get("violated_constraints") or [{}])[0] if failure else {}
    if "mass_residual" in str((worst or {}).get("constraint", "")):
        return Decision(MASS, higher(level, MODERATE), "7",
                        "the mission did not fly and the mass balance is the worst residual (weight snowball)"
                        + not_evaluated)
    return Decision(MISSION, level, "9", "the mission did not fly and nothing upstream is implicated: set it up again"
                    + not_evaluated)


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
        elif key and _result_exists(key[-1]):
            # B363 (V11): the duplicate guard refused the call because this design's result already exists (it
            # refuses only when the coupling contract counts that result as current, B363 V5): the node's job is done.
            decision = Decision(NODES[NODES.index(node) + 1], level, "flow",
                                f"{KEY_TOOL[node]} was refused ALREADY_DONE: this design's result exists "
                                f"(kb seq {_out(key[-1]).get('kb_seq')})")
        elif upstream:
            decision = Decision(upstream.next_node, higher(level, upstream.level), upstream.rule, upstream.reason)
        else:
            what = (key[-1].get("note") or _out(key[-1]).get("error") or "it failed") if key else \
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


# The rule name of a REVIEW that ends the link because nothing it said could be routed (C2); "complete" is kept for
# the integrator's COMPLETE verdict only.
NO_RECOMMENDATION_RULE = "no-recommendation"


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
        # C2 (l20_final Case 6.4): this ends the link as COMPLETE does, but it is not the integrator's COMPLETE
        # verdict, so it has its own rule name and the handler its own stop reason. Routing is unchanged.
        said = (recorded or {}).get("verdict") if recorded else None
        if said is None:
            found = re.search(r"VERDICT\W*[:=]\s*\W*(COMPLETE|CONTINUE|RETRY)", output or "", re.I)
            said = found.group(1).upper() if found else None
        how = "recorded" if recorded else "in its text"
        return Decision(COMPLETE, level, NO_RECOMMENDATION_RULE,
                        f"the integrator gave no recommendation to act on (verdict {how}: {said or 'none'})")
    asked = requested_level or complexity_request(output)
    new_level = higher(level, LEVEL_OF_NODE[target])
    if asked:
        new_level = higher(new_level, asked)
    rule = {MISSION: "10", PROPULSION: "11", MASS: "11", AERO: "12", GEOMETRY: "13"}.get(target, "10")
    return Decision(target, new_level, rule,
                    f"the integrator recommends: {str(rec.get('RECOMMENDED_CHANGE', ''))[:200]} -- produced by {target}")
