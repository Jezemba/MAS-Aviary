"""Design-space coupling fixes (2026-10-03): B129, B130, B131, B132 (pycycle part), B133.

Background: `Avion/.claude/planning/true_cycle_inputs_2026-10-03.md`. The section 11 table names
which values are set in tigl / pycycle and which the framework must pass on from the geometry.

Stubbed: no MCP server, no model, no .env.
"""

import json

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import coupling
from src.tools import geometry_guard as gg

FUSELAGE_MORPH = {"component_uid": "Fuselage1", "new_parameters": {}, "warnings": [],
                  "geometry_morph": {"rebuilt": True,
                                     "before": {"length": 37.684, "width": 3.960, "height": 4.149},
                                     "after": {"length": 40.000, "width": 4.100, "height": 4.300}}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("AVION_DESIGN_AUTHORITY", raising=False)
    monkeypatch.delenv("AVION_COARSE_MESH", raising=False)
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_tool_server_map", {"set_inputs": "pycycle", "run_cycle": "pycycle",
                                                 "get_outputs": "pycycle"})
    dp._call_ctx.__dict__.clear()
    yield
    dp._call_ctx.__dict__.clear()


def _store():
    return dp._design_state.data_store


# ---- B130: the whole aircraft is always meshed ------------------------------------------------------

def test_a_component_uid_is_dropped_so_the_whole_aircraft_is_meshed():
    out = dp.resolve_request("generate_volume_mesh", {"session_id": "t", "component_uid": "Wing1"})
    assert "component_uid" not in out
    assert _store()["_mesh_component_dropped"] == "Wing1"


def test_a_mesh_request_without_component_uid_is_left_alone():
    out = dp.resolve_request("generate_volume_mesh", {"session_id": "t"})
    assert "component_uid" not in out
    assert "_mesh_component_dropped" not in _store()


# ---- B131: SU2 normalises by the FULL wing --------------------------------------------------------

@pytest.mark.parametrize("tool", ["configure_from_cpacs", "create_su2_session"])
def test_su2_gets_twice_the_semi_span_reference_area(tool):
    coupling.put_var(dp._design_state, "geom.reference_area", 61.39076274891849, source_tool="t")
    out = dp.resolve_request(tool, {"session_id": "s", "cpacs_file_path": "/tmp/a.xml"})
    assert out["ref_area"] == pytest.approx(122.7815, abs=1e-3)      # D150 full wing


def test_a_caller_supplied_reference_area_still_wins():
    coupling.put_var(dp._design_state, "geom.reference_area", 61.39, source_tool="t")
    out = dp.resolve_request("configure_from_cpacs", {"session_id": "s", "cpacs_file_path": "/x",
                                                      "ref_area": 130.1})
    assert out["ref_area"] == 130.1


# ---- B129: the cycle is captured from get_outputs too ---------------------------------------------

def test_get_outputs_values_are_captured_as_the_engine_cycle():
    dp.intercept_response("get_outputs", json.dumps(
        {"values": {"perf.TSFC": 0.5060009245230022, "perf.Fn": 4999.9999999999945}, "missing": []}))
    assert coupling.get_var(dp._design_state, "prop.sfc_cruise") == pytest.approx(0.506, abs=1e-3)
    assert coupling.get_var(dp._design_state, "prop.fn_lbf") == pytest.approx(5000.0, abs=1e-3)


def test_run_cycle_capture_is_unchanged():
    dp.intercept_response("run_cycle", json.dumps(
        {"success": True, "outputs": {"perf.TSFC": 0.5442, "perf.Fn": 6336.0}}))
    assert coupling.get_var(dp._design_state, "prop.sfc_cruise") == pytest.approx(0.5442)


def test_get_outputs_without_the_cycle_names_captures_nothing():
    dp.intercept_response("get_outputs", json.dumps({"values": {"balance.W": 262.0}, "missing": []}))
    assert coupling.get_var(dp._design_state, "prop.sfc_cruise") is None


