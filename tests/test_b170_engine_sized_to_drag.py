"""B170: the engine is sized to the design's drag and reaches aviary (Jessica, 2026-10-10).

Sandbox 20261010-091649: SU2 drag gave aviary a drag factor of 1.714 while aviary flew the bench engine
(SCALE_FACTOR 1.0) -> ZERO_FUEL. Replayed on aviary: drag 1.2 collapses with engine 1.0 and flies with 1.5.
pycycle's thrust was captured (prop.fn_lbf) but never passed on, and Fn_DES assumed L/D 17.
"""

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools import coupling
from src.tools.data_plane import get_design_state, init_data_plane, intercept_response, resolve_request

TOOL_SERVER_MAP = {"set_inputs": "pycycle", "run_cycle": "pycycle", "set_aircraft_parameters": "aviary"}


@pytest.fixture(autouse=True)
def _state():
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    yield
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


def _seed(cl=None, cd=None, mtom=78126.0, fn=None):
    ds = get_design_state()
    ds.data_store["mass_mtom_kg"] = mtom
    if cl is not None:
        coupling.put_var(ds, "aero.cl_cruise", cl)
        coupling.put_var(ds, "aero.cd_cruise", cd)
    if fn is not None:
        coupling.put_var(ds, "prop.fn_lbf", fn)
        # B187: a cycle result the framework sized on this mass and drag.
        from src.tools import data_plane as _dp

        ds.data_store["prop_cycle_key"] = _dp._cycle_sizing_key()
    return ds


def test_the_old_coefficient_is_the_l_over_d_17_case():
    """FN_DES_PER_KG_LBF = g / 17 / 2 / 4.448 * 1.25: the coupled formula at L/D 17 reproduces it."""
    assert coupling.fn_des_from_drag(73000, 17.0, 1.0) == pytest.approx(coupling.mtom_to_pycycle_fn_des(73000), rel=1e-3)


def test_bench_engine_scale_one_at_the_old_assumption():
    assert coupling.fn_to_aviary_engine_scale(coupling.mtom_to_pycycle_fn_des(73000)) == pytest.approx(1.0, abs=0.01)


def test_fn_des_follows_the_measured_drag():
    _seed(cl=0.5563, cd=0.0395)
    resolved = resolve_request("set_inputs", {"session_id": "s", "values": {"fan:PR": 1.685}})
    fn = resolved["values"]["Fn_DES"]
    cd_total = coupling.total_cruise_cd(0.0395)
    # the framework uses the cruise Reynolds number, this the nominal one: within 1%
    assert fn == pytest.approx(78126.0 * 9.81 / (0.5563 / cd_total) / 2 / 4.448 * 1.25, rel=1e-2)
    assert fn > coupling.mtom_to_pycycle_fn_des(78126.0) * 1.5   # L/D ~9.5, not 17


def test_the_framework_thrust_replaces_a_copied_one():
    """The sandbox's propulsion agent passed Fn_DES 6336 (copied from design.json); it used to win."""
    _seed(cl=0.5563, cd=0.0395)
    resolved = resolve_request("set_inputs", {"session_id": "s", "values": {"Fn_DES": 6336.0}})
    assert resolved["values"]["Fn_DES"] > 9000
    out = json.loads(intercept_response("set_inputs", json.dumps({"success": True})))
    assert out["framework_applied"]["overridden"]["Fn_DES"]["asked"] == 6336.0


def test_without_aero_the_mass_based_thrust_and_an_agent_value_stand():
    _seed()
    assert resolve_request("set_inputs", {"values": {}})["values"]["Fn_DES"] == pytest.approx(
        coupling.mtom_to_pycycle_fn_des(78126.0))
    assert resolve_request("set_inputs", {"values": {"Fn_DES": 7000.0}})["values"]["Fn_DES"] == 7000.0


def test_aviary_gets_pycycles_thrust_as_engine_scale():
    _seed(fn=11872.0)
    resolved = resolve_request("set_aircraft_parameters", {"session_id": "a", "parameters": {
        "Aircraft.Engine.SCALE_FACTOR": 1.0}})
    assert resolved["parameters"]["Aircraft.Engine.SCALE_FACTOR"] == pytest.approx(11872.0 / 5936.0)
    out = json.loads(intercept_response("set_aircraft_parameters", json.dumps({"success": True})))
    assert "Aircraft.Engine.SCALE_FACTOR" in out["framework_applied"]["overridden"]


def test_no_cycle_result_leaves_the_engine_alone():
    _seed()
    resolved = resolve_request("set_aircraft_parameters", {"session_id": "a", "parameters": {"Aircraft.Wing.AREA": 120.0}})
    assert "Aircraft.Engine.SCALE_FACTOR" not in resolved["parameters"]
