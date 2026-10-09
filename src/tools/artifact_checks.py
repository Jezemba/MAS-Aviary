"""Final-answer checks that stop an agent reporting work it did not do (B31).

WHY THIS EXISTS

Twice -- 2026-08-12 and again on 2026-09-14 (drc-Precision-7960-Tower, Stage A
run 1) -- the sequential geometry stage called open_cpacs, read the wing,
morphed it, and finished with ``MESH_BASE64: 'exported'`` having never called
generate_volume_mesh. Every tool call it made succeeded, so the handler's
``tool_success`` aspiration accepted the stage. The aero stage then retried three
times (35 of the link's 91 minutes) against a mesh that did not exist, and the
mission ran uncoupled. Nothing detected the gap where it happened.

A prompt cannot close this: the prompt already said "Always call
generate_volume_mesh". The check has to sit where every coordination structure
ends an agent's turn, and that is smolagents' ``final_answer_checks``. Sequential
stages, orchestrated workers, networked peers and graph nodes all finish through
it, so one check covers all eight combinations without the framework deciding
anyone's stage order.

WHAT IT CHECKS

An agent may not finish while the run has no volume mesh IF, in its own turn,
it did geometry work (opened or modified the CPACS: GEOMETRY_WORK_TOOLS) AND it
holds generate_volume_mesh. That is exactly the failure observed: do the
geometry, skip the mesh, report success.

Why "did geometry work" and not merely "holds the tool" (Jessica, 2026-09-14):
the coordination structure is the variable under study, and tool distribution
differs by structure. Networked peers all hold every tool, so a holds-the-tool
rule would stop EVERY networked peer from finishing until someone meshed --
pressure no other structure receives. Keyed on the agent's own actions, the rule
binds the same kind of agent everywhere: the sequential geometry stage, the
orchestrated worker that did geometry, the networked peer that took geometry on.
An agent doing mass, propulsion or aero work is never affected.

The evidence of a mesh is the payload the data plane captured at the tool
boundary (``generate_volume_mesh__mesh_base64``), not the agent's claim and not
the mere fact that the tool was called: a refused or failed call leaves no mesh.
Agent memory is reset on every run() (no caller passes reset=False), so "its
own turn" is exactly the memory handed to the check.

The refusal names the tool and how to call it -- an error that names the correct
action is the form measured to get one-step recovery. It is bounded
(MAX_MESH_REFUSALS per agent) so a real outage cannot burn an agent's whole step
budget; past the bound the agent may finish, and the run records that it did so
without a mesh. Every refusal and every mesh-less finish is counted in the data
plane and written to result.json.
"""
from __future__ import annotations

from typing import Any, Callable

MESH_TOOL = "generate_volume_mesh"
# Tools that load or change the geometry. Reading tools (get_wing_summary, ...) are
# deliberately absent: an aero or mass agent reads geometry without owning it.
GEOMETRY_WORK_TOOLS = frozenset({"open_cpacs", "set_high_level_parameters", "morph_wing", "morph_fuselage"})
MESH_STORE_KEY = f"{MESH_TOOL}__mesh_base64"
MAX_MESH_REFUSALS = 3


class MissingVolumeMesh(Exception):
    """Raised to reject a final answer; smolagents shows the message to the agent."""


def volume_mesh_generated() -> bool:
    """True once a real volume mesh payload has been captured in this run."""
    from src.tools.data_plane import get_design_state

    ds = get_design_state()
    if ds is None:
        return False
    payload = (getattr(ds, "data_store", None) or {}).get(MESH_STORE_KEY)
    return isinstance(payload, str) and len(payload) > 100


def _record(key: str, value: Any) -> None:
    from src.tools.data_plane import get_design_state

    ds = get_design_state()
    if ds is None:
        return
    store = ds.data_store
    if isinstance(value, int):
        store[key] = int(store.get(key) or 0) + value
    else:
        items = list(store.get(key) or [])
        items.append(value)
        store[key] = items


def geometry_work_done(memory: Any) -> list[str]:
    """Names of geometry-work tools this agent called in its current turn."""
    called: list[str] = []
    for step in getattr(memory, "steps", None) or []:
        for tc in getattr(step, "tool_calls", None) or []:
            name = getattr(tc, "name", None)
            if name in GEOMETRY_WORK_TOOLS and name not in called:
                called.append(name)
    return called


