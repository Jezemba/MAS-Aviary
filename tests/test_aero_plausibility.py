"""Captured SU2 aero must be physically plausible before it is injected.

The failure this prevents (live, sweep run 1/16, 2026-08-03): an SU2 config with
no REF_AREA fell back to SU2's default of 1.0 m^2 instead of the wing's ~192
m^2, inflating every coefficient by ~190x. The captured CL=15.18 was injected as
Mission.Design.LIFT_COEFFICIENT, aviary's Newton solver tried to trim to it and
diverged, and the run reported

    GTOW 310,918 kg  (real ~85,700)      fuel 208,551 kg  (real ~12,100)

while the ledger recorded status "coupled". The mass capture has had a sanity
bracket since the start; aero had none.
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

TOOL_SERVER_MAP = {"read_history_csv": "su2", "set_aircraft_parameters": "aviary"}


@pytest.fixture(autouse=True)
def _clean():
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    yield
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


def _capture(cl, cd):
    intercept_response("read_history_csv", json.dumps({"columns": ["CL", "CD"],
                                                       "rows": [{"CL": cl, "CD": cd}]}))
    return get_design_state()


class TestImplausibleRejected:
    def test_the_live_failure_cl_15(self):
        """THE regression: CL=15.18 from a missing REF_AREA must not inject."""
        ds = _capture(15.17654435, 0.2478556743)
        assert coupling.get_var(ds, "aero.cl_cruise") is None
        assert ds.data_store["aero_coupling_status"] == "AERO_IMPLAUSIBLE"
        assert ds.data_store["aero_rejected_cl"] == pytest.approx(15.17654435)

    def test_rejected_aero_does_not_reach_aviary(self):
        _capture(15.17654435, 0.2478556743)
        out = resolve_request("set_aircraft_parameters",
                              {"session_id": "s", "parameters": {}})
        assert "Mission.Design.LIFT_COEFFICIENT" not in out["parameters"]
        assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" not in out["parameters"]

    @pytest.mark.parametrize("cl,cd", [
        (15.18, 0.248),    # missing REF_AREA
        (3.5, 0.02),       # above max high-lift
        (-1.0, 0.02),      # deeply negative lift
        (0.5, 0.0),        # zero drag is unphysical
        (0.5, -0.01),      # negative drag
        (0.5, 2.0),        # absurd drag
    ])
    def test_bracket_bounds(self, cl, cd):
        ds = _capture(cl, cd)
        assert coupling.get_var(ds, "aero.cl_cruise") is None
        assert ds.data_store["aero_coupling_status"] == "AERO_IMPLAUSIBLE"


class TestPlausibleAccepted:
    @pytest.mark.parametrize("cl,cd", [
        (0.09221, 0.0018375),   # real coarse-Euler values from a good run
        (0.1919762, 0.00253683),
        (0.5, 0.0208),          # aviary-default-ish cruise point
        (2.5, 0.15),            # high-lift, still physical
        (-0.2, 0.03),           # small negative lift
    ])
    def test_real_values_still_capture(self, cl, cd):
        ds = _capture(cl, cd)
        assert coupling.get_var(ds, "aero.cl_cruise") == pytest.approx(cl)
        assert coupling.get_var(ds, "aero.cd_cruise") == pytest.approx(cd)
        assert ds.data_store.get("aero_coupling_status") != "AERO_IMPLAUSIBLE"

    def test_good_aero_still_injects(self):
        _capture(0.19, 0.0141)
        out = resolve_request("set_aircraft_parameters",
                              {"session_id": "s", "parameters": {"Aircraft.Wing.ASPECT_RATIO": 11.0}})
        assert out["parameters"]["Mission.Design.LIFT_COEFFICIENT"] == pytest.approx(0.19)
        assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" in out["parameters"]


class TestLedgerReportsRejection:
    def test_distinct_status_in_ledger(self):
        from src.logging.design_ledger import _aero_coupled

        _capture(15.18, 0.248)
        out = _aero_coupled({})
        assert out["coupled"] is False
        assert out["aero_coupling_status"] == "AERO_IMPLAUSIBLE"
        assert out["detection"] == "data_plane"
        assert "implausible" in (out["aero_capture_failure"] or "")
