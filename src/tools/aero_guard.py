"""B111: a mission must not fly aero that SU2 could not physically have produced.

geoauth_all8_7960: three of eight links flew Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR 0.5 -- the
floor of aero_cd_to_aviary_drag_factor's clamp -- built from SU2 results no wing produces: CL 0.0497
with CD -0.0168 (three links, identical, all solves on sessions that were never configure_from_cpacs'd
or that sized the baseline), and CL 1.42. Orchestrated iterative link 2 reported 6,911 kg -- half of
every other link -- on half the drag. The clamp hid the input; the CL bound check only ran if an
agent had happened to call get_design_space, and the agent can pass the clamped values itself.

B274 (l20_final bug checks, Cases 1-2): O_SP 6 / 19 and O_IF 4 flew SU2 results solved at a fixed 2 deg with
no lift target (CL 0.461 / 0.463) whose CD (0.0053 / 0.0044) was below the induced drag of that lift; su2-mcp
had marked them `su2_unchecked` (no aspect ratio, so no floor). They passed this guard, which checked only the
sign of CD and the CL range, and became the study's three best iterations. The mission is now also refused when
the aero it would fly came from a solve with no target CL, when the drag is `su2_unchecked`, or when CD is
below CL^2 / (pi e AR).
"""
from __future__ import annotations

import math
import os

_CL_RANGE = (0.05, 1.0)       # aviary-mcp's declared Mission.Design.LIFT_COEFFICIENT range


def oswald_efficiency(aspect_ratio: float) -> float:
    """Raymer eq. 12.48 span efficiency, held to [0.5, 0.95] -- su2-mcp drag_model.oswald_efficiency, the e su2-mcp
    uses for its own induced-drag floor (B138) when the caller passes none (MAS-Aviary passes none)."""
    e = 1.78 * (1.0 - 0.045 * aspect_ratio ** 0.68) - 0.64
    return min(max(e, 0.5), 0.95)


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


def _solve_record() -> dict:
    """What the data plane recorded about the solve the aero came from (B274)."""
    try:
        from src.tools.data_plane import get_design_state

        state = get_design_state()
        store = state.data_store if state is not None else {}
    except Exception:      # pragma: no cover
        store = {}
    assessment = store.get("aero_drag_assessment") if isinstance(store.get("aero_drag_assessment"), dict) else {}
    wing = store.get("geometry_wing") if isinstance(store.get("geometry_wing"), dict) else {}
    return {"trimmed": store.get("aero_solve_trimmed"), "assessment": assessment,
            "wing_ar": wing.get("Aircraft.Wing.ASPECT_RATIO")}


def induced_drag_floor(cl, assessment: dict | None, wing_ar=None) -> tuple[float, float, float] | None:
    """(CL^2/(pi e AR), AR, e): AR and e as su2-mcp used them for the solve, else the geometry's AR with su2-mcp's e."""
    assessment = assessment or {}
    ar = assessment.get("aspect_ratio")
    e = assessment.get("oswald_efficiency")
    if not isinstance(ar, (int, float)) or ar <= 0:
        ar = wing_ar if isinstance(wing_ar, (int, float)) and wing_ar > 0 else None
        e = None
    if ar is None:
        return None
    if not isinstance(e, (int, float)) or e <= 0:
        e = oswald_efficiency(float(ar))
    return float(cl) ** 2 / (math.pi * float(ar) * float(e)), float(ar), float(e)


def nonphysical_reason(cl, cd, record: dict | None = None) -> str | None:
    """Why this CL/CD cannot be flown as a cruise solution, or None if it can."""
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
    record = record or {}
    if record.get("trimmed") is False:
        problems.append("the SU2 solve had no target CL: it ran at a fixed angle of attack, so its CL is not the "
                        "cruise lift and its CD is not the drag at cruise")
    if (record.get("assessment") or {}).get("source") == "su2_unchecked":
        problems.append("su2-mcp could not check the drag against the induced drag (drag source su2_unchecked: the "
                        "session had no aspect_ratio)")
    floor = induced_drag_floor(cl, record.get("assessment"), record.get("wing_ar")) if cd > 0.0 else None
    if floor is not None and cd < floor[0] * (1.0 - 1e-9):
        problems.append(f"CD {cd:.4g} is below the induced drag of its own lift, CL^2/(pi e AR) = {floor[0]:.4g} "
                        f"(AR {floor[1]:.4g}, e {floor[2]:.4g}), which an inviscid flow cannot be")
    return "; ".join(problems) or None


def aero_nonphysical(tool_name: str, resolved: dict) -> dict | None:
    """Refuse run_simulation while the design's SU2 aero is non-physical (B111) or untrimmed / unchecked (B274)."""
    if tool_name != "run_simulation":
        return None
    if os.environ.get("AVION_REQUIRE_PHYSICAL_AERO", "1") != "1":
        return None
    cl, cd, su2 = _latest_aero()
    if cl is None or cd is None:
        return None                      # no aero at all is B84's refusal, not this one
    reason = nonphysical_reason(cl, cd, _solve_record())
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
            "condition, reference area and the cruise-lift target) or the wrong reference area. The aero owner should call "
            f"configure_from_cpacs(session_id='{sid}', cpacs_file_path=<the design's CPACS>), then "
            "run_su2_solver and read_history_csv again; then run_simulation."
        ),
        "su2_cl": float(cl),
        "su2_cd": float(cd),
    }
