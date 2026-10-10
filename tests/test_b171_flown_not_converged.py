"""B171: a mission that flew but whose optimiser did not converge is a result with a warning.

aipexws8 v2sc2 link 1 (2026-10-10): fuel 22,998.6 kg, GTOW 102,187.6 kg, "converged": false, residuals
726 kg / 88 kg / 84 kg / 22 nm. It was scored sim_fail and the integrator was told to RETRY. Did-not-fly
(no fuel burned) is still a failure. No cutoff on the residuals: the agents judge them.
"""

import json

import pytest

from src.coordination.design_state import DesignState
from src.logging.eval_classifier import classify_aviary_eval
from src.logging.org_theory_metrics import _staged_pipeline_metrics
from src.tools.data_plane import init_data_plane, intercept_response

FLOWN_NOT_CONVERGED = {
    "success": True, "session_id": "s", "converged": False, "exit_code": 1, "iterations": 209,
    "summary": {"fuel_burned_kg": 22998.6, "gtow_kg": 102187.6, "wing_mass_kg": 9337.7},
    "failure": {"optimizer_message": "Positive directional derivative for linesearch", "violated_constraints": [
        {"constraint": "traj.climb.collocation_constraint.defects:mass", "violation": 726.38},
        {"constraint": "mission:constraints:mass_residual", "violation": 84.36}]},
}


@pytest.fixture(autouse=True)
def _state():
    init_data_plane(DesignState(), {"run_simulation": "aviary"})
    yield
    init_data_plane(DesignState(), {"run_simulation": "aviary"})


def test_the_tool_result_carries_a_warning_not_an_error():
    out = json.loads(intercept_response("run_simulation", json.dumps(FLOWN_NOT_CONVERGED)))
    assert out["flown"] is True and "error_code" not in out and out.get("valid") is not False
    warning = out["convergence_warning"]
    assert warning["note"].startswith("WARNING, not a failure")
    assert warning["optimizer_message"] == "Positive directional derivative for linesearch"
    assert [r["violation"] for r in warning["residuals"]] == [726.38, 84.36]


def test_no_fuel_is_still_did_not_fly():
    payload = dict(FLOWN_NOT_CONVERGED, summary={"fuel_burned_kg": 4.3e-06, "gtow_kg": 79560.1})
    out = json.loads(intercept_response("run_simulation", json.dumps(payload)))
    assert out["error_code"] == "ZERO_FUEL" and "convergence_warning" not in out


def test_a_converged_run_gets_no_warning():
    payload = dict(FLOWN_NOT_CONVERGED, converged=True, exit_code=0)
    assert "convergence_warning" not in json.loads(intercept_response("run_simulation", json.dumps(payload)))


def test_flown_not_converged_is_scored_on_its_metrics():
    c = classify_aviary_eval({"fuel_burned_kg": 22998.6, "gtow_kg": 102187.6}, converged=False)
    assert c.result != "sim_fail" and c.result in ("omission", "commission")
    assert c.converged is False and "warning" in c.reason


def test_did_not_fly_is_sim_fail():
    c = classify_aviary_eval({"fuel_burned_kg": 0.0, "gtow_kg": 79560.1}, converged=False)
    assert c.result == "sim_fail" and "did not fly" in c.reason


def test_unextracted_fuel_keeps_the_old_rule():
    assert classify_aviary_eval({"gtow_kg": 79560.1}, converged=False).result == "sim_fail"
    assert classify_aviary_eval({"gtow_kg": 73705.42}, converged=True).result == "success"


def test_stage_completion_uses_the_recorded_result_not_the_word_failed():
    stages = [
        {"agent_name": "simulation_executor", "turn_number": 1, "content": "Aviary optimizer failed ...",
         "metadata": {"completion_met": True}},
        {"agent_name": "mdo_integrator", "turn_number": 2, "content": "CONSTRAINTS_FAILED: fuel\nVERDICT: CONTINUE",
         "metadata": {"completion_met": True}},
    ]
    m = _staged_pipeline_metrics(stages, {}, [])
    assert [s["met"] for s in m["per_stage_completion"]] == [True, True]
    assert {s["reason"] for s in m["per_stage_completion"]} == {"completion_criteria"}
