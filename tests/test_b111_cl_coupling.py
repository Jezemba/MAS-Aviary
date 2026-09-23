"""B111: the cruise CL is an input to the aero solve, computed from mass x mission x geometry.

geoauth_all8_7960 had the coupling backwards: `aero.cl_cruise` was produced by SU2 at a fixed AOA of
2 deg and consumed by aviary as the design cruise CL. The aircraft needs CL = W/(q S) ~ 0.57; the
solve delivered 0.05-0.22, and at that lift its inviscid CD (~1e-4 of signal under ~1e-2 of mesh
error) came back negative in 5 of 16 links.
"""
import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import coupling


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    state = DesignState()
    monkeypatch.setattr(dp, "_design_state", state)
    yield state


# ---- the number itself ---------------------------------------------------------------------------

def test_the_f25_baseline_needs_about_half():
    cl = coupling.cruise_lift_coefficient(79560, 0.78, 33000, 122.78)
    assert cl == pytest.approx(0.57, abs=0.03)


def test_a_heavier_aircraft_on_the_same_wing_needs_more_lift():
    light = coupling.cruise_lift_coefficient(70000, 0.78, 33000, 122.78)
    heavy = coupling.cruise_lift_coefficient(90000, 0.78, 33000, 122.78)
    assert heavy > light


def test_a_bigger_wing_needs_less_lift_coefficient():
    small = coupling.cruise_lift_coefficient(79560, 0.78, 33000, 100.0)
    big = coupling.cruise_lift_coefficient(79560, 0.78, 33000, 200.0)
    assert big < small


@pytest.mark.parametrize("mass,mach,alt,area", [
    (79560, 0.78, 33000, 10.0),      # a semi-span area typo -> CL 7, refuse to trim to it
    (0, 0.78, 33000, 122.78),
    (79560, 0, 33000, 122.78),
    (79560, 0.78, 33000, 0),
    ("heavy", 0.78, 33000, 122.78),
])
def test_unusable_inputs_give_no_target(mass, mach, alt, area):
    assert coupling.cruise_lift_coefficient(mass, mach, alt, area) is None


def test_it_is_registered_as_an_su2_input():
    spec = coupling.CANONICAL_VARS["aero.target_cl"]
    assert "su2" in spec.consumers


# ---- injection into the solve ---------------------------------------------------------------------

def _ready(state, mass=79560.0, area=122.78):
    state.data_store["mass_mtom_kg"] = mass
    state.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": area, "Aircraft.Wing.ASPECT_RATIO": 9.4}


def test_the_solve_is_trimmed_to_the_aircrafts_lift(fresh):
    _ready(fresh)
    resolved = dp.resolve_request("configure_from_cpacs", {"session_id": "s", "cpacs_file_path": "x.xml"})
    assert resolved["target_cl"] == pytest.approx(0.57, abs=0.03)
    assert fresh.data_store["aero_target_cl"] == resolved["target_cl"]


def test_the_geometrys_wing_is_what_sizes_it(fresh):
    _ready(fresh, area=200.0)
    big = dp.resolve_request("configure_from_cpacs", {"session_id": "s"})["target_cl"]
    _ready(fresh, area=100.0)
    small = dp.resolve_request("configure_from_cpacs", {"session_id": "s"})["target_cl"]
    assert small > big


def test_a_caller_supplied_target_is_not_overridden(fresh):
    _ready(fresh)
    resolved = dp.resolve_request("configure_from_cpacs", {"session_id": "s", "target_cl": 0.42})
    assert resolved["target_cl"] == 0.42


def test_without_mass_nothing_is_injected(fresh):
    fresh.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 122.78, "Aircraft.Wing.ASPECT_RATIO": 9.4}
    assert "target_cl" not in dp.resolve_request("configure_from_cpacs", {"session_id": "s"})


def test_without_a_wing_nothing_is_injected(fresh):
    fresh.data_store["mass_mtom_kg"] = 79560.0
    assert "target_cl" not in dp.resolve_request("configure_from_cpacs", {"session_id": "s"})


def test_the_missions_applied_wing_is_a_fallback(fresh):
    fresh.data_store["mass_mtom_kg"] = 79560.0
    fresh.data_store["design_params_applied"] = {"Aircraft.Wing.AREA": 122.78}
    assert dp.resolve_request("configure_from_cpacs", {"session_id": "s"})["target_cl"] is not None


def test_other_tools_are_not_given_a_target(fresh):
    _ready(fresh)
    assert "target_cl" not in dp.resolve_request("run_su2_solver", {"session_id": "s"})


def test_a_nonsense_wing_area_does_not_trim(fresh):
    _ready(fresh, area=1.0)
    assert "target_cl" not in dp.resolve_request("configure_from_cpacs", {"session_id": "s"})
