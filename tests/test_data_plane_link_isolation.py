"""Chain-link isolation of the data plane (.claude/BUGS.md B8).

The DesignState is a process-global singleton that ``load_tools_for_agent``
deliberately REUSES — every agent inside one link must share one state, which
is what makes the typed coupling registry work. But nothing cleared it between
chain links, so in a single ``stat_batch_runner`` process link k inherited link
k-1's state:

  * link k-1's SU2 ``aero.cd_cruise`` was injected into link k's mission, which
    reported itself "coupled" while flying drag computed for a DIFFERENT
    geometry — the exact failure the coupling work exists to prevent;
  * ``mission_coupling_error`` stayed silent on link k even when SU2 never ran
    there, because the stale registry looked coupled;
  * a stale ``sessions['tigl']`` was auto-injected into link k's geometry calls
    (``resolve_request`` overrides a provided session_id with the stored one),
    pointing them at link k-1's still-open server session and its OLD geometry.

``reset_design_state()`` is called at every link boundary in
``scripts/stat_batch_runner.py``, before the pre-hook creates the new session.
"""

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools import coupling
from src.tools.data_plane import (
    get_design_state,
    init_data_plane,
    intercept_response,
    mission_coupling_error,
    reset_design_state,
    resolve_request,
)

TOOL_SERVER_MAP = {
    "open_cpacs": "tigl",
    "get_wing_summary": "tigl",
    "create_su2_session": "su2",
    "read_history_csv": "su2",
    "estimate_mass": "mass",
    "create_session": "aviary",
    "set_aircraft_parameters": "aviary",
    "run_simulation": "aviary",
}


@pytest.fixture(autouse=True)
def _clean_data_plane():
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    yield
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


def _simulate_link_with_su2(cl=0.19, cd=0.0141):
    """Populate the state the way a link that actually ran SU2 would."""
    ds = get_design_state()
    intercept_response(
        "read_history_csv",
        json.dumps({"rows": [{"CL": cl, "CD": cd}]}),
    )
    ds.sessions["tigl"] = "LINK0-TIGL-SESSION"
    ds.data_store["mass_wing_kg"] = 7419.8
    return ds


class TestLeakWithoutReset:
    """Characterise the leak the fix removes (guards against regressing)."""

    def test_state_survives_without_reset(self):
        ds0 = _simulate_link_with_su2()
        assert coupling.get_var(ds0, "aero.cd_cruise") == pytest.approx(0.0141)
        # No reset -> same object, same values.
        assert get_design_state() is ds0


class TestResetIsolatesLinks:
    def test_typed_aero_registry_is_cleared(self):
        _simulate_link_with_su2()
        reset_design_state()
        ds1 = get_design_state()
        assert coupling.get_var(ds1, "aero.cl_cruise") is None
        assert coupling.get_var(ds1, "aero.cd_cruise") is None
        assert coupling.registry(ds1) == {}

    def test_new_state_object_is_installed(self):
        ds0 = _simulate_link_with_su2()
        returned = reset_design_state()
        ds1 = get_design_state()
        assert ds1 is not ds0
        assert ds1 is returned
        assert isinstance(ds1, DesignState)

    def test_stale_sessions_are_cleared(self):
        _simulate_link_with_su2()
        reset_design_state()
        assert get_design_state().sessions.get("tigl") is None

    def test_stale_mass_is_cleared(self):
        _simulate_link_with_su2()
        reset_design_state()
        assert get_design_state().data_store.get("mass_wing_kg") is None

    def test_tool_server_map_survives_reset(self):
        """Static topology must NOT be wiped — it is not per-design state."""
        _simulate_link_with_su2()
        reset_design_state()
        # A session-needing tool still resolves to its server, so session
        # injection keeps working after the reset.
        get_design_state().sessions["tigl"] = "NEW-TIGL-SESSION"
        out = resolve_request("get_wing_summary", {"wing_uid": "Wing1"})
        assert out["session_id"] == "NEW-TIGL-SESSION"


class TestStaleAeroNoLongerInjects:
    def test_link1_mission_does_not_inherit_link0_drag(self):
        """The headline bug: link k must not fly link k-1's SU2 drag."""
        _simulate_link_with_su2(cl=0.19, cd=0.0141)

        # Link 0's mission IS coupled — injection fires.
        out0 = resolve_request(
            "set_aircraft_parameters",
            {"session_id": "s0", "parameters": {"Aircraft.Wing.ASPECT_RATIO": 11.0}},
        )
        assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" in out0["parameters"]

        # New link, no SU2 run yet -> must NOT be coupled.
        reset_design_state()
        out1 = resolve_request(
            "set_aircraft_parameters",
            {"session_id": "s1", "parameters": {"Aircraft.Wing.ASPECT_RATIO": 13.5}},
        )
        assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" not in out1["parameters"]
        assert "Mission.Design.LIFT_COEFFICIENT" not in out1["parameters"]
        assert (
            get_design_state().data_store.get("aero_coupling_status")
            == "MISSING_no_su2_aero"
        )

    def test_uncoupled_advisory_fires_again_on_the_new_link(self):
        """The advisory was silenced on links 1..N by the stale registry."""
        _simulate_link_with_su2()
        coupled = resolve_request(
            "set_aircraft_parameters",
            {"session_id": "s0", "parameters": {}},
        )
        assert mission_coupling_error("set_aircraft_parameters", coupled) is None

        reset_design_state()
        uncoupled = resolve_request(
            "set_aircraft_parameters",
            {"session_id": "s1", "parameters": {}},
        )
        err = mission_coupling_error("set_aircraft_parameters", uncoupled)
        assert err is not None
        assert err["error_code"] == "UNCOUPLED_MISSION"

    def test_stale_wing_mass_scaler_does_not_inject(self):
        _simulate_link_with_su2()
        reset_design_state()
        out = resolve_request(
            "set_aircraft_parameters",
            {"session_id": "s1", "parameters": {}},
        )
        assert "Aircraft.Wing.MASS_SCALER" not in out["parameters"]

    def test_fresh_su2_on_the_new_link_couples_normally(self):
        """Reset must not break coupling — only staleness."""
        _simulate_link_with_su2(cd=0.0141)
        reset_design_state()
        intercept_response(
            "read_history_csv", json.dumps({"rows": [{"CL": 0.22, "CD": 0.0155}]})
        )
        out = resolve_request(
            "set_aircraft_parameters",
            {"session_id": "s1", "parameters": {"Aircraft.Wing.ASPECT_RATIO": 13.5}},
        )
        assert out["parameters"]["Mission.Design.LIFT_COEFFICIENT"] == pytest.approx(0.22)
        assert get_design_state().data_store["aero_injected_cd"] == pytest.approx(0.0155)
