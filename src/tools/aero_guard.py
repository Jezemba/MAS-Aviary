"""B111: a mission must not fly aero that SU2 could not physically have produced.

geoauth_all8_7960: three of eight links flew Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR 0.5 -- the
floor of aero_cd_to_aviary_drag_factor's clamp -- built from SU2 results no wing produces: CL 0.0497
with CD -0.0168 (three links, identical, all solves on sessions that were never configure_from_cpacs'd
or that sized the baseline), and CL 1.42. Orchestrated iterative link 2 reported 6,911 kg -- half of
every other link -- on half the drag. The clamp hid the input; the CL bound check only ran if an
agent had happened to call get_design_space, and the agent can pass the clamped values itself.
"""
from __future__ import annotations

import os

_CL_RANGE = (0.05, 1.0)       # aviary-mcp's declared Mission.Design.LIFT_COEFFICIENT range


def _latest_aero():
    try:
        from src.tools import coupling
        from src.tools.data_plane import get_design_state

        state = get_design_state()
        if state is None:
            return None, None, None
        cl = coupling.get_var(state, "aero.cl_cruise")
        cd = coupling.get_var(state, "aero.cd_cruise")
        if cl is None or cd is None:
            cl = state.data_store.get("aero_cl_cruise")
            cd = state.data_store.get("aero_cd_cruise")
        su2 = (getattr(state, "sessions", None) or {}).get("su2")
        return cl, cd, su2
    except Exception:      # pragma: no cover - a guard problem must never block work
        return None, None, None


def nonphysical_reason(cl, cd) -> str | None:
    """Why this CL/CD cannot be a cruise solution, or None if it can."""
    try:
        cl, cd = float(cl), float(cd)
    except (TypeError, ValueError):
        return None
    problems = []
    if cd <= 0.0:
        problems.append(f"CD {cd:.4g} is not positive -- no real flow has zero or negative drag")
    if not (_CL_RANGE[0] <= cl <= _CL_RANGE[1]):
        problems.append(f"CL {cl:.4g} is outside the cruise range aviary accepts "
                        f"[{_CL_RANGE[0]}, {_CL_RANGE[1]}]")
    return "; ".join(problems) or None


def aero_nonphysical(tool_name: str, resolved: dict) -> dict | None:
    """Refuse run_simulation while the design's SU2 aero is non-physical (B111)."""
    if tool_name != "run_simulation":
        return None
    if os.environ.get("AVION_REQUIRE_PHYSICAL_AERO", "1") != "1":
        return None
    cl, cd, su2 = _latest_aero()
    if cl is None or cd is None:
        return None                      # no aero at all is B84's refusal, not this one
    reason = nonphysical_reason(cl, cd)
    if reason is None:
        return None
    sid = su2 or "<the su2 session>"
    return {
        "success": False,
        "error_code": "AERO_NONPHYSICAL",
        "error": (
            f"The SU2 result this mission would fly is not physical: {reason}. Flying it would scale "
            "the aircraft's drag by a clamped factor and report a fuel burn no aircraft achieves. The "
            "usual cause is a solve on a session that was never configured for this aircraft (flight "
            "condition and reference area) or the wrong reference area. The aero owner should call "
            f"configure_from_cpacs(session_id='{sid}', cpacs_file_path=<the design's CPACS>), then "
            "run_su2_solver and read_history_csv again; then run_simulation."
        ),
        "su2_cl": float(cl),
        "su2_cd": float(cd),
    }
