"""Injected coupling values are validated against aviary's PUBLISHED bounds.

Two live failures shaped this, in opposite directions.

1. Injecting garbage (2026-08-03). An SU2 config with no REF_AREA left SU2 on its
   1.0 m^2 default, so CL came out 15.18 instead of ~0.08. It was injected as
   Mission.Design.LIFT_COEFFICIENT, aviary's trim solver diverged, and the run
   reported GTOW 310,918 kg / fuel 208,551 kg while the ledger said "coupled".

2. Rejecting valid physics (same day). The first attempt at a guard hardcoded
   CL in [-0.5, 3.0] and CD in (0, 1.0]. Requiring CD > 0 is WRONG: the captured
   value is SU2's INVISCID drag, which by d'Alembert tends to zero and on a
   coarse mesh is routinely slightly NEGATIVE (measured -0.004643). The friction
   term is added downstream by aero_cd_to_aviary_drag_factor. That one constant
   silently uncoupled every single run.

The fix uses no invented numbers: aviary publishes its own bounds via
get_design_space (LIFT_COEFFICIENT 0.05-1.0), the data plane caches them, and
only the value actually injected is checked. Aviary itself reports such a value
in `violations` but still returns valid=True, so it will not stop us.
"""

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools import coupling
from src.tools.data_plane import (
    get_design_state,
    init_data_plane,
    intercept_response,
    resolve_request,
)

TOOL_SERVER_MAP = {
    "read_history_csv": "su2",
    "get_design_space": "aviary",
    "set_aircraft_parameters": "aviary",
}

# Verbatim shape of aviary's get_design_space response for the coupling slots.
DESIGN_SPACE = {
    "success": True,
    "parameters": [
        {"name": "Mission.Design.LIFT_COEFFICIENT", "min": 0.05, "max": 1.0},
        {"name": "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR", "min": 0.5, "max": 2.0},
        {"name": "Aircraft.Wing.ASPECT_RATIO", "min": 7.0, "max": 17.0},
    ],
}


@pytest.fixture(autouse=True)
def _clean():
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    yield
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


def _publish_bounds():
    intercept_response("get_design_space", json.dumps(DESIGN_SPACE))


def _capture(cl, cd):
    intercept_response(
        "read_history_csv",
        json.dumps({"columns": ["CL", "CD"], "rows": [{"CL": cl, "CD": cd}]}),
    )


def _inject(params=None):
    return resolve_request(
        "set_aircraft_parameters",
        {"session_id": "s", "parameters": params if params is not None else {}},
    )


class TestBoundsArePublishedNotInvented:
    def test_bounds_captured_from_design_space(self):
        _publish_bounds()
        bounds = get_design_state().data_store["param_bounds"]
        assert bounds["Mission.Design.LIFT_COEFFICIENT"] == (0.05, 1.0)
        assert bounds["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"] == (0.5, 2.0)

    def test_no_bounds_published_means_no_gate(self):
        """Without the consumer's contract we do not invent one."""
        _capture(0.19, 0.0141)
        out = _inject()
        assert out["parameters"]["Mission.Design.LIFT_COEFFICIENT"] == pytest.approx(0.19)


class TestOutOfBoundsRejected:
    def test_the_live_failure_cl_15(self):
        _publish_bounds()
        _capture(15.17654435, 0.2478556743)
        out = _inject()
        assert "Mission.Design.LIFT_COEFFICIENT" not in out["parameters"]
        assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" not in out["parameters"]

    def test_status_and_rejected_values_recorded(self):
        _publish_bounds()
        _capture(15.17654435, 0.2478556743)
        _inject()
        store = get_design_state().data_store
        assert store["aero_coupling_status"] == "AERO_OUT_OF_BOUNDS"
        assert store["aero_rejected_cl"] == pytest.approx(15.17654435)
        assert "0.05" in store["aero_capture_failure"]

    @pytest.mark.parametrize("cl", [1.5, 15.18, 0.0, -0.3])
    def test_values_outside_published_range(self, cl):
        _publish_bounds()
        _capture(cl, 0.01)
        assert "Mission.Design.LIFT_COEFFICIENT" not in _inject()["parameters"]


class TestNegativeInviscidDragAccepted:
    """The regression that uncoupled everything — SU2's inviscid CD may be < 0."""

    def test_measured_negative_cd_still_couples(self):
        _publish_bounds()
        _capture(0.1802433149, -0.004643166003)
        out = _inject()
        assert out["parameters"]["Mission.Design.LIFT_COEFFICIENT"] == pytest.approx(0.1802433149)
        assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" in out["parameters"]
        assert get_design_state().data_store["aero_coupling_status"] == "injected"

    @pytest.mark.parametrize("cd", [-0.004643, -6.9e-05, 0.0, 0.0018375, 0.0208])
    def test_inviscid_cd_is_not_gated(self, cd):
        _publish_bounds()
        _capture(0.19, cd)
        assert "Mission.Design.LIFT_COEFFICIENT" in _inject()["parameters"]

    def test_drag_factor_stays_inside_declared_range(self):
        """The transform's own clamp is what bounds the drag side."""
        _publish_bounds()
        _capture(0.19, -0.004643)
        f = _inject()["parameters"]["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"]
        assert 0.5 <= f <= 2.0


class TestCaptureRecordsWithoutJudging:
    def test_out_of_bounds_values_are_still_captured(self):
        """Capture is a measurement; the decision happens at injection."""
        _capture(15.18, 0.2479)
        ds = get_design_state()
        assert coupling.get_var(ds, "aero.cl_cruise") == pytest.approx(15.18)
        assert coupling.get_var(ds, "aero.cd_cruise") == pytest.approx(0.2479)


class TestLedgerReportsDistinctMode:
    def test_out_of_bounds_is_its_own_status(self):
        from src.logging.design_ledger import _aero_coupled

        _publish_bounds()
        _capture(15.18, 0.2479)
        _inject()
        out = _aero_coupled({})
        assert out["coupled"] is False
        assert out["aero_coupling_status"] == "AERO_OUT_OF_BOUNDS"
        assert out["detection"] == "data_plane"

    def test_coupled_run_reports_coupled(self):
        from src.logging.design_ledger import _aero_coupled

        _publish_bounds()
        _capture(0.1802433149, -0.004643166003)
        _inject()
        out = _aero_coupled({})
        assert out["coupled"] is True
        assert out["injected_cl"] == pytest.approx(0.1802433149)
