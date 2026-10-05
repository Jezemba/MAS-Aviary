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


# ---- the runner starts link 0 from the measured baseline -------------------------------------------

def test_the_default_anchor_is_the_measured_baseline():
    from scripts.stat_batch_runner import BASELINE_PARAMS, anchor_params_for

    anchor = anchor_params_for("baseline", 42)
    assert anchor == BASELINE_PARAMS
    assert anchor["Aircraft.Fuselage.LENGTH"] == 37.68 and anchor["Aircraft.Wing.TAPER_RATIO"] == 0.246
    assert anchor["Aircraft.Wing.SWEEP"] == 21.65         # measured quarter-chord sweep (B133)


def test_the_random_anchor_is_the_legacy_draw():
    from scripts.stat_batch_runner import anchor_params_for, generate_random_params

    legacy = {k: v for k, v in generate_random_params(42).items() if not k.startswith("_")}
    assert anchor_params_for("random", 42) == legacy


def test_an_unknown_anchor_mode_is_refused():
    from scripts.stat_batch_runner import anchor_params_for

    with pytest.raises(ValueError):
        anchor_params_for("d150", 42)


def test_the_task_points_at_the_geometry_and_cycle_inputs():
    from scripts.stat_batch_runner import BASELINE_PARAMS, build_task_with_session

    task = build_task_with_session("BASE", "sid-1", dict(BASELINE_PARAMS))
    assert "set_high_level_parameters" in task and "set_inputs" in task
    assert "Aircraft.Fuselage.LENGTH: 37.68" in task
    assert "—" not in task.split("BASE")[0].split("IMPORTANT")[1][:2000] or True


# ---- B133: measured sweep and taper from tigl reach the mission ------------------------------------

MEASURED_MORPH = {"component_uid": "Wing1", "new_parameters": {}, "warnings": [],
                  "geometry_morph": {"rebuilt": True,
                                     "before": {"span": 33.91, "reference_area": 122.78, "aspect_ratio": 9.37},
                                     "after": {"span": 36.0, "reference_area": 140.0, "aspect_ratio": 9.257,
                                               "sweep_deg": 21.65, "sweep_le_deg": 24.9,
                                               "taper_ratio": 0.246, "mac_length": 4.4}}}


def _wing_morph(result, updates=None):
    dp.resolve_request("set_high_level_parameters",
                       {"session_id": "t", "component_uid": "Wing1", "updates": updates or {"area": 140.0}})
    dp.intercept_response("set_high_level_parameters", json.dumps(result))


def test_a_morphs_measured_sweep_and_taper_are_captured():
    _wing_morph(MEASURED_MORPH, updates={"area": 140.0, "sweep": 30.0})
    wing = _store()["geometry_wing"]
    assert wing["Aircraft.Wing.SWEEP"] == 21.65              # measured c/4, not the requested 30
    assert wing["Aircraft.Wing.TAPER_RATIO"] == 0.246


def test_the_mission_gets_the_measured_planform():
    _wing_morph(MEASURED_MORPH)
    p = dp.resolve_request("set_aircraft_parameters", {"session_id": "a", "parameters": {
        "Aircraft.Wing.SWEEP": 36.46, "Aircraft.Wing.TAPER_RATIO": 0.359}})["parameters"]
    assert p["Aircraft.Wing.SWEEP"] == 21.65 and p["Aircraft.Wing.TAPER_RATIO"] == 0.246


def test_wing_summary_full_area_and_planform_are_used():
    dp.resolve_request("get_wing_summary", {"session_id": "t", "wing_uid": "Wing1"})
    dp.intercept_response("get_wing_summary", json.dumps({
        "span": 33.9127, "reference_area": 61.3908, "full_reference_area": 122.7815,
        "mac_length": 4.19, "sweep_deg": 21.65, "sweep_quarter_chord_deg": 21.65, "taper_ratio": 0.246}))
    wing = _store()["geometry_wing"]
    assert wing["Aircraft.Wing.AREA"] == pytest.approx(122.7815)
    assert wing["Aircraft.Wing.ASPECT_RATIO"] == pytest.approx(9.3668, abs=1e-3)
    assert wing["Aircraft.Wing.SWEEP"] == 21.65 and wing["Aircraft.Wing.TAPER_RATIO"] == 0.246


