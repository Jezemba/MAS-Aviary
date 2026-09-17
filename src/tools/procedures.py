"""The written-down procedure for each role: which tools, in which order (B85).

WHY THIS EXISTS (Jessica, 2026-09-16)

"Not knowing to call read_history_csv is a bug, because there is no error message to
reflect that, and nowhere where that is documented."

That is the right diagnosis of what validate8_net showed. The agents were not incapable --
they claimed TODOs, obeyed the in-flight guard and reorganised around the first refusal.
What they did not have was the SEQUENCE. The SU2 solves completed and wrote history.csv,
mass and propulsion sat unclaimed and needed no aero at all, and a peer told to wait spent a
400-900 s step deciding what to do instead. None of that is reasoning failure; it is a
missing reference.

So the sequence is written down once, here, as data:

  * ``read_procedure(role=...)`` -- an agent can look it up whenever it wants;
  * every refusal that names a producing tool is generated from this same table, so the
    error message and the reference can never drift apart;
  * after ``_REFUSALS_BEFORE_POINTER`` refusals the agent is told plainly that it is
    spending its step budget and pointed at ``read_procedure`` (Jessica: "maybe after 3
    refusals it should get a warning that it's using its step budget because it may not
    know").

This is deliberately a flat, checkable table rather than prose in a prompt. Prose in the
system prompt is what we had; it produced 43 aero warnings and 51 mass hints with 0
estimate_mass (B84).
"""
from __future__ import annotations

from dataclasses import dataclass

_REFUSALS_BEFORE_POINTER = 3


@dataclass(frozen=True)
class Step:
    tool: str
    why: str


@dataclass(frozen=True)
class Procedure:
    role: str
    goal: str
    steps: tuple[Step, ...]
    produces: tuple[str, ...]
    needs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


GEOMETRY = Procedure(
    role="geometry",
    goal="Turn the design variables into a morphed aircraft and a CFD-ready volume mesh.",
    needs=(),
    produces=("a morphed CPACS geometry", "an exported CPACS file for the other disciplines",
              "generate_volume_mesh__mesh_base64 (the volume mesh)"),
    steps=(
        Step("open_cpacs", "open the baseline CPACS; keep the session id."),
        Step("set_high_level_parameters", "morph the wing to the design. updates={'area': <m^2>, "
                                          "'aspect_ratio': <-> , 'sweep': <deg>} -- ANY ONE of those "
                                          "three fires the morph, and they are the values the mission "
                                          "already holds (Aircraft.Wing.AREA / ASPECT_RATIO / SWEEP). "
                                          "span+root_chord+tip_chord is only an alternative way to "
                                          "derive area; you do NOT need all four, and you do not need "
                                          "get_high_level_parameters first (it returns {} for a real "
                                          "CPACS wing -- B97)."),
        Step("export_cpacs", "write the MORPHED geometry to a file; mass-mcp sizes on this file, "
                             "not on the baseline fixture."),
        Step("generate_volume_mesh", "produce the volume mesh the aero stage solves on."),
    ),
    notes=("Take the target values from the design the mission holds -- get_design_state or the task "
           "text -- and pass them straight to set_high_level_parameters. Every run measured on the 7960 "
           "skipped the morph and meshed the BASELINE aircraft instead (B95/B97).",
           "A design change here makes every downstream result stale: aero and mass must be "
           "re-run after a morph, or the mission will refuse them as produced for an earlier geometry.",
           "component_uid is a CPACS UID, not a display name: the DLR-F25 baseline has Fuselage1, "
           "Wing1 (main wing), Wing2H (horizontal tail) and Wing3V (vertical tail). 'Wing' is not a "
           "UID and every run so far has lost a step to it. Call read_procedure or check the task "
           "text for the UIDs of the CPACS actually open.",),
)

AERO = Procedure(
    role="aero",
    goal="Solve cruise CL/CD for the CURRENT geometry so aviary does not use its default drag polar.",
    needs=("the volume mesh from the geometry role",),
    produces=("aero.cl_cruise", "aero.cd_cruise"),
    steps=(
        Step("create_su2_session", "start an SU2 session."),
        Step("set_mesh", "give the session the volume mesh (pass the mesh ref, not the bytes)."),
        Step("run_su2_solver", "solve. Its response carries final_coefficients, and CL/CD are "
                               "captured from it automatically -- you do NOT need another call."),
        Step("read_history_csv", "ONLY if the solve reported final_coefficients_note instead of "
                                 "final_coefficients: read the history file to recover CL/CD."),
    ),
    notes=("The coefficients are captured for you and injected into the mission. You never pass "
           "CL/CD by hand and you never compute the drag factor yourself.",
           "If another agent is already running the solve, do not wait idly -- take an unclaimed "
           "TODO. Mass and propulsion need no aero at all.",),
)

