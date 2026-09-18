"""B99, B102 and the zero-fuel floor -- three ways a reported number stopped describing the aircraft.

B99   horizon10 sequential link 1: the wing was morphed to 200.01 m^2 (MAC 5.10) and meshed, then the data
      plane injected ref_area 61.39 / ref_length 4.192 into the SU2 session -- the BASELINE's semi-span area
      and MAC, captured once from get_wing_summary and never refreshed after the morph. CL was overstated by
      at least 63%, and it became the mission's LIFT_COEFFICIENT.
B102  validate15: five run_simulation calls, all refused, no mission flown -- yet result.json recorded
      fuel_burned_kg 15000.0 (the constraint bound, echoed in the task text) and the next link was told
      "constraint <= 15000: PASS".
Floor _is_zero_fuel tested `== 0.0`, so 1.5696e-05 kg, 0.00093 kg and 1.05e-05 kg all passed as results.

Stubbed: no MCP server, no model, no .env.
"""

import json
import types

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import coupling
from scripts.stat_batch_runner import (ZERO_FUEL_FLOOR_KG, _is_zero_fuel, build_quick_metrics,
                                       measured_fuel_from_kb, reconcile_measured_fuel)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    yield


# ---- B99: the injected SU2 references follow the morph ----------------------------------------

def _baseline_summary():
    dp._capture_geometry_ref("get_wing_summary", {"reference_area": 61.39076274891849,
                                                   "mac_length": 4.192320937872386,
                                                   "wetted_area": 111.58})


def _morph(rebuilt=True):
    return {"component_uid": "Wing1", "geometry_morph": {
        "rebuilt": rebuilt,
        "before": {"reference_area": 122.78152549783698, "mac_length": 4.192320937872386},
        "after": {"reference_area": 200.0119958070674, "mac_length": 5.104453955260532}}}


def test_a_morph_refreshes_the_reference_area_and_mac():
    _baseline_summary()
    assert coupling.get_var(dp._design_state, "geom.reference_area") == pytest.approx(61.3908, abs=1e-3)
    dp._capture_geometry_ref("set_high_level_parameters", _morph())
    # semi-span convention kept: the morph reports the FULL area, the registry stores half of it
    assert coupling.get_var(dp._design_state, "geom.reference_area") == pytest.approx(100.006, abs=1e-3)
    assert coupling.get_var(dp._design_state, "geom.mac_length") == pytest.approx(5.1045, abs=1e-3)


def test_the_semi_span_convention_matches_the_baseline_exactly():
    """get_wing_summary's 61.39 is exactly half the morph's own 'before' area of 122.78."""
    before = _morph()["geometry_morph"]["before"]["reference_area"]
    assert before / 2.0 == pytest.approx(61.39076274891849, rel=1e-9)


def test_a_morph_that_did_not_rebuild_changes_nothing():
    _baseline_summary()
    dp._capture_geometry_ref("set_high_level_parameters", _morph(rebuilt=False))
    assert coupling.get_var(dp._design_state, "geom.reference_area") == pytest.approx(61.3908, abs=1e-3)


# ---- zero-fuel floor ------------------------------------------------------------------------------

@pytest.mark.parametrize("fuel", [0.0, 1.5696105269368272e-05, 0.0009324991396196458, 1.0512294438735407e-05])
def test_near_zero_artefacts_are_zero_fuel(fuel):
    assert _is_zero_fuel(types.SimpleNamespace(eval_classification={"fuel_burned_kg": fuel}))


@pytest.mark.parametrize("fuel", [9020.14, 14878.25, 17409.27])
def test_real_fuel_burns_are_not(fuel):
    assert not _is_zero_fuel(types.SimpleNamespace(eval_classification={"fuel_burned_kg": fuel}))


def test_a_missing_figure_is_not_mistaken_for_zero():
    assert not _is_zero_fuel(types.SimpleNamespace(eval_classification={}))


def test_the_run_time_guard_uses_the_same_floor():
    payload = {"success": True, "summary": {"fuel_burned_kg": 1.0512294438735407e-05}}
    dp._flag_zero_fuel("run_simulation", payload)
    assert payload["error_code"] == "ZERO_FUEL"


# ---- B102: only a flown mission produces a fuel figure --------------------------------------------

def _kb(tmp_path, entries):
    path = tmp_path / "knowledge_base.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries))
    return path


def _sim(status, fuel=None):
    return {"tool": "run_simulation", "status": status,
            "outputs": {"summary": {"fuel_burned_kg": fuel}} if fuel is not None else {}}


def test_refused_missions_measure_nothing(tmp_path):
    kb = _kb(tmp_path, [_sim("refused")] * 5)
    assert measured_fuel_from_kb(kb) is None


def test_the_last_successful_mission_is_the_measurement(tmp_path):
    kb = _kb(tmp_path, [_sim("success", 18000.0), _sim("refused"), _sim("success", 17409.27)])
    assert measured_fuel_from_kb(kb) == pytest.approx(17409.27)