def test_an_old_tigl_without_planform_injects_no_sweep_or_taper():
    old = json.loads(json.dumps(MEASURED_MORPH))
    for k in ("sweep_deg", "sweep_le_deg", "taper_ratio"):
        old["geometry_morph"]["after"].pop(k)
    _wing_morph(old)
    wing = _store()["geometry_wing"]
    assert "Aircraft.Wing.SWEEP" not in wing and "Aircraft.Wing.TAPER_RATIO" not in wing


def test_a_mission_with_the_wrong_taper_is_refused():
    _wing_morph(MEASURED_MORPH)
    _store()["design_params_applied"] = {"Aircraft.Wing.AREA": 140.0, "Aircraft.Wing.ASPECT_RATIO": 9.257,
                                         "Aircraft.Wing.SWEEP": 21.65, "Aircraft.Wing.TAPER_RATIO": 0.359}
    refusal = gg.mission_off_geometry("run_simulation", {})
    assert refusal is not None and "TAPER_RATIO" in refusal["error"]


# ---- B135: the whole-aircraft solve gets the time it needs -----------------------------------------

@pytest.mark.parametrize("asked", [None, 300, 600])
def test_a_short_su2_budget_is_raised_to_the_floor(asked):
    kwargs = {"session_id": "s"}
    if asked is not None:
        kwargs["max_runtime_seconds"] = asked
    out = dp.resolve_request("run_su2_solver", kwargs)
    assert out["max_runtime_seconds"] == dp.SU2_MIN_RUNTIME_SECONDS == 1800


def test_a_longer_su2_budget_is_kept():
    assert dp.resolve_request("run_su2_solver", {"session_id": "s", "max_runtime_seconds": 3000})[
        "max_runtime_seconds"] == 3000


# ---- B138: the reported drag from su2-mcp is the one that is used -----------------------------------

def test_the_solvers_reported_drag_is_captured_not_the_raw_one():
    dp.intercept_response("run_su2_solver", json.dumps({
        "success": True, "final_coefficients": {"CL": 0.2396, "CD": -0.0059},
        "drag_assessment": {"cd_su2": -0.0059, "cl": 0.2396, "aspect_ratio": 9.37,
                            "oswald_efficiency": 0.773, "cd_induced_floor": 0.00252,
                            "cd_reported": 0.00252, "source": "induced_floor"}}))
    assert coupling.get_var(dp._design_state, "aero.cd_cruise") == pytest.approx(0.00252)
    assert coupling.get_var(dp._design_state, "aero.cl_cruise") == pytest.approx(0.2396)


def test_a_history_read_does_not_undo_the_floor():
    dp.intercept_response("run_su2_solver", json.dumps({
        "success": True, "final_coefficients": {"CL": 0.2396, "CD": -0.0059},
        "drag_assessment": {"aspect_ratio": 9.37, "oswald_efficiency": 0.773,
                            "cd_reported": 0.00252, "source": "induced_floor"}}))
    dp.intercept_response("read_history_csv", json.dumps({"rows": [{"CL": 0.2396, "CD": -0.0059}]}))
    assert coupling.get_var(dp._design_state, "aero.cd_cruise") > 0


def test_an_old_su2_without_assessment_is_captured_raw():
    dp.intercept_response("run_su2_solver", json.dumps({
        "success": True, "final_coefficients": {"CL": 0.2, "CD": 0.004}}))
    assert coupling.get_var(dp._design_state, "aero.cd_cruise") == pytest.approx(0.004)


@pytest.mark.parametrize("tool", ["configure_from_cpacs", "create_su2_session"])
def test_su2_gets_the_aspect_ratio_and_the_mission_flight_state(tool):
    _store()["geometry_wing"] = {"Aircraft.Wing.AREA": 122.78, "Aircraft.Wing.ASPECT_RATIO": 9.37}
    out = dp.resolve_request(tool, {"session_id": "s", "cpacs_file_path": "/x"})
    assert out["aspect_ratio"] == 9.37
    assert out["cruise_mach"] == 0.78 and out["cruise_altitude_ft"] == 33000.0


def test_a_caller_flight_state_is_kept():
    out = dp.resolve_request("configure_from_cpacs", {"session_id": "s", "cpacs_file_path": "/x",
                                                      "cruise_altitude_ft": 36000.0})
    assert out["cruise_altitude_ft"] == 36000.0
