"""Coupling must not depend on WHICH tool the agent reads results with.

B23. ``read_history_csv`` was the only capture point for CL/CD, so a run coupled
or not according to the caller's choice of follow-up tool rather than whether the
physics worked.

Measured in the 2026-08-12 post-fix sweep, same config, adjacent runs:

  run 1/16  ... run_su2_solver -> read_history_csv        -> COUPLED
  run 3/16  ... run_su2_solver -> sample_surface_solution -> UNCOUPLED

Run 3's aero stage was otherwise flawless -- create_su2_session,
configure_from_cpacs, set_mesh, get_su2_status, run_su2_solver (exit 0, 155 s),
zero config_errors, zero tool errors. It reached for the surface sampler, hit
"Surface solution not found", retried the identical call, and returned. The
coefficients existed in history.csv the entire time, so the mission flew
aviary's DEFAULT drag.

su2-mcp's run_su2_solver now reports the coefficients it just computed, and this
captures from that response as well. A solver that has computed these values
should report them rather than leaving them to exactly one of several plausible
follow-up calls.
"""

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools import coupling
from src.tools.data_plane import get_design_state, init_data_plane, intercept_response

TOOL_SERVER_MAP = {
    "run_su2_solver": "su2",
    "read_history_csv": "su2",
    "sample_surface_solution": "su2",
}


@pytest.fixture(autouse=True)
def _state():
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    yield
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


SOLVED = {
    "success": True,
    "exit_code": 0,
    "runtime_seconds": 155.3,
    "final_coefficients": {"CL": 0.169656643, "CD": 0.005549388855, "CMz": -0.0123},
}


def _cl_cd():
    ds = get_design_state()
    return coupling.get_var(ds, "aero.cl_cruise"), coupling.get_var(ds, "aero.cd_cruise")


class TestCaptureFromSolver:
    def test_solver_response_populates_the_registry(self):
        intercept_response("run_su2_solver", json.dumps(SOLVED))
        cl, cd = _cl_cd()
        assert cl == pytest.approx(0.169656643)
        assert cd == pytest.approx(0.005549388855)

    def test_the_run_3_sequence_now_couples(self):
        """Solve, then reach for the surface sampler and fail -- still coupled."""
        intercept_response("run_su2_solver", json.dumps(SOLVED))
        intercept_response(
            "sample_surface_solution",
            json.dumps({"error": {"type": "not_found", "message": "not found"}}),
        )
        cl, cd = _cl_cd()
        assert cl is not None and cd is not None

    def test_l_over_d_derived(self):
        intercept_response("run_su2_solver", json.dumps(SOLVED))
        lod = coupling.get_var(get_design_state(), "aero.l_over_d")
        assert lod == pytest.approx(0.169656643 / 0.005549388855)

    def test_provenance_names_the_real_source(self):
        """The registry must not claim read_history_csv for a solver capture."""
        intercept_response("run_su2_solver", json.dumps(SOLVED))
        src = get_design_state().data_store.get("analysis_vars__src") or {}
        assert src.get("aero.cl_cruise") == "run_su2_solver"

    def test_negative_inviscid_cd_is_captured_not_rejected(self):
        """Inviscid CD is legitimately negative (d'Alembert); capture records
        what SU2 measured. Validation belongs at injection."""
        payload = dict(SOLVED, final_coefficients={"CL": 0.15, "CD": -0.000365826459})
        intercept_response("run_su2_solver", json.dumps(payload))
        _, cd = _cl_cd()
        assert cd == pytest.approx(-0.000365826459)


class TestReadHistoryStillWorks:
    def test_history_capture_unchanged(self):
        rows = {"rows": [{'       "CL"       ': "0.2059", '       "CD"       ': "0.0018"}]}
        intercept_response("read_history_csv", json.dumps(rows))
        cl, cd = _cl_cd()
        assert cl == pytest.approx(0.2059)
        assert cd == pytest.approx(0.0018)

    def test_history_overrides_an_earlier_solver_capture(self):
        """Both fire in a normal run; the explicit history read is the more
        precise source and must win, not be blocked by the earlier capture."""
        intercept_response("run_su2_solver", json.dumps(SOLVED))
        intercept_response(
            "read_history_csv",
            json.dumps({"rows": [{"CL": "0.2059", "CD": "0.0018"}]}),
        )
        cl, cd = _cl_cd()
        assert cl == pytest.approx(0.2059)


class TestNoFalseCaptures:
    def test_failed_solve_captures_nothing(self):
        intercept_response(
            "run_su2_solver",
            json.dumps({"success": False, "solver_error": "mesh not found"}),
        )
        assert _cl_cd() == (None, None)

    def test_success_without_coefficients_captures_nothing(self):
        intercept_response(
            "run_su2_solver",
            json.dumps({"success": True, "final_coefficients_note": "no CL/CD"}),
        )
        assert _cl_cd() == (None, None)

    def test_malformed_coefficients_ignored(self):
        for bad in ({"CL": "abc", "CD": "0.1"}, {"CL": 0.1}, {}, None, "nope"):
            init_data_plane(DesignState(), TOOL_SERVER_MAP)
            intercept_response(
                "run_su2_solver", json.dumps({"success": True, "final_coefficients": bad})
            )
            assert _cl_cd() == (None, None), f"{bad!r} should not have been captured"

    def test_unrelated_tool_ignored(self):
        intercept_response("estimate_mass", json.dumps({"final_coefficients": {"CL": 1, "CD": 1}}))
        assert _cl_cd() == (None, None)
