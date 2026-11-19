"""design.json: the current design as the framework captured it, written next to the knowledge base.

sequential-prompts-v2 (Jessica, 2026-10-04): the propulsion, mission, simulation and integrator
prompts say to use "design.json". The framework writes it (not an agent and not the summary model)
from the values it captured from real tool results: the measured geometry, the drag su2-mcp
reported, the mass, the engine cycle, and the parameters the mission flew. It is rewritten after
every tool result the data plane captures, so it is always the latest state. Agents read it with
the read_design_json tool.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from smolagents import Tool

FILE_NAME = "design.json"


def _public(record: dict | None) -> dict:
    return {k: v for k, v in (record or {}).items() if not str(k).startswith("_")}


# V9 (validation 2): aviary-mcp 36b3a53 design_space.READONLY_PARAMETER_NAMES -- computed by aviary, refused with
# READ_ONLY_PARAMETER when sent. Plus any parameter a set reply marked derived (data_plane design_params_derived).
AVIARY_READ_ONLY = frozenset({"Aircraft.Wing.SPAN", "Aircraft.Engine.SCALED_SLS_THRUST"})


def applied_split(store: dict | None) -> tuple[dict, dict]:
    """(the parameters aviary accepted that can be set, the derived read-only ones it reports with them).

    V9: design.json listed Aircraft.Wing.SPAN in mission_applied next to the settable keys; the mission architect copied
    that block into set_aircraft_parameters and aviary refused the whole call (READ_ONLY_PARAMETER) -- all 15 such calls
    of validation 2 had design.json's mission_applied with SPAN in their input, none another source.
    """
    store = store or {}
    derived = AVIARY_READ_ONLY | set(store.get("design_params_derived") or ())
    applied = _public(store.get("design_params_applied"))
    return ({k: v for k, v in applied.items() if k not in derived},
            {k: v for k, v in applied.items() if k in derived})


AVIARY_AERO_KEYS = {"LIFT_COEFFICIENT": "Mission.Design.LIFT_COEFFICIENT",
                    "SUBSONIC_DRAG_COEFF_FACTOR": "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"}


def aero_in_aviary(store: dict) -> dict:
    """The SU2 lift coefficient and drag factor aviary holds (C5): taken from aviary's own confirmation of a
    successful set_aircraft_parameters, never from a call the framework only prepared."""
    applied = (store or {}).get("design_params_applied") or {}
    return {short: applied.get(full) for short, full in AVIARY_AERO_KEYS.items()}


def build_design(store: dict, sessions: dict | None = None) -> dict[str, Any]:
    """The design document from the data plane's store. Only captured values, never defaults."""
    from src.config.canonical import load_canonical

    try:
        mission = load_canonical().get("mission", {}) or {}
    except Exception:  # pragma: no cover - never fail a write on config
        mission = {}
    cycle = store.get("prop_cycle_outputs") or {}
    applied = store.get("design_params_applied") or {}
    settable, derived_read_only = applied_split(store)
    return {
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mission": {k: mission.get(k) for k in ("range_nmi", "num_passengers", "cruise_mach",
                                                "cruise_altitude_ft")},
        # The design-space variables of the prompt table, as tools reported them (2026-10-08):
        # per tigl component the measured geometry and any value set (<key>_set; a set 'sweep' is
        # a positioning angle, the measured one is sweep_quarter_chord_deg), the engine inputs
        # pycycle confirmed, and the mission's own engine scale factor.
        "design_space": {
            "geometry": {uid: dict(v) for uid, v in (store.get("design_space_components") or {}).items()},
            "engine": dict(store.get("design_space_engine") or {}),
            "mission": {k: applied[k] for k in ("Aircraft.Engine.SCALE_FACTOR",) if k in applied},
        },
        "geometry": {
            "cpacs_file": store.get("morphed_cpacs_path"),
            "wing": _public(store.get("geometry_wing")),
            "fuselage": _public(store.get("geometry_fuselage")),
            "mesh_ref": ("generate_volume_mesh__mesh_base64"
                         if isinstance(store.get("generate_volume_mesh__mesh_base64"), str) else None),
        },
        "aero": {
            "cl_cruise": store.get("aero_cl_cruise"),
            "cd_cruise": store.get("aero_cd_cruise"),
            "drag_assessment": store.get("aero_drag_assessment"),
            # B277: the lift target of the solve these came from (None and False: untrimmed, fixed AOA).
            "solve_target_cl": store.get("aero_solve_target_cl"),
            "solve_trimmed": store.get("aero_solve_trimmed"),
            "solve_converged": store.get("aero_solve_converged"),
            "solve_residual": store.get("aero_solve_residual"),
            # B344: the mass / wing area the solve was trimmed for, and whether it was solved on this link or seeded
            # from the last flown link (graph pre-init) with that link's own trim target.
            "solve_trim_basis": store.get("aero_solve_trim_basis"),
            "provenance": store.get("aero_provenance"),
            # Drag model decision 2026-11-18: SU2 is solved and recorded; the mission flies aviary's FLOPS drag.
            "drag_coupling": "solved, not coupled to drag (the mission flies aviary's FLOPS drag)",
            # C5 (B284) + drag decision (B324), merged: what aviary ACCEPTED for the design CL and the drag factor,
            # read from aviary's own "applied" list of a successful set_aircraft_parameters (design_params_applied),
            # never from a call the framework only prepared. Since B324 the framework injects neither and refuses
            # an agent's set of either (DRAG_FROM_AVIARY_MODEL), so both are None: aviary received no SU2 value and
            # computed its own design CL and drag.
            "injected_into_aviary": aero_in_aviary(store),
        },
        "aviary_cruise_point": store.get("aviary_cruise_point"),
        "engine_sizing": {"fn_des_basis": store.get("engine_fn_des_basis"),
                          "error": store.get("engine_sizing_error")},
        # B306 (mass-mcp M3): the deck the mission flies, carried so the next iteration's mass is sized with it.
        "engine_deck": _public(store.get("engine_deck")) if isinstance(store.get("engine_deck"), dict) else None,
        "mass_engine_basis": store.get("mass_engine_basis"),
        "structures": {"wing_mass_kg": store.get("mass_wing_kg"), "mtom_kg": store.get("mass_mtom_kg"),
                       "sizing_converged": store.get("mass_sizing_converged"),
                       "estimate_error": store.get("mass_estimate_error"),
                       # G1: the measured CPACS tails mass-mcp sized with; the framework puts them on aviary.
                       "tails": ({k: v for k, v in store["geometry_tails"].items() if not str(k).startswith("_")}
                                 if isinstance(store.get("geometry_tails"), dict) else None),
                       "tails_source": (store.get("geometry_tails") or {}).get("_source")
                       if isinstance(store.get("geometry_tails"), dict) else None,
                       "tail_error": store.get("tail_geometry_error"),
                       # J2: the measured wing t/c mass-mcp sized with; the framework puts it on aviary.
                       "wing_thickness_to_chord": (store["geometry_wing_tc"].get("Aircraft.Wing.THICKNESS_TO_CHORD")
                                                   if isinstance(store.get("geometry_wing_tc"), dict) else None),
                       "wing_thickness_to_chord_source": (store["geometry_wing_tc"].get("_source")
                                                          if isinstance(store.get("geometry_wing_tc"), dict) else None),
                       "wing_thickness_to_chord_error": store.get("wing_tc_error"),
                       "wing_mass_source": store.get("mass_wing_source")},
        "propulsion": {"sfc_cruise": cycle.get("prop.sfc_cruise"), "fn_lbf": cycle.get("prop.fn_lbf"),
                       "cycle_converged": store.get("prop_cycle_converged")},
        # V9: settable keys only; the derived values aviary reports (SPAN) apart, marked read-only.
        "mission_applied": settable,
        **({"mission_derived_read_only": {
            "values": derived_read_only,
            "note": ("Computed by aviary from the parameters in mission_applied (SPAN = sqrt(ASPECT_RATIO x AREA)); "
                     "read-only. Do not send these in set_aircraft_parameters: aviary refuses the whole call "
                     "(READ_ONLY_PARAMETER).")}} if derived_read_only else {}),
        "sessions": dict(sessions or {}),
        # B180: the recommendation, the responses to it and complexity requests, as recorded by the agents'
        # tool calls (src/tools/recommendation_tools.py), in order. Carried to the next link with design.json.
        "coordination_signals": list(store.get("coordination_signals") or []),
        # Graph-routed v2 on the networked board: level, role runs, how the link ended, the route events.
        **({"graph_route": store["graph_route"]} if store.get("graph_route") else {}),
        # C1/C7: how the coordinator loop ended; written once, when the link's coordination stops.
        **({"coordination_stop": store["coordination_stop"]} if store.get("coordination_stop") else {}),
    }