def mesh_before_finish(final_answer: Any, memory: Any, agent: Any = None) -> bool:
    """smolagents final_answer check: whoever did the geometry must produce the mesh."""
    tools = getattr(agent, "tools", None) or {}
    if MESH_TOOL not in tools:
        return True
    if volume_mesh_generated():
        return True
    did = geometry_work_done(memory if memory is not None else getattr(agent, "memory", None))
    if not did:
        return True

    name = getattr(agent, "name", None) or "agent"
    refused = int(getattr(agent, "_avion_mesh_refusals", 0) or 0)
    if refused >= MAX_MESH_REFUSALS:
        _record("finished_without_mesh", name)
        return True

    agent._avion_mesh_refusals = refused + 1
    _record("final_answer_refused_no_mesh", 1)
    raise MissingVolumeMesh(
        f"NOT FINISHED (refusal {refused + 1} of {MAX_MESH_REFUSALS}): you worked on the "
        f"geometry in this turn ({', '.join(did)}), but no CFD volume mesh has been "
        f"generated in this run. You have the {MESH_TOOL} tool, and the "
        f"aerodynamic analysis cannot run without a mesh -- reporting a mesh that was "
        f"not generated makes every later step fail. Call {MESH_TOOL} now (omit "
        f"mesh_size_min, mesh_size_max and far_field_distance to use the calibrated "
        f"defaults), then give your final answer with the mesh reference it returns."
    )


# ---- the mission stage: apply and validate before finishing (2026-10-09) -----------------------------
#
# Sandbox 20261009-093133: the mission agent read design.json, called configure_mission, reasoned that
# set_aircraft_parameters was "redundant", and finished with PARAMETERS_SET and VALIDATION_STATUS: valid
# it never produced. The aviary session therefore never received the geometry, SU2 aero or wing mass
# (the data plane applies them inside set_aircraft_parameters), and run_simulation was refused three
# times (GEOMETRY_NOT_APPLIED). The pipeline's completion criterion for that stage is observational only.
# Same rule as the mesh gate: keyed on the agent's own actions, so it binds the agent that did mission
# work in every coordination structure, and never one that did not.

APPLY_TOOL = "set_aircraft_parameters"
MISSION_WORK_TOOLS = frozenset({"configure_mission", APPLY_TOOL})
MAX_VALIDATION_REFUSALS = 3


class MissingValidatedParameters(Exception):
    """Raised to reject a final answer; smolagents shows the message to the agent."""


def _steps(memory: Any) -> list:
    return list(getattr(memory, "steps", None) or [])


def mission_work_done(memory: Any) -> list[str]:
    """Names of mission-work tools this agent called in its current turn."""
    called: list[str] = []
    for step in _steps(memory):
        for tc in getattr(step, "tool_calls", None) or []:
            name = getattr(tc, "name", None)
            if name in MISSION_WORK_TOOLS and name not in called:
                called.append(name)
    return called


def parameters_validated(memory: Any) -> bool:
    """True if a set_aircraft_parameters call in this turn came back valid."""
    import json

    for step in _steps(memory):
        names = [getattr(tc, "name", None) for tc in getattr(step, "tool_calls", None) or []]
        if APPLY_TOOL not in names:
            continue
        text = str(getattr(step, "observations", "") or "")
        try:
            data = json.loads(text)
            if isinstance(data, dict) and data.get("valid") is True:
                return True
        except ValueError:
            if '"valid": true' in text:
                return True
    return False


def validated_parameters_before_finish(final_answer: Any, memory: Any, agent: Any = None) -> bool:
    """smolagents final_answer check: whoever worked on the mission must apply and validate it."""
    tools = getattr(agent, "tools", None) or {}
    if APPLY_TOOL not in tools:
        return True
    mem = memory if memory is not None else getattr(agent, "memory", None)
    did = mission_work_done(mem)
    if not did or parameters_validated(mem):
        return True

    name = getattr(agent, "name", None) or "agent"
    refused = int(getattr(agent, "_avion_validation_refusals", 0) or 0)
    if refused >= MAX_VALIDATION_REFUSALS:
        _record("finished_without_validated_parameters", name)
        return True

    agent._avion_validation_refusals = refused + 1
    _record("final_answer_refused_not_validated", 1)
    raise MissingValidatedParameters(
        f"NOT FINISHED (refusal {refused + 1} of {MAX_VALIDATION_REFUSALS}): you worked on the mission "
        f"in this turn ({', '.join(did)}), but no {APPLY_TOOL} call in this turn returned valid: true. "
        f"The aviary session only receives the design (the geometry's wing and fuselage, the SU2 lift "
        f"and drag, the wing mass) when {APPLY_TOOL} is called, and the simulation cannot fly without it. "
        f"Call {APPLY_TOOL} now, then give your final answer with the validation it returns."
    )


def with_artifact_checks(checks: list[Callable] | None = None) -> list[Callable]:
    """Existing final-answer checks plus the artifact checks, without duplicates."""
    combined = list(checks or [])
    for check in (mesh_before_finish, validated_parameters_before_finish):
        if check not in combined:
            combined.append(check)
    return combined
