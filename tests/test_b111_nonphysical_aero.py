"""B111: SU2 aero no wing could produce must not reach the mission, clamped or not."""
import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import aero_guard, coupling


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("AVION_REQUIRE_PHYSICAL_AERO", raising=False)
    state = DesignState()
    state.sessions["su2"] = "su2-session"
    monkeypatch.setattr(dp, "_design_state", state)
    yield state


def _aero(cl, cd):
    dp._design_state.data_store["aero_cl_cruise"] = cl
    dp._design_state.data_store["aero_cd_cruise"] = cd
    try:
        coupling.put_var(dp._design_state, "aero.cl_cruise", cl, source_tool="read_history_csv")
        coupling.put_var(dp._design_state, "aero.cd_cruise", cd, source_tool="read_history_csv")
    except Exception:
        pass


@pytest.mark.parametrize("cl,cd", [
    (0.0497, -0.0168),     # geoauth orchestrated iterative L1/L2, staged L2 -- flew drag factor 0.5
    (0.1456, -0.006),      # geoauth sequential iterative L1
    (1.4247, 0.0245),      # geoauth sequential staged L2 -- flew drag factor 0.5
])
def test_the_observed_nonphysical_results_are_refused(cl, cd):
    _aero(cl, cd)
    refusal = aero_guard.aero_nonphysical("run_simulation", {})
    assert refusal["error_code"] == "AERO_NONPHYSICAL"
    assert "configure_from_cpacs(session_id='su2-session'" in refusal["error"]


@pytest.mark.parametrize("cl,cd", [(0.2217, 0.0038), (0.2112, 0.0028), (0.3611, 0.0062), (0.2129, 0.0064)])
def test_the_observed_physical_results_fly(cl, cd):
    _aero(cl, cd)
    assert aero_guard.aero_nonphysical("run_simulation", {}) is None


def test_no_aero_is_left_to_the_coupling_refusal():
    assert aero_guard.aero_nonphysical("run_simulation", {}) is None


def test_only_the_mission_is_refused():
    _aero(0.0497, -0.0168)
    assert aero_guard.aero_nonphysical("set_aircraft_parameters", {}) is None


def test_the_switch_turns_it_off(monkeypatch):
    monkeypatch.setenv("AVION_REQUIRE_PHYSICAL_AERO", "0")
    _aero(0.0497, -0.0168)
    assert aero_guard.aero_nonphysical("run_simulation", {}) is None


def test_the_declared_cl_bound_holds_without_get_design_space():
    assert dp._bounds_for("Mission.Design.LIFT_COEFFICIENT") == (0.05, 1.0)


def test_a_published_bound_still_wins():
    dp._design_state.data_store["param_bounds"] = {"Mission.Design.LIFT_COEFFICIENT": (0.1, 0.9)}
    assert dp._bounds_for("Mission.Design.LIFT_COEFFICIENT") == (0.1, 0.9)


def test_an_out_of_range_cl_is_not_injected_even_without_get_design_space():
    _aero(1.4247, 0.0245)
    resolved = {"session_id": "a", "parameters": {"Aircraft.Wing.ASPECT_RATIO": 10.0}}
    dp._inject_phase_h_aero(resolved)
    assert "Mission.Design.LIFT_COEFFICIENT" not in resolved["parameters"]
    assert dp._design_state.data_store["aero_coupling_status"] == "AERO_OUT_OF_BOUNDS"
