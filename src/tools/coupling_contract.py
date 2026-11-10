"""Coupled variables are REQUIRED INPUTS of the tool that consumes them (B84).

WHAT IT WAS DOING BEFORE

Every MCP server has a default for everything, and the framework's injectors are all
no-ops when their upstream has not run:

  * no SU2 solve  -> no drag factor injected -> aviary flies its OWN DEFAULT drag polar
  * no mass-mcp   -> no Aircraft.Wing.MASS injected -> aviary uses its OWN internal FLOPS wing mass
  * no mass-mcp   -> no Fn_DES injected      -> pycycle sizes the engine on its own default thrust

So the discipline chain (geometry -> SU2 -> aviary, geometry -> mass -> pycycle -> aviary)
existed only as a convention inside the middleware, never as a requirement. A mission ran,
returned a plausible fuel figure, and nothing anywhere said the number was not this
design's. The framework only ADVISED, on the successful result:

    AERO COUPLING MISSING: ... aviary will use its DEFAULT drag polar
    COUPLING AVAILABLE (optional): ... Your choice.

**Agents ignored the advice every time.** validate7_net link 1: 5 aero warnings, 7 mass
hints, and 0 estimate_mass, 0 run_cycle, 0 claim_todo -- the tool called right after each
of the first five hints was get_design_state / generate_volume_mesh / create_su2_session /
set_mesh / set_mesh, never the discipline the hint named. validate4 across the sweep: 43
aero warnings, 51 mass hints, same outcome, with orchestrated links recording
eval=commission on fuel computed from the default polar.

Nor is it caught at the end: nothing checks at final_answer that every discipline ran. The
only end-of-run gate is B31's volume mesh. A link with 0 estimate_mass and 0 run_cycle
finished and was scored.

THE RULE (Jessica, 2026-09-16)

One rule, everywhere: **if a tool consumes a coupled variable, that variable is a required
input of the call.** Call it without one and the call does not run; the error names the
missing parameter and the tool that produces it, exactly as a missing argument would.

  run_simulation  requires  SU2 CL/CD, mass-mcp's wing mass, and a cycle run for this design
  run_cycle       requires  mass-mcp's MTOM (it is what sizes engine thrust)

set_aircraft_parameters is deliberately NOT in that list (B85): it is how a peer applies its
design and checks valid:true, so gating it refused work unrelated to the mission and burned
the step budget. run_simulation is where the default drag polar would actually be used.

Nothing special-cases a coordination structure: it is one agent calling one tool that has
required inputs. Injection stays -- resolve_request still fills these from the typed
registry whenever the discipline HAS run for the current design (and still derives the drag
factor from SU2 CD, a formula the model kept skipping), so this fires only when the value
genuinely does not exist. A value the agent passes by hand counts: the contract is about
what reaches the solver, not about who produced it.

STALE COUNTS AS MISSING. A result produced for an earlier geometry is not this design's
result, so each capture records the design it was produced for (the B81 geometry epoch) and
a later morph makes it stale.

PROPULSION (B3, 2026-10-31). There is no aviary parameter that carries SFC into fuel burn (the FLOPS
scalers do not land, measured deltafuel = 0.0000 kg), so the designed cycle reaches the mission as its own
engine deck: pycycle's off-design table of the converged cycle, flown by aviary in place of the bench
table. run_simulation requires that deck on its session (ENGINE_DECK); propulsion_coupled is True only
when the mission reports it flew it. AVION_ENGINE_DECK=0 restores the bench engine (regression runs).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

ERROR_CODE = "MISSING_REQUIRED_PARAMETERS"


@dataclass(frozen=True)
class CoupledInput:
    """One coupled variable, and how the agent gets it."""

    var: str                  # typed-registry name written by the producing discipline
    parameter: str            # the parameter it becomes on the consuming call ("" = a run, not a value)
    label: str
    producer: str             # which discipline produced it -- also the staleness key
    produced_by: str          # the exact tool sequence
    note: str = ""


AERO_CL = CoupledInput(
    var="aero.cl_cruise", parameter="Mission.Design.LIFT_COEFFICIENT",
    label="SU2 cruise lift coefficient", producer="su2",
    produced_by="create_su2_session -> set_mesh -> run_su2_solver -> read_history_csv",
    note="Captured as aero.cl_cruise and injected for you.")
AERO_CD = CoupledInput(
    var="aero.cd_cruise", parameter="Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR",
    label="SU2 cruise drag (aviary's drag polar)", producer="su2",
    produced_by="create_su2_session -> set_mesh -> run_su2_solver -> read_history_csv",
    note="Captured as aero.cd_cruise; the drag factor is derived and injected for you -- "
         "without it aviary flies its own DEFAULT drag polar.")
WING_MASS = CoupledInput(
    var="mass.wing_kg", parameter="Aircraft.Wing.MASS",
    label="structural wing mass (mass-mcp)", producer="mass",
    produced_by="export_cpacs (the morphed geometry) -> estimate_mass",
    note="Size it on the EXPORTED MORPHED geometry, not the baseline fixture. Captured as "
         "mass.wing_kg and injected for you -- without it aviary uses its own internal FLOPS wing mass.")
MTOM = CoupledInput(
    var="mass.mtom_kg", parameter="Fn_DES",
    label="maximum take-off mass (mass-mcp)", producer="mass",
    produced_by="export_cpacs (the morphed geometry) -> estimate_mass",
    note="MTOM sizes the engine's design-point thrust. Captured as mass.mtom_kg and injected "
         "as Fn_DES for you -- without it pycycle sizes the engine on its own default thrust.")
CYCLE = CoupledInput(
    var="prop.sfc_cruise", parameter="",
    label="engine cycle run (pycycle TSFC / thrust)", producer="pycycle",
    produced_by="create_cycle_model -> set_inputs -> run_cycle",
    note="Required as a RUN: the converged cycle is what the framework turns into the mission's "
         "engine deck (B3), so its thrust and fuel flow fly the mission.")

ENGINE_DECK = CoupledInput(
    var="prop.engine_deck", parameter="",
    label="engine deck of the designed cycle (pycycle off-design table)", producer="pycycle",
    produced_by="create_cycle_model -> set_inputs -> run_cycle on a cycle that converges; the framework then "
                "makes the deck (pycycle generate_engine_deck) and puts it on the aviary session "
                "(set_engine_deck) before run_simulation",
    note="aviary flies this engine's thrust and fuel flow over the whole mission, so the cycle you design "
         "(BPR, pressure ratios, T4) is what burns the fuel (B3). A new cycle takes tens of minutes the first "
         "time; an unchanged cycle is reused.")
# The whole rule, in one table: tool -> the coupled variables that call consumes.
#
# B85 (Jessica, 2026-09-16): set_aircraft_parameters was REMOVED from this table. It is not
# only the mission seed -- it is how a peer applies its design and checks valid:true -- so
# requiring the coupled inputs there refused work that had nothing to do with the mission.
# validate8_net link 1 measured exactly that: 5 MISSING_REQUIRED_PARAMETERS, every one of
# them on set_aircraft_parameters, at 200-900 s a step, and two of three peers reached
# max_steps having produced nothing. run_simulation is the call that actually burns aviary's
# default drag polar, so that is where the requirement belongs.
REQUIRED_BY_TOOL: dict[str, tuple[CoupledInput, ...]] = {
    "run_simulation": (AERO_CL, AERO_CD, WING_MASS, CYCLE, ENGINE_DECK),
    "run_cycle": (MTOM,),
}


# -- what "the current design" means ------------------------------------------------------

def current_design_fingerprint() -> str:
    """Identity of the geometry the disciplines are supposed to be consuming.

    Built from the B81 guard state: the CPACS the design's session was opened from and that
    session's geometry epoch (bumped by every morph). The MESH is deliberately not part of it --
    a remesh is a different discretisation of the same design, and treating it as a design
    change would make a perfectly good SU2 solve look stale.

    B255: only the session the design lives in counts, whether or not it is still open. This
    used to hash every OPEN session, so close_cpacs changed the "design" with the geometry
    untouched: a networked peer's mass result was then refused as "produced for an EARLIER
    geometry" 12 times in a link that never morphed, and nothing flew.
    """
    try:
        from src.tools.duplicate_guard import _state as guard_state

        gs = guard_state()
        if gs is None:
            return "design:none"
        active = gs.get("active_design") or {}
        epochs = gs.get("geometry_epoch") or {}
        session = active.get("session")
        payload = {
            "epochs": sorted((str(k), int(v)) for k, v in
                             ({session: epochs.get(session, 0)} if session else epochs).items()),
            "cpacs": active.get("fp"),
        }
    except Exception:      # pragma: no cover - a fingerprint bug must never block work
        return "design:none"
    return "design:" + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


# B262: the tool calls whose results are coupled inputs, and the producer each one's result belongs to.
PRODUCER_TOOLS = {"estimate_mass": "mass", "run_su2_solver": "su2", "read_history_csv": "su2",
                  "run_cycle": "pycycle", "get_outputs": "pycycle"}
_call_start = __import__("threading").local()


def note_call_start(tool_name: str) -> None:
    """Remember the design a producing call STARTED on (B262), for note_capture on this thread."""
    producer = PRODUCER_TOOLS.get(tool_name)
    if producer:
        started = getattr(_call_start, "fp", None) or {}
        started[producer] = current_design_fingerprint()
        _call_start.fp = started


def note_capture(producer: str, detail: Any = None) -> None:
    """Record WHICH design a discipline's results were produced for.

    B262: that is the design when the call STARTED, not when its answer came back. Networked 2-link study,
    2026-11-09: agent_3's estimate_mass was reading the AR 9.36 file while agent_1 reshaped the wing to AR 10;
    stamped at capture, the old mass counted as the new design's, and the mission flew it.
    """
    from src.tools.data_plane import get_design_state

    state = get_design_state()
    if state is None:
        return
    started = (getattr(_call_start, "fp", None) or {}).pop(producer, None)
    state.data_store[f"_coupled_fp_{producer}"] = started or current_design_fingerprint()
    if detail is not None:
        state.data_store[f"_coupled_detail_{producer}"] = detail


def _captured_fingerprint(producer: str) -> str | None:
    from src.tools.data_plane import get_design_state

    state = get_design_state()
    if state is None:
        return None
    value = (state.data_store or {}).get(f"_coupled_fp_{producer}")
    return value if isinstance(value, str) else None


# -- the check ------------------------------------------------------------------------------

def _values_of(resolved: dict) -> dict:
    """The parameter dict of the call, whichever name this tool uses for it."""
    out: dict = {}
    for key in ("parameters", "values"):
        params = (resolved or {}).get(key)
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = None
        if isinstance(params, dict):
            out.update(params)
    return out


def status(required: CoupledInput, resolved: dict | None = None) -> str:
    """``ok`` | ``missing`` | ``stale`` for one coupled input against the current design."""
    from src.tools import coupling
    from src.tools.data_plane import get_design_state

    # Passed by hand on this very call: the contract is about what reaches the solver.
    if required.parameter and resolved is not None and required.parameter in _values_of(resolved):
        return "ok"
    if required is ENGINE_DECK:
        # B3: the deck exists only for an engine sized on this design (CYCLE says so when it is not);
        # AVION_ENGINE_DECK=0 is the explicit bench-engine switch for regression runs.
        from src.tools.data_plane import engine_deck_current, engine_deck_enabled, engine_is_current

        if not engine_deck_enabled() or not engine_is_current():
            return "ok"
        sid = (resolved or {}).get("session_id")
        return "ok" if engine_deck_current(sid) else "deck_missing"

    if coupling.get_var(get_design_state(), required.var) is None:
        return "missing"
    if required is CYCLE:
        # B187: the cycle must be sized on this design's mass and drag, not run on pycycle's defaults.
        from src.tools.data_plane import engine_is_current

        if not engine_is_current():
            return "engine_stale"
    captured = _captured_fingerprint(required.producer)
    if captured is None:
        return "ok"          # produced before fingerprints were recorded; not evidence of staleness
    return "ok" if captured == current_design_fingerprint() else "stale"


def check(tool_name: str, resolved: dict) -> dict | None:
    """The refusal payload for a call whose coupled inputs do not exist yet, else None."""
    import os

    if tool_name == "run_su2_solver" and os.environ.get("AVION_REQUIRE_COUPLED_INPUTS", "1") == "1":
        return _su2_reference_refusal(resolved)
    required = REQUIRED_BY_TOOL.get(tool_name)
    if not required:
        return None
    if os.environ.get("AVION_REQUIRE_COUPLED_INPUTS", "1") != "1":
        return None

    problems = [(r, s) for r, s in ((r, status(r, resolved)) for r in required) if s != "ok"]
    if not problems:
        return None
    # One count per refusal per producer -- two missing SU2 parameters are one missing solve,
    # not two refusals, and the metric has to read as "how often did this tool refuse".
    for producer, state in sorted({(r.producer, s) for r, s in problems}):
        _bump(tool_name, producer, state)
    return _refusal(tool_name, problems)


# -- SU2 references follow the wing (B253) -------------------------------------------------

STALE_SU2_REFERENCES = "STALE_SU2_REFERENCES"
_SU2_REFERENCE_TOL = 0.005  # CPACS-declared 122.4 against the measured 122.83 m^2 is 0.35 %: the same wing


def _as_dict(result: Any) -> dict:
    if isinstance(result, dict):
        return result
    try:
        parsed = json.loads(result)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def note_su2_references(tool_name: str, resolved: dict, result: Any) -> None:
    """Remember the reference area and aspect ratio each SU2 session was actually configured with."""
    if tool_name not in ("create_su2_session", "configure_from_cpacs"):
        return
    from src.tools.data_plane import get_design_state

    state = get_design_state()
    if state is None:
        return
    data = _as_dict(result)
    session = data.get("session_id") or resolved.get("session_id")
    area = (data.get("references") or {}).get("REF_AREA")
    if not session or area is None:
        return
    state.data_store.setdefault("su2_session_references", {})[session] = {
        "ref_area": area, "aspect_ratio": (data.get("drag_floor") or {}).get("aspect_ratio")}


def _su2_reference_refusal(resolved: dict) -> dict | None:
    """Refuse a solve whose session was configured for a different wing than the design has now.

    networked_graph_routed, 2026-11-09: a new mesh of the 132.54 m^2 wing went by set_mesh into a session
    configured for the 125.86 m^2 wing, and SU2 normalised by the old area without a word. A mesh does not
    carry the references, so a session has to be configured again when the wing changes.
    """
    from src.tools.data_plane import get_design_state

    state = get_design_state()
    if state is None:
        return None
    configured = (state.data_store.get("su2_session_references") or {}).get(resolved.get("session_id"))
    wing = state.data_store.get("geometry_wing") or {}
    if not configured:
        return None
    wrong = []
    for label, have, want in (("reference area", configured.get("ref_area"), wing.get("Aircraft.Wing.AREA")),
                              ("aspect ratio", configured.get("aspect_ratio"), wing.get("Aircraft.Wing.ASPECT_RATIO"))):
        if isinstance(have, (int, float)) and isinstance(want, (int, float)) and want > 0 \
                and abs(have / want - 1.0) > _SU2_REFERENCE_TOL:
            wrong.append(f"{label} {have:.4g} (the wing now has {want:.4g})")
    if not wrong:
        return None
    _bump("run_su2_solver", "geometry", "stale")
    return {
        "success": False,
        "error_code": STALE_SU2_REFERENCES,
        "error": ("run_su2_solver did not run: this SU2 session was configured for a different wing: "
                  + "; ".join(wrong) + ". CL and CD would be normalised by the old values. Call "
                  "configure_from_cpacs on this session, or create a new session, then run_su2_solver."),
    }


def _refusal(tool_name: str, problems: list[tuple[CoupledInput, str]]) -> dict:
    lines = []
    for required, state in problems:
        name = required.parameter or "(a completed run)"
        if state == "engine_stale":
            head = (f"{name} -- {required.label}: the engine result on record was not sized on this design's "
                    "current mass and drag. Run run_cycle again")
        elif state == "deck_missing":
            from src.tools.data_plane import get_design_state

            why = ((get_design_state().data_store if get_design_state() is not None else {})
                   .get("engine_deck_error") or "not produced")
            head = (f"{name} -- {required.label}: the designed engine is not on this aviary session "
                    f"({why})")
        elif state == "stale":
            head = (f"{name} -- {required.label}: the value on record was produced for an EARLIER "
                    "geometry. The design has changed since, so it is not this design's value")
        else:
            head = f"{name} -- {required.label}: not produced for this design yet"
        lines.append(f"- {head}.\n  Produce it: {required.produced_by}.\n  {required.note}")

    mesh = _mesh_ref()
    tail = f"\nThe volume mesh for this design is ready to use: {mesh}." if mesh else ""
    # B85: an agent cannot see its own step budget draining. After a few refusals, say so and
    # point at the written procedure (Jessica, 2026-09-16).
    from src.tools.agent_context import current_agent_name
    from src.tools.procedures import budget_warning

    tail += budget_warning(refusals_so_far(current_agent_name()) + 1)
    return {
        "success": False,
        "error_code": ERROR_CODE,
        "error": (
            f"{tool_name} is missing required coupled inputs, so it did not run. Without them the "
            "solver substitutes its own default and the answer would not be this design's answer.\n"
            + "\n".join(lines)
            + tail
            + f"\nRun what is listed above, then call {tool_name} again -- the values are captured "
              "and injected automatically."
        ),
        "missing": [{"parameter": r.parameter, "variable": r.var, "producer": r.producer,
                     "state": s, "produced_by": r.produced_by} for r, s in problems],
        "design_fingerprint": current_design_fingerprint(),
    }


def _mesh_ref() -> str | None:
    from src.tools.data_plane import get_design_state

    state = get_design_state()
    payload = ((getattr(state, "data_store", None) or {}).get("generate_volume_mesh__mesh_base64")) if state else None
    return "generate_volume_mesh__mesh_base64" if isinstance(payload, str) and len(payload) > 100 else None


# -- what the agent reads BEFORE it calls ------------------------------------------------------

def description_suffix(tool_name: str) -> str:
    """The REQUIRED block appended to a consuming tool's description.

    Jessica, 2026-09-16: communicate the requirement at the point of calling the tool, so a
    missing input never costs a wasted call in the first place.
    """
    required = REQUIRED_BY_TOOL.get(tool_name)
    if not required:
        return ""
    lines = ["", "REQUIRED COUPLED INPUTS (this call does not run without them):"]
    for item in required:
        lines.append(f"- {item.parameter or '(a completed run)'}: {item.label}. "
                     f"Produced by {item.produced_by}.")
    lines.append("They are captured and injected automatically once that discipline has run for the "
                 "CURRENT geometry -- you do not pass them by hand. A value produced for an earlier "
                 "geometry does not count: re-run that discipline after a design change.")
    return "\n".join(lines)


def apply_to_tool(tool: Any) -> None:
    """Add the REQUIRED block to a consuming tool's description, once."""
    suffix = description_suffix(getattr(tool, "name", ""))
    if not suffix:
        return
    description = getattr(tool, "description", "") or ""
    if "REQUIRED COUPLED INPUTS" in description:
        return
    try:
        tool.description = description + "\n" + suffix
    except Exception:      # pragma: no cover - a read-only description must not break loading
        logger.debug("could not annotate %s with its required coupled inputs", getattr(tool, "name", "?"))