def design_path() -> str | None:
    """design.json lives in the link directory, next to knowledge_base.jsonl."""
    from src.tools.knowledge_base import _run_config

    kb_path = (_run_config or {}).get("path")
    return os.path.join(os.path.dirname(os.path.abspath(kb_path)), FILE_NAME) if kb_path else None


def write_design(state: Any) -> str | None:
    """Rewrite design.json from the design state. Never raises: a report must not break a call."""
    try:
        path = design_path()
        if not path or state is None:
            return None
        with _WRITE_LOCK:
            doc = _build_consistent(state)
            _write_atomic(path, doc)
        return path
    except Exception as exc:  # pragma: no cover
        _note_write_failure(state, "design.json", exc)
        return None


# B361: networked peers write design.json from their own call threads at the same time. Every writer used the same
# "design.json.tmp", so two writes could interleave into one file (a corrupt design.json, or os.replace failing on
# a tmp the other writer had already moved), and build_design could raise "dictionary changed size during
# iteration" while another peer's capture wrote the store -- swallowed, so that write (design_flown.json included,
# which the next link starts from) silently did not happen. Writes are serialized, each has its own tmp file, the
# build is retried on a concurrent change, and a write that still fails is recorded.
import threading as _threading  # noqa: E402

_WRITE_LOCK = _threading.Lock()


