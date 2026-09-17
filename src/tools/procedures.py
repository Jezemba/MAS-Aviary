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
        Step("set_high_level_parameters", "apply span, root chord, tip chord and sweep TOGETHER -- "
                                          "the morph only fires when all four are given."),
        Step("export_cpacs", "write the MORPHED geometry to a file; mass-mcp sizes on this file, "
                             "not on the baseline fixture."),
        Step("generate_volume_mesh", "produce the volume mesh the aero stage solves on."),
    ),
    notes=("A design change here makes every downstream result stale: aero and mass must be "
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
