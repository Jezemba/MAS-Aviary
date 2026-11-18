"""B349: aviary-mcp refuses a second concurrent run_simulation on a session (RUN_IN_PROGRESS). Networked peers share
one aviary session, so the refusal reaches the agents; it is not a failed mission and must not make the guard of
B188 refuse the next, unchanged run_simulation. Replies shaped like aviary-mcp's (_error + review #6 fingerprint).
No network, no servers."""
import json

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import mission_guard


@pytest.fixture
def plane():
    state = DesignState()
    state.sessions["aviary"] = "av1"
    dp.init_data_plane(state, {"run_simulation": "aviary"})
    yield state
    dp.init_data_plane(DesignState(), {})


@pytest.mark.parametrize("code", sorted(mission_guard.NOT_RUN_CODES))
def test_b349_a_run_that_never_started_is_not_a_failed_mission(plane, code):
    reply = json.dumps({"success": False, "error_code": code, "error": "not run", "design_fingerprint": "fp"})
    mission_guard.observe("run_simulation", {"session_id": "av1"}, reply, "error")
    assert "av1" not in (plane.data_store.get("_failed_missions") or {})
    assert mission_guard.check("run_simulation", {"session_id": "av1"}) is None


def test_b349_a_real_failed_mission_is_still_tracked(plane):
    reply = json.dumps({"success": False, "error_code": "MISSION_NOT_CONVERGED", "error": "no valid result"})
    mission_guard.observe("run_simulation", {"session_id": "av1"}, reply, "error")
    assert mission_guard.check("run_simulation", {"session_id": "av1"})["error_code"] == mission_guard.ERROR_CODE


def test_b349_in_progress_does_not_hide_the_first_runs_outcome(plane):
    """Peer B is refused while peer A's run flies; A's converged reply then clears any failure on the session."""
    mission_guard.observe("run_simulation", {"session_id": "av1"},
                          json.dumps({"success": False, "error_code": "MISSION_NOT_CONVERGED"}), "error")
    mission_guard.observe("run_simulation", {"session_id": "av1"},
                          json.dumps({"success": False, "error_code": "RUN_IN_PROGRESS"}), "error")
    assert plane.data_store["_failed_missions"]["av1"]["why"].startswith("MISSION_NOT_CONVERGED")
    mission_guard.observe("run_simulation", {"session_id": "av1"},
                          json.dumps({"success": True, "summary": {"fuel_burned_kg": 16638.7}}), "success")
    assert "av1" not in plane.data_store["_failed_missions"]