def _build_consistent(state: Any, attempts: int = 5) -> dict:
    for attempt in range(attempts):
        try:
            return build_design(dict(getattr(state, "data_store", {}) or {}), dict(getattr(state, "sessions", {}) or {}))
        except RuntimeError:          # another thread changed a nested container while it was read
            if attempt == attempts - 1:
                raise
            time.sleep(0.01)
    raise RuntimeError("unreachable")  # pragma: no cover


def _write_atomic(path: str, doc: dict) -> None:
    tmp = f"{path}.{os.getpid()}.{_threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, default=str)
    os.replace(tmp, path)


def _note_write_failure(state: Any, what: str, exc: Exception) -> None:
    import logging

    logging.getLogger(__name__).warning("[B361] %s was not written: %s: %s", what, type(exc).__name__, exc)
    try:
        state.data_store.setdefault("design_json_write_errors", []).append(f"{what}: {type(exc).__name__}: {exc}")
    except Exception:
        pass


FLOWN_FILE_NAME = "design_flown.json"


def write_flown_design(state: Any) -> str | None:
    """B265: design_flown.json, design.json as it stood when a mission flew. Never raises.

    The runner carries this one to the next link (falling back to design.json when nothing flew), so the
    next link starts from the design that has a fuel figure, not from whatever was touched after it.
    """
    try:
        path = design_path()
        if not path or state is None:
            return None
        store = getattr(state, "data_store", {}) or {}
        path = os.path.join(os.path.dirname(path), FLOWN_FILE_NAME)
        with _WRITE_LOCK:             # B361
            doc = _build_consistent(state)
            if store.get("flown_cpacs_path"):
                doc["geometry"]["cpacs_file"] = store["flown_cpacs_path"]
            _write_atomic(path, doc)
        return path
    except Exception as exc:  # pragma: no cover
        _note_write_failure(state, "design_flown.json", exc)
        return None


class ReadDesignJson(Tool):
    name = "read_design_json"
    description = (
        "Returns design.json: the current design as the framework captured it from real tool results "
        "-- the mission, the measured geometry (wing, fuselage, CPACS file, mesh reference), the "
        "aerodynamic coefficients SU2 reported, the masses, the engine cycle, and the parameters the "
        "mission flew. Use it instead of re-deriving values from earlier stages' text."
    )
    inputs: dict[str, Any] = {}
    output_type = "string"

    def forward(self) -> str:  # noqa: D102
        from src.tools.data_plane import get_design_state

        path = write_design(get_design_state())
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        state = get_design_state()
        return json.dumps(build_design(getattr(state, "data_store", {}) or {},
                                       getattr(state, "sessions", {}) or {}), indent=1, default=str)
