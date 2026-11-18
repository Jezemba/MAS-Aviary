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
from tests.cruise_point_helper import stamp

TOOL_SERVER_MAP = {"set_inputs": "pycycle", "run_cycle": "pycycle", "set_aircraft_parameters": "aviary"}


@pytest.fixture(autouse=True)
def _state():
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    yield
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


def _seed(ld=None, mtom=78126.0, fn=None, monkeypatch=None):
    """Drag model decision (Jessica, 2026-11-18): the engine is sized on the trim mass basis and aviary's FLOPS cruise
    lift-to-drag (aviary-mcp get_cruise_point), no longer on SU2's CL/CD."""
    import src.tools.data_plane as _dp

    ds = get_design_state()
    ds.data_store["mass_mtom_kg"] = mtom
    if monkeypatch is not None:
        monkeypatch.setattr(_dp, "trim_mass_basis", lambda: (mtom, "test basis"))
        monkeypatch.setattr(_dp, "carried_engine", lambda: ("/decks/d.deck", 1.3, "carried"))
    if ld is not None:
        ds.data_store.update({"aviary_cruise_point": {"lift_to_drag": ld, "mass_kg": mtom, "cl": 0.56,
                                                      "cd": 0.56 / ld, "engine_scale_used": 1.3,
                                                      "cd_depends_on_engine_scale": False,
                                                      "design_fingerprint": "fp"},
                              "aviary_design_fingerprint": "fp"})
        stamp(ds)    # B338
    if fn is not None:
        coupling.put_var(ds, "prop.fn_lbf", fn)
        # B187: a cycle result the framework sized on this mass and drag.
        ds.data_store["prop_cycle_key"] = _dp._cycle_sizing_key()
    return ds


def test_fn_des_follows_aviarys_cruise_lift_to_drag(monkeypatch):
    _seed(ld=14.1, monkeypatch=monkeypatch)
    resolved = resolve_request("set_inputs", {"session_id": "s", "values": {"fan:PR": 1.685}})
    assert resolved["values"]["Fn_DES"] == pytest.approx(78126.0 * 9.81 / 14.1 / 2 / 4.448 * 1.25)


def test_the_framework_thrust_replaces_a_copied_one(monkeypatch):
    """The sandbox's propulsion agent passed Fn_DES 6336 (copied from design.json); it used to win."""
    _seed(ld=14.1, monkeypatch=monkeypatch)
    resolved = resolve_request("set_inputs", {"session_id": "s", "values": {"Fn_DES": 6336.0}})
    assert resolved["values"]["Fn_DES"] > 7000
    out = json.loads(intercept_response("set_inputs", json.dumps({"success": True})))
    assert out["framework_applied"]["overridden"]["Fn_DES"]["asked"] == 6336.0


def test_without_aviarys_cruise_point_no_thrust_is_assumed_and_an_agent_value_does_not_stand(monkeypatch):
    """Was: the mass-based thrust (L/D 17 assumed) and an agent value stood. Now nothing is assumed: Fn_DES is not
    set and the reason is recorded; run_cycle is refused (tests/test_b306_final_rerun_fixes.py)."""
    _seed(monkeypatch=monkeypatch)
    assert "Fn_DES" not in resolve_request("set_inputs", {"values": {}})["values"]
    assert "Fn_DES" not in resolve_request("set_inputs", {"values": {"Fn_DES": 7000.0}})["values"]
    assert get_design_state().data_store["engine_sizing_error"]


def test_aviary_gets_pycycles_thrust_as_engine_scale(monkeypatch):
    _seed(ld=14.1, fn=11872.0, monkeypatch=monkeypatch)
    resolved = resolve_request("set_aircraft_parameters", {"session_id": "a", "parameters": {
        "Aircraft.Engine.SCALE_FACTOR": 1.0}})
    assert resolved["parameters"]["Aircraft.Engine.SCALE_FACTOR"] == pytest.approx(11872.0 / 5936.0)
    out = json.loads(intercept_response("set_aircraft_parameters", json.dumps({"success": True})))
    assert "Aircraft.Engine.SCALE_FACTOR" in out["framework_applied"]["overridden"]


def test_no_cycle_result_leaves_the_engine_alone(monkeypatch):
    _seed(monkeypatch=monkeypatch)
    resolved = resolve_request("set_aircraft_parameters", {"session_id": "a", "parameters": {"Aircraft.Wing.AREA": 120.0}})
    assert "Aircraft.Engine.SCALE_FACTOR" not in resolved["parameters"]