# ---- B132 (pycycle part): the engine is sized at the mission cruise -------------------------------

def test_set_inputs_gets_the_mission_flight_condition():
    out = dp.resolve_request("set_inputs", {"session_id": "p", "values": {"splitter.BPR": 8.0}})
    assert out["values"]["fc.alt"] == 33000.0
    assert out["values"]["fc.MN"] == 0.78
    assert out["values"]["splitter.BPR"] == 8.0


def test_an_explicit_flight_condition_is_kept():
    out = dp.resolve_request("set_inputs", {"session_id": "p", "values": {"fc.alt": 36000.0}})
    assert out["values"]["fc.alt"] == 36000.0
    assert out["values"]["fc.MN"] == 0.78


def test_json_string_values_are_handled():
    out = dp.resolve_request("set_inputs", {"session_id": "p", "values": json.dumps({"fan.PR": 1.5})})
    assert out["values"]["fc.MN"] == 0.78 and out["values"]["fan.PR"] == 1.5


# ---- B133: the fuselage comes from the measured geometry ------------------------------------------

def _fuselage_morph():
    dp.resolve_request("set_high_level_parameters",
                       {"session_id": "t", "component_uid": "Fuselage1", "updates": {"length": 40.0}})
    dp.intercept_response("set_high_level_parameters", json.dumps(FUSELAGE_MORPH))


def test_a_fuselage_morph_is_captured_from_its_measurement():
    _fuselage_morph()
    fus = _store()["geometry_fuselage"]
    assert fus["Aircraft.Fuselage.LENGTH"] == 40.0
    assert fus["Aircraft.Fuselage.MAX_HEIGHT"] == 4.3
    assert fus["Aircraft.Fuselage.MAX_WIDTH"] == 4.1
    assert "geometry_wing" not in _store()          # a fuselage morph is not a wing


def test_the_mission_gets_the_reshaped_fuselage_whatever_it_asked_for():
    _fuselage_morph()
    out = dp.resolve_request("set_aircraft_parameters", {"session_id": "a", "parameters": {
        "Aircraft.Fuselage.LENGTH": 30.07, "Aircraft.Engine.SCALE_FACTOR": 1.2}})
    p = out["parameters"]
    assert p["Aircraft.Fuselage.LENGTH"] == 40.0
    assert p["Aircraft.Fuselage.MAX_HEIGHT"] == 4.3 and p["Aircraft.Fuselage.MAX_WIDTH"] == 4.1
    assert p["Aircraft.Engine.SCALE_FACTOR"] == 1.2              # non-geometric: the mission's own


def test_without_a_fuselage_morph_the_mission_keeps_its_fuselage():
    out = dp.resolve_request("set_aircraft_parameters", {"session_id": "a", "parameters": {
        "Aircraft.Fuselage.LENGTH": 37.68}})
    assert out["parameters"]["Aircraft.Fuselage.LENGTH"] == 37.68


def test_flying_a_different_fuselage_is_refused():
    _fuselage_morph()
    _store()["design_params_applied"] = {"Aircraft.Fuselage.LENGTH": 30.07,
                                         "Aircraft.Fuselage.MAX_HEIGHT": 4.3,
                                         "Aircraft.Fuselage.MAX_WIDTH": 4.1}
    refusal = gg.mission_off_geometry("run_simulation", {"session_id": "a"})
    assert refusal["error_code"] == "GEOMETRY_NOT_APPLIED" and "LENGTH" in refusal["error"]


def test_flying_the_reshaped_fuselage_proceeds():
    _fuselage_morph()
    _store()["design_params_applied"] = {"Aircraft.Fuselage.LENGTH": 40.0,
                                         "Aircraft.Fuselage.MAX_HEIGHT": 4.3,
                                         "Aircraft.Fuselage.MAX_WIDTH": 4.1}
    assert gg.mission_off_geometry("run_simulation", {}) is None