STRUCTURES = Procedure(
    role="structures",
    goal="Size the structural wing mass of the current geometry.",
    needs=("the exported MORPHED CPACS file from the geometry role",),
    produces=("mass.wing_kg", "mass.mtom_kg"),
    steps=(
        Step("estimate_mass", "size on the exported morphed CPACS path -- NOT the baseline fixture."),
    ),
    notes=("mass.wing_kg is injected into the mission as Aircraft.Wing.MASS_SCALER; without it "
           "aviary silently uses its own internal FLOPS wing mass.",
           "mass.mtom_kg sizes the engine, so propulsion cannot run until this has.",),
)

PROPULSION = Procedure(
    role="propulsion",
    goal="Run the engine cycle for this design.",
    needs=("mass.mtom_kg from the structures role -- it sizes design-point thrust",),
    produces=("prop.sfc_cruise", "prop.fn_lbf"),
    steps=(
        Step("create_cycle_model", "build the cycle model."),
        Step("set_inputs", "Fn_DES is injected from mass.mtom_kg automatically."),
        Step("run_cycle", "run it; TSFC and thrust are captured from the outputs."),
    ),
    notes=("The cycle result is RECORDED for this design but does not feed aviary's fuel burn: "
           "aviary burns a tabulated engine deck and no parameter carries SFC into it (B3).",),
)

MISSION = Procedure(
    role="mission",
    goal="Fly the canonical mission on THIS design and measure fuel burn.",
    needs=("aero.cl_cruise / aero.cd_cruise", "mass.wing_kg", "a completed engine cycle"),
    produces=("fuel_burned_kg",),
    steps=(
        Step("set_aircraft_parameters", "apply the design variables; repeat until valid is true. "
                                        "This call has no coupled-input requirement."),
        Step("run_simulation", "fly it. This is the call that REQUIRES the coupled inputs above -- "
                               "without them aviary would substitute its own defaults."),
        Step("get_results", "read fuel burn and the constraint outcomes."),
    ),
    notes=("Everything the mission needs is injected from the typed registry once the producing "
           "discipline has run for the current geometry.",),
)

PROCEDURES: dict[str, Procedure] = {p.role: p for p in (GEOMETRY, AERO, STRUCTURES, PROPULSION, MISSION)}

# Which role produces each coupled variable, so a refusal can point at the right procedure.
PRODUCER_ROLE = {"su2": "aero", "mass": "structures", "pycycle": "propulsion", "tigl": "geometry"}

ORDER = ("geometry", "aero", "structures", "propulsion", "mission")


def render(role: str | None = None) -> str:
    """The procedure for one role, or the whole sequence when no role is given."""
    if role:
        key = str(role).strip().lower()
        procedure = PROCEDURES.get(key) or next(
            (p for name, p in PROCEDURES.items() if key in name or name in key), None)
        if procedure is None:
            return ("No procedure for role " + repr(role) + ". Known roles: "
                    + ", ".join(ORDER) + ". Call read_procedure() with no role for the whole sequence.")
        return _render_one(procedure)
    head = ("THE DESIGN SEQUENCE. Each stage consumes what the one before it produced; a geometry "
            "change makes everything downstream stale.\n" + " -> ".join(ORDER) + "\n")
    return head + "\n".join(_render_one(PROCEDURES[name]) for name in ORDER)


def _render_one(procedure: Procedure) -> str:
    lines = [f"\n=== {procedure.role.upper()} ===", procedure.goal]
    if procedure.needs:
        lines.append("Needs first: " + "; ".join(procedure.needs))
    lines.append("Steps, in order:")
    lines += [f"  {i}. {step.tool} -- {step.why}" for i, step in enumerate(procedure.steps, 1)]
    lines.append("Produces: " + ", ".join(procedure.produces))
    lines += [f"Note: {note}" for note in procedure.notes]
    return "\n".join(lines)


def sequence_for(producer: str) -> str:
    """The tool sequence that produces a given discipline's outputs, for a refusal message."""
    procedure = PROCEDURES.get(PRODUCER_ROLE.get(producer, ""), None)
    if procedure is None:
        return ""
    return " -> ".join(step.tool for step in procedure.steps)


def budget_warning(refusals_so_far: int) -> str:
    """After a few refusals, say plainly that they cost steps, and where the answer is written.

    An agent cannot see its own step budget draining, and in validate8_net two of three peers
    reached max_steps without producing anything (Jessica, 2026-09-16).
    """
    if refusals_so_far < _REFUSALS_BEFORE_POINTER:
        return ""
    return (
        f"\n\nYOU HAVE NOW BEEN REFUSED {refusals_so_far} TIMES, AND EVERY REFUSAL COSTS YOU ONE OF "
        "YOUR LIMITED STEPS. Stop guessing the order of work: call read_procedure(role='<your role>') "
        "-- or read_procedure() for the whole design sequence -- which lists exactly which tools "
        "produce what, in order. If another agent is already doing this work, call read_todos and "
        "take something unclaimed instead; mass and propulsion do not depend on aero."
    )


# The DLR-F25 baseline's UIDs, used until the live ones are captured from the open session.
BASELINE_COMPONENT_UIDS = ("Fuselage1", "Wing1", "Wing2H", "Wing3V")