# -- metrics ---------------------------------------------------------------------------------------

def _bump(tool_name: str, producer: str, state: str) -> None:
    from src.tools.knowledge_base import get_kb

    kb = get_kb()
    if kb is None:
        return
    kb.bump_nested("missing_data_refusals", tool_name, f"{producer}:{state}")


def refusals_so_far(agent: str | None = None) -> int:
    """How many times THIS agent has been refused for missing data or duplicate work.

    Both kinds cost a step, and a step costs 200-900 s, so they are counted together: what
    the agent needs to know is that it is spending its budget being turned away.
    """
    from src.tools.knowledge_base import get_kb

    kb = get_kb()
    if kb is None:
        return 0
    name = agent or ""
    with kb._lock:
        entries = list(kb.entries)
    return sum(1 for e in entries
               if e.get("status") == "refused" and (not name or e.get("agent") == name))


def coupling_status() -> dict:
    """What actually reached each solver, for result.json."""
    out: dict[str, Any] = {
        "aero_status": status(AERO_CD),
        "mass_status": status(WING_MASS),
        "propulsion_status": status(CYCLE),
        "engine_sizing_status": status(MTOM),
    }
    out["propulsion_ran"] = out["propulsion_status"] == "ok"
    # B3: coupled only when the mission itself reports flying the designed engine's deck (aviary-mcp's
    # run_simulation `engine` block), not when the framework merely intended to attach it.
    from src.tools.data_plane import engine_deck_enabled, get_design_state

    store = get_design_state().data_store if get_design_state() is not None else {}
    engine = store.get("mission_engine") or {}
    out["engine_deck_status"] = status(ENGINE_DECK)
    out["mission_engine"] = {k: engine.get(k) for k in ("deck", "deck_key", "designed_deck", "engine_current_at_flight",
                                                        "scale_factor",
                                                        "scaled_sls_thrust_lbf", "engine_mass_kg")} if engine else None
    out["propulsion_coupled"] = bool(engine_deck_enabled() and engine.get("engine_current_at_flight")
                                     and engine.get("designed_deck"))
    out["mission_coupled"] = out["aero_status"] == "ok" and out["mass_status"] == "ok"
    out["design_fingerprint"] = current_design_fingerprint()
    return out