def test_the_validate15_case_is_reported_as_not_measured(tmp_path):
    """Five refused missions, classifier says 15000.0 -- the constraint bound."""
    kb = _kb(tmp_path, [_sim("refused")] * 5)
    result = types.SimpleNamespace(eval_classification={"fuel_burned_kg": 15000.0})
    result_dict = {"eval_classification": {"fuel_burned_kg": 15000.0}}
    assert reconcile_measured_fuel(result, result_dict, kb) == "not_measured"
    assert result.eval_classification["fuel_burned_kg"] is None
    assert result_dict["eval_classification"]["fuel_burned_kg"] is None
    assert result_dict["fuel_classifier_value"] == 15000.0


def test_a_classifier_that_agrees_with_the_mission_is_kept(tmp_path):
    kb = _kb(tmp_path, [_sim("success", 17409.269138300027)])
    result = types.SimpleNamespace(eval_classification={"fuel_burned_kg": 17409.269138300027})
    result_dict = {"eval_classification": {"fuel_burned_kg": 17409.269138300027}}
    assert reconcile_measured_fuel(result, result_dict, kb) == "run_simulation"
    assert result_dict["eval_classification"]["fuel_burned_kg"] == pytest.approx(17409.27, abs=0.01)
    assert "fuel_classifier_value" not in result_dict


def test_a_classifier_that_disagrees_is_overruled_by_the_mission(tmp_path):
    kb = _kb(tmp_path, [_sim("success", 16645.6)])
    result = types.SimpleNamespace(eval_classification={"fuel_burned_kg": 15000.0})
    result_dict = {"eval_classification": {"fuel_burned_kg": 15000.0}}
    reconcile_measured_fuel(result, result_dict, kb)
    assert result.eval_classification["fuel_burned_kg"] == pytest.approx(16645.6)
    assert result_dict["fuel_classifier_value"] == 15000.0


def test_a_near_zero_mission_is_not_a_result(tmp_path):
    kb = _kb(tmp_path, [_sim("success", 1.0512294438735407e-05)])
    result = types.SimpleNamespace(eval_classification={"fuel_burned_kg": 1.0512294438735407e-05})
    result_dict = {"eval_classification": {"fuel_burned_kg": 1.0512294438735407e-05}}
    assert reconcile_measured_fuel(result, result_dict, kb) == "zero_fuel"
    assert result_dict["eval_classification"]["fuel_burned_kg"] is None


def test_the_next_link_is_never_given_a_verdict_on_an_unmeasured_figure():
    text = build_quick_metrics({"fuel_burned_kg": None, "gtow_kg": 83137.1})
    fuel_line = next(l for l in text.splitlines() if "fuel_burned_kg" in l)
    assert "not measured" in fuel_line
    assert "PASS" not in fuel_line and "FAIL" not in fuel_line


def test_a_measured_figure_still_gets_its_verdict():
    text = build_quick_metrics({"fuel_burned_kg": 17409.3})
    assert "constraint <= 15000: FAIL" in text


def test_the_floor_is_far_below_any_real_fuel_burn():
    assert ZERO_FUEL_FLOOR_KG < 9020.14 / 10


# ---- B105: a morph that did not rebuild must not count as a morph ---------------------------------
#
# horizon10 links 3 and 8: set_high_level_parameters(updates={'sweep_deg': 20.0, 'taper_ratio': 0.3})
# returned success with no geometry_morph -- the keys are not ones the morph reads -- and the wing was
# untouched. The geometry epoch advanced anyway, so STALE_GEOMETRY let the baseline be meshed. Those are
# the two worst links in the run, both 23,193.45 kg, identical to validate4's baseline to 14 figures.

from src.tools import duplicate_guard as dg
from src.tools import knowledge_base as kbm


def _guard_state_with_session(sid="tigl-1"):
    kbm.configure_run(None, 0, 1, None)
    gs = dg._state()
    gs["geometry_epoch"][sid] = 0
    return gs


def _observe(result):
    dg.observe("set_high_level_parameters", {"session_id": "tigl-1"}, json.dumps(result), "success", None)


def test_a_no_op_morph_does_not_advance_the_epoch():
    gs = _guard_state_with_session()
    _observe({"component_uid": "Wing1", "new_parameters": {"sweep_deg": 20.0, "taper_ratio": 0.3},
              "warnings": []})                                   # no geometry_morph: nothing rebuilt
    assert gs["geometry_epoch"]["tigl-1"] == 0


def test_a_reported_non_rebuild_does_not_advance_the_epoch():
    gs = _guard_state_with_session()
    _observe({"component_uid": "Wing1", "geometry_morph": {"rebuilt": False}})
    assert gs["geometry_epoch"]["tigl-1"] == 0


def test_a_real_morph_advances_the_epoch():
    gs = _guard_state_with_session()
    _observe({"component_uid": "Wing1", "geometry_morph": {"rebuilt": True,
                                                           "after": {"reference_area": 200.01}}})
    assert gs["geometry_epoch"]["tigl-1"] == 1