def component_uids() -> tuple:
    """The real UIDs of the CPACS in play, live ones preferred over the baseline."""
    try:
        from src.tools.data_plane import get_design_state

        state = get_design_state()
        live = list((getattr(state, "data_store", None) or {}).get("component_uids") or []) if state else []
    except Exception:      # pragma: no cover - never fail a prompt over this
        live = []
    return tuple(live) if live else BASELINE_COMPONENT_UIDS


def component_uid_block() -> str:
    """The UID line for a peer's task text (B86 3.5)."""
    uids = component_uids()
    if not uids:
        return ""
    return ("COMPONENT UIDs for this CPACS -- use these EXACTLY, they are UIDs and not display "
            "names: " + ", ".join(uids) + ". The main wing is "
            + next((u for u in uids if u.lower().startswith("wing")), uids[0])
            + ", not 'Wing'.")

# -- What a peer should do NEXT (B93) ----------------------------------------------------------
#
# validate11 measured the cost of not answering this question. The geometry holder re-claimed a
# TODO it already held three times (556 + 465 + 662 s) and re-opened the same CPACS file twice,
# never reaching generate_volume_mesh, while the aero holder sat blocked on the mesh it was not
# producing. Across validate10 and validate11 peers spent 2,354 s re-claiming work they already
# owned. read_procedure has had 0 calls in every run since it was added: the reference exists and
# is never consulted, exactly as the prose in the system prompt was never acted on (B84).
#
# So the next step is pushed, not offered: every refusal and every self-claim ends with the one
# line that says what this peer should do now, derived from the table above and the knowledge
# base's record of what has actually succeeded for this design.

# Which roles must have produced something before a role can do its own work.
DEPENDS_ON: dict[str, tuple[str, ...]] = {
    "geometry": (),
    "aero": ("geometry",),
    "structures": ("geometry",),
    "propulsion": ("structures",),
    "mission": ("aero", "structures", "propulsion"),
}

# Steps a peer may legitimately skip: the procedure lists them as conditional.
_OPTIONAL_STEPS = {("aero", "read_history_csv")}


def _succeeded_tools() -> set:
    """Tools that have SUCCEEDED for this design, from the knowledge base (B81/B87)."""
    try:
        from src.tools.knowledge_base import get_kb

        kb = get_kb()
        if kb is None:
            return set()
        return {e["tool"] for e in kb.entries if e.get("status") == "success"}
    except Exception:      # pragma: no cover - never fail a message over this
        return set()


def next_step_for(role: str, done: set | None = None):
    """The next Step of ``role``'s procedure that has not succeeded yet, or None when finished."""
    procedure = PROCEDURES.get(str(role or "").strip().lower())
    if procedure is None:
        return None
    done = _succeeded_tools() if done is None else done
    for step in procedure.steps:
        if step.tool in done or (procedure.role, step.tool) in _OPTIONAL_STEPS:
            continue
        return step
    return None


def next_step_line(role: str, done: set | None = None) -> str:
    """One pushed line naming this peer's next action, for a refusal or a self-claim (B93).

    Empty when there is nothing useful to say, so callers can append it unconditionally.
    """
    procedure = PROCEDURES.get(str(role or "").strip().lower())
    if procedure is None:
        return ""
    done = _succeeded_tools() if done is None else done
    step = next_step_for(role, done)
    if step is None:
        return (f"Every step of the {procedure.role} procedure has succeeded for this design. "
                f"Release it: mark_todo_done('{_todo_name(procedure.role)}', result='<the values it "
                f"produced>') so the peers waiting on {procedure.role} can move.")
    finished = [s.tool for s in procedure.steps if s.tool in done]
    prefix = (f"Done so far for {procedure.role}: {', '.join(finished)}. " if finished
              else f"Nothing has been produced for {procedure.role} yet. ")
    return prefix + f"YOUR NEXT STEP IS {step.tool} -- {step.why}"


def _todo_name(role: str) -> str:
    """The board's name for a role's TODO (the board calls structures 'mass')."""
    return {"structures": "mass"}.get(role, role)


def role_for_todo(todo_name: str) -> str:
    """The procedure role behind a board TODO name."""
    name = str(todo_name or "").strip().lower()
    if name in PROCEDURES:
        return name
    return {"mass": "structures", "simulation": "mission", "evaluation": "mission"}.get(name, "")


def blocking_roles(role: str, done_todos: set) -> tuple:
    """Which prerequisite roles of ``role`` are not done yet (B93).

    ``done_todos`` is the set of BOARD names that are finished. A peer whose own work cannot
    start because of these is BLOCKED -- it is not idle, and it must not be told to get on
    with work it cannot do.
    """
    role = str(role or "").strip().lower()
    blocking = []
    for need in DEPENDS_ON.get(role, ()):
        if _todo_name(need) not in done_todos:
            blocking.append(need)
    return tuple(blocking)

