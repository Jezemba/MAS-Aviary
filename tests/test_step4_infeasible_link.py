"""A flown mission that does not fly is the link's result, not a reason to retry blind (2026-10-05).

v2b145: link 1's three attempts each flew run_simulation to ZERO_FUEL (SLSQP "Positive directional
derivative for linesearch"); each time the mdo_integrator assessed the failure and the runner threw
it away by retrying the same start. The data plane now flags a flown-but-infeasible mission; the
runner records the link as infeasible and carries the assessment to the next link. A zero figure
with no flown mission (no simulation output) still retries.

Stubbed: no MCP server, no model, no .env.
"""

import json
import sys
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

V2B145_RUN_SIMULATION = {
    "success": True, "session_id": "10d609f8", "converged": False, "exit_code": 1,
    "runtime_seconds": 49.15, "iterations": 632,
    "summary": {"fuel_burned_kg": 7.443605159244908e-06, "gtow_kg": 79560.1, "wing_mass_kg": 9330.6},
}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    yield dp._design_state


def test_a_flown_zero_fuel_mission_is_flagged_infeasible(fresh):
    out = json.loads(dp.intercept_response("run_simulation", json.dumps(V2B145_RUN_SIMULATION)))
    assert out["error_code"] == "ZERO_FUEL"
    flag = fresh.data_store["_simulation_flown_infeasible"]
    assert flag["iterations"] == 632 and flag["converged"] is False and flag["exit_code"] == 1


def test_the_validation_probe_zero_is_not_a_flown_mission(fresh):
    dp.intercept_response("set_aircraft_parameters", json.dumps(
        {"success": True, "valid": True, "model_eval": {"outputs": {"fuel_burned_kg": 0.0}}}))
    assert "_simulation_flown_infeasible" not in fresh.data_store


def test_a_real_mission_is_not_flagged(fresh):
    ok = dict(V2B145_RUN_SIMULATION, summary={"fuel_burned_kg": 18197.0, "gtow_kg": 91660.6})
    dp.intercept_response("run_simulation", json.dumps(ok))
    assert "_simulation_flown_infeasible" not in fresh.data_store


def test_the_flag_crosses_the_process_boundary_and_is_read_once(fresh, monkeypatch):
    import stat_batch_runner as sbr

    dp.intercept_response("run_simulation", json.dumps(V2B145_RUN_SIMULATION))
    summary = dp.export_state_summary()
    assert summary["_simulation_flown_infeasible"]["iterations"] == 632
    monkeypatch.setattr(dp, "_design_state", DesignState())       # the parent's state
    dp.install_state_summary(summary)
    assert sbr._take_flown_infeasible()["iterations"] == 632
    assert sbr._take_flown_infeasible() is None                    # consumed: the next attempt starts clean


def test_no_flown_mission_means_no_flag_so_the_runner_still_retries(fresh):
    import stat_batch_runner as sbr

    assert sbr._take_flown_infeasible() is None
