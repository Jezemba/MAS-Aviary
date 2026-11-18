"""B111: a mission must not fly aero that SU2 could not physically have produced.

geoauth_all8_7960: three of eight links flew Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR 0.5 -- the
floor of aero_cd_to_aviary_drag_factor's clamp -- built from SU2 results no wing produces: CL 0.0497
with CD -0.0168 (three links, identical, all solves on sessions that were never configure_from_cpacs'd
or that sized the baseline), and CL 1.42. Orchestrated iterative link 2 reported 6,911 kg -- half of
every other link -- on half the drag. The clamp hid the input; the CL bound check only ran if an
agent had happened to call get_design_space, and the agent can pass the clamped values itself.

B277 (l20_final bug checks, Cases 1-2): O_SP 6 / 19 and O_IF 4 flew SU2 results solved at a fixed 2 deg with
no lift target (CL 0.461 / 0.463) whose CD (0.0053 / 0.0044) was below the induced drag of that lift; su2-mcp
had marked them `su2_unchecked` (no aspect ratio, so no floor). They passed this guard, which checked only the
sign of CD and the CL range, and became the study's three best iterations. The mission is now also refused when
the aero it would fly came from a solve with no target CL, when the drag is `su2_unchecked`, or when CD is
below CL^2 / (pi e AR).

Drag model decision (Jessica, 2026-11-18): the mission flies aviary's FLOPS drag; SU2's CL/CD are recorded only. The
guard now requires the design's SU2 solve to be trimmed to the cruise lift (within su2-mcp's 1 %) and converged;
the CD-based rules (CD > 0, su2_unchecked, induced drag) were removed with the drag coupling.
"""
from __future__ import annotations

import os

_CL_RANGE = (0.05, 1.0)       # aviary-mcp's declared Mission.Design.LIFT_COEFFICIENT range
# B306 (audit F7): the trim tolerance su2-mcp itself applies when it reports a solve (su2-mcp fix/final-rerun
# convergence.TRIM_TOLERANCE = 0.01, relative), so the framework and the tool agree on what "on target" means.
TRIM_CL_REL_TOL = 0.01


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
    """What the data plane recorded about the solve the aero came from (B277)."""
    try:
        from src.tools.data_plane import get_design_state

        state = get_design_state()
        store = state.data_store if state is not None else {}
    except Exception:      # pragma: no cover
        store = {}
    assessment = store.get("aero_drag_assessment") if isinstance(store.get("aero_drag_assessment"), dict) else {}
    wing = store.get("geometry_wing") if isinstance(store.get("geometry_wing"), dict) else {}
    return {"trimmed": store.get("aero_solve_trimmed"), "assessment": assessment,
            "wing_ar": wing.get("Aircraft.Wing.ASPECT_RATIO"), "target_cl": store.get("aero_solve_target_cl"),
            "converged": store.get("aero_solve_converged"), "residual": store.get("aero_solve_residual"),
            "known": True}


def nonphysical_reason(cl, cd, record: dict | None = None) -> str | None:
    """Why this CL/CD cannot be flown as a cruise solution, or None if it can."""
    try:
        cl, cd = float(cl), float(cd)
    except (TypeError, ValueError):
        return None
    problems = []
    # Drag model decision 2026-11-18: SU2's CD is recorded, not flown (aviary flies its FLOPS drag), so the CD-based
    # rules (CD > 0, the B277 induced-drag rule and su2_unchecked) no longer guard the mission: on the study mesh a
    # converged, trimmed Euler CD can be below the induced drag (A13: negative at M0.5) without that being an
    # invalid solve. What still matters is that the solve is the trimmed, converged cruise solve of this design.
    del cd
    if not (_CL_RANGE[0] <= cl <= _CL_RANGE[1]):
        problems.append(f"CL {cl:.4g} is outside the cruise range aviary accepts "
                        f"[{_CL_RANGE[0]}, {_CL_RANGE[1]}]")
    record = record or {}
    if record.get("trimmed") is False:
        problems.append("the SU2 solve had no target CL: it ran at a fixed angle of attack, so its CL is not the "
                        "cruise lift and its CD is not the drag at cruise")
    elif record.get("known") and record.get("trimmed") is not True:
        # B306 (audit F7): unknown is not trimmed -- a session the framework never saw configured, or aero seeded
        # from a record that does not say.
        problems.append("it is not known that the SU2 solve was trimmed to the cruise lift (no record of the "
                        "session's lift target)")
    target = record.get("target_cl")
    if record.get("trimmed") is True and isinstance(target, (int, float)) and target > 0 \
            and abs(cl / float(target) - 1.0) > TRIM_CL_REL_TOL + 1e-12:
        problems.append(f"CL {cl:.4g} is not the lift target {float(target):.4g} the solve was trimmed to (more than "
                        "1 % apart: the trim did not converge)")
    if record.get("known") and record.get("converged") is not True:
        res = record.get("residual") or {}
        problems.append("the SU2 solve did not reach its residual target"
                        + (f" (rms[Rho] {res.get('rms_rho')} against {res.get('target')})" if res.get("rms_rho") is not None
                           else " (or it is not known that it did)"))
    return "; ".join(problems) or None


def aero_nonphysical(tool_name: str, resolved: dict) -> dict | None:
    """Refuse run_simulation while the design's SU2 aero is non-physical (B111) or untrimmed / unchecked (B277)."""
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
            f"run_simulation was not run: the design's SU2 cruise solve is not usable: {reason}. A mission needs a "
            "current, converged SU2 solve trimmed to the cruise lift (its CL/CD are recorded; the mission flies "
            "aviary's drag model). The usual cause is a session that was never configured for this aircraft or a "
            "solve that stopped early. The aero owner should call "
            f"configure_from_cpacs(session_id='{sid}', cpacs_file_path=<the design's CPACS>), then "
            "run_su2_solver and read_history_csv again; then run_simulation."
        ),
        "su2_cl": float(cl),
        "su2_cd": float(cd),
    }
