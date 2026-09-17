"""No SU2 solve without a mesh on the session it will actually use (B88).

validate9, 21:07-21:24: ``set_mesh`` had 0 calls in the whole run. The 34.5 MB mesh existed
only as a data-plane ref, both SU2 workdirs held config.cfg and nothing else, and
``run_su2_solver`` started anyway and died in 2.6 s with "The SU2 mesh file named mesh.su2 was
not found" -> MPI_ABORT. B31 enforces meshing before FINISHING, not before SOLVING, so nothing
stopped it; B84 then correctly refused ``run_simulation`` for missing aero and the link had no
way out -- no captured CL/CD and no path to one.

The refusal names the ref the peer already holds, so the fix is one call away.
"""
from __future__ import annotations

def mesh_missing(tool_name: str, resolved: dict) -> dict | None:
    """Refuse ``run_su2_solver`` on a session that has no mesh, before the server is called.

    validate9, 21:07-21:24: ``set_mesh`` had 0 calls in the whole run. The 34.5 MB mesh existed
    only as a data-plane ref, both SU2 workdirs held config.cfg and nothing else, and the solve
    started anyway and died in 2.6 s with "The SU2 mesh file named mesh.su2 was not found" ->
    MPI_ABORT. B31 enforces meshing before FINISHING, not before solving, so nothing stopped it;
    B84 then correctly refused run_simulation for missing aero and the link had no way out.

    The refusal names the ref the peer already holds, so the fix is one call away.
    """
    import os

    if tool_name != "run_su2_solver":
        return None
    if os.environ.get("AVION_REQUIRE_MESH_BEFORE_SOLVE", "1") != "1":
        return None

    session_id = (resolved or {}).get("session_id")
    try:
        from src.tools.duplicate_guard import _state as guard_state

        gs = guard_state()
        session = (gs or {}).get("su2", {}).get(str(session_id)) if gs else None
    except Exception:      # pragma: no cover - a guard problem must never block work
        return None
    if session is None or session.get("mesh"):
        return None        # unknown session: let the server judge it. Has a mesh: proceed.

    from src.tools.data_plane import get_design_state

    state = get_design_state()
    payload = ((getattr(state, "data_store", None) or {}).get("generate_volume_mesh__mesh_base64")) if state else None
    have_mesh = isinstance(payload, str) and len(payload) > 100
    if have_mesh:
        where = ("A mesh for this design already exists: ref generate_volume_mesh__mesh_base64. "
                 f"Call set_mesh(session_id='{session_id}', mesh_base64='generate_volume_mesh__mesh_base64') "
                 "and then run_su2_solver again.")
    else:
        where = ("No mesh exists for this design yet. Run generate_volume_mesh first (component_uid is a "
                 "CPACS UID -- read_procedure(role='geometry') lists them), then set_mesh, then solve.")
    return {
        "success": False,
        "error_code": "NO_MESH",
        "error": (
            f"session {session_id} has no mesh attached, so the solver would abort immediately "
            "(SU2 looks for mesh.su2 in the session workdir and there is none). " + where
        ),
        "session_id": session_id,
    }
