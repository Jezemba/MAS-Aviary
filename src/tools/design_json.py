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


def build_design(store: dict, sessions: dict | None = None) -> dict[str, Any]:
    """The design document from the data plane's store. Only captured values, never defaults."""
    from src.config.canonical import load_canonical

    try:
        mission = load_canonical().get("mission", {}) or {}
    except Exception:  # pragma: no cover - never fail a write on config
        mission = {}
    cycle = store.get("prop_cycle_outputs") or {}
    return {
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mission": {k: mission.get(k) for k in ("range_nmi", "num_passengers", "cruise_mach",
                                                "cruise_altitude_ft")},
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
            "injected_into_aviary": {"LIFT_COEFFICIENT": store.get("aero_injected_cl"),
                                     "SUBSONIC_DRAG_COEFF_FACTOR": store.get("aero_injected_drag_factor")},
        },
        "structures": {"wing_mass_kg": store.get("mass_wing_kg"), "mtom_kg": store.get("mass_mtom_kg"),
                       "wing_mass_source": store.get("mass_wing_source")},
        "propulsion": {"sfc_cruise": cycle.get("prop.sfc_cruise"), "fn_lbf": cycle.get("prop.fn_lbf"),
                       "cycle_converged": store.get("prop_cycle_converged")},
        "mission_applied": _public(store.get("design_params_applied")),
        "sessions": dict(sessions or {}),
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
        doc = build_design(getattr(state, "data_store", {}) or {}, getattr(state, "sessions", {}) or {})
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, default=str)
        os.replace(tmp, path)
        return path
    except Exception:  # pragma: no cover
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
