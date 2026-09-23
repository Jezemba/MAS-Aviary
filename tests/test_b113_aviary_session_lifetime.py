"""B113: an aviary session must survive a long link, and a dead one must be recoverable.

geoauth_all8_7960 networked_graph_routed L1: the runner's session was reaped by aviary's 2 h idle
timeout while the team spent its first two hours on geometry, meshing and SU2. All 27 later aviary
calls returned NO_SESSION while `session_creation_refusal` pointed the agents back at the dead id,
and the link burned the full 480-minute timeout without flying a mission.
"""
import json
import sys
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import knowledge_base as kb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class FakeTool:
    def __init__(self, name, result=None, fail=False):
        self.name = name
        self.calls = []
        self._result = result or {}
        self._fail = fail

    def forward(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail:
            raise RuntimeError("server gone")
        return json.dumps(self._result)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    state = DesignState()
    monkeypatch.setattr(dp, "_design_state", state)
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_presessions", {})
    monkeypatch.setattr(dp, "_tool_server_map", {"configure_mission": "aviary",
                                                 "create_session": "aviary",
                                                 "set_aircraft_parameters": "aviary",
                                                 "check_constraints": "aviary",
                                                 "run_simulation": "aviary"})
    yield state


NO_SESSION = {"success": False, "error_code": "NO_SESSION",
              "error": "Session 'x' not found or expired."}


# ---- the session is dropped once the server says it is gone -------------------------------------

def test_a_no_session_reply_drops_the_registered_session(fresh):
    dp.register_presession("aviary", "dead-session")
    assert fresh.sessions["aviary"] == "dead-session"
    dp.intercept_response("configure_mission", json.dumps(NO_SESSION))
    assert fresh.sessions.get("aviary") is None
    assert fresh.data_store["dead_sessions"]["aviary"] == "dead-session"


def test_a_healthy_reply_changes_nothing(fresh):
    dp.register_presession("aviary", "live-session")
    dp.intercept_response("configure_mission", json.dumps({"success": True}))
    assert fresh.sessions["aviary"] == "live-session"
    assert "dead_sessions" not in fresh.data_store


def test_the_refusal_stands_down_for_a_dead_session(fresh):
    dp.register_presession("aviary", "dead-session")
    assert dp.session_creation_refusal("create_session", {}) is not None   # still alive
    dp.intercept_response("configure_mission", json.dumps(NO_SESSION))
    assert dp.session_creation_refusal("create_session", {}) is None       # now recoverable


def test_the_refusal_still_protects_a_live_session(fresh):
    dp.register_presession("aviary", "live-session")
    refusal = dp.session_creation_refusal("create_session", {})
    assert refusal["error_code"] == "SESSION_EXISTS"
    assert refusal["session_id"] == "live-session"


# ---- a replacement session gets the run's mission and design back --------------------------------

def test_a_replacement_is_given_the_canonical_mission_and_design(fresh):
    fresh.data_store["design_params_applied"] = {"Aircraft.Wing.AREA": 122.5}
    configure = FakeTool("configure_mission")
    setter = FakeTool("set_aircraft_parameters")
    dp._registered_tools.update({"configure_mission": configure, "set_aircraft_parameters": setter})

    dp._restore_aviary_session("new-session")

    assert configure.calls and configure.calls[0]["session_id"] == "new-session"
    assert configure.calls[0]["range_nmi"] == 2500 and configure.calls[0]["num_passengers"] == 239
    assert setter.calls[0]["parameters"] == {"Aircraft.Wing.AREA": 122.5}


def test_restoring_never_raises_when_the_server_fails(fresh):
    dp._registered_tools["configure_mission"] = FakeTool("configure_mission", fail=True)
    dp._restore_aviary_session("new-session")      # must not raise


def test_nothing_is_re_applied_when_no_design_was_set(fresh):
    setter = FakeTool("set_aircraft_parameters")
    dp._registered_tools.update({"configure_mission": FakeTool("configure_mission"),
                                 "set_aircraft_parameters": setter})
    dp._restore_aviary_session("new-session")
    assert not setter.calls


# ---- the heartbeat keeps a live session from idling out ------------------------------------------

def test_the_heartbeat_touches_the_session_and_stops_cleanly():
    from stat_batch_runner import AviaryHeartbeat

    tool = FakeTool("check_constraints", {"success": True})
    with AviaryHeartbeat({"check_constraints": tool}, "sid", every=0.05) as hb:
        deadline = __import__("time").time() + 2.0
        while not tool.calls and __import__("time").time() < deadline:
            __import__("time").sleep(0.01)
    assert tool.calls, "the heartbeat never touched the session"
    assert tool.calls[0] == {"session_id": "sid", "constraints": []}
    assert hb._thread is not None and not hb._thread.is_alive()


def test_a_failing_heartbeat_does_not_break_the_run():
    from stat_batch_runner import AviaryHeartbeat

    tool = FakeTool("check_constraints", fail=True)
    with AviaryHeartbeat({"check_constraints": tool}, "sid", every=0.05) as hb:
        deadline = __import__("time").time() + 2.0
        while not hb.failures and __import__("time").time() < deadline:
            __import__("time").sleep(0.01)
    assert hb.failures >= 1


def test_no_session_means_no_heartbeat():
    from stat_batch_runner import AviaryHeartbeat

    tool = FakeTool("check_constraints")
    with AviaryHeartbeat({"check_constraints": tool}, "", every=0.05):
        __import__("time").sleep(0.1)
    assert not tool.calls


def test_the_heartbeat_is_not_written_to_the_knowledge_base():
    with kb.not_recorded():
        assert kb.record_tool_result("check_constraints", "aviary", {}, {"ok": True}) is None
    # and recording works again afterwards
    assert not getattr(kb._no_record, "on", False)


def test_an_auto_create_that_is_not_a_replacement_stays_blank(fresh, monkeypatch):
    """B77's control case: a session the runner never registered must NOT be quietly configured.

    Configuring every auto-created session would hide "the child did not get the runner's session",
    which is what tests/test_b77_runner_session.py detects by flying the wrong mission.
    """
    configure = FakeTool("configure_mission")
    dp._registered_tools.update({"create_session": FakeTool("create_session", {"session_id": "fresh-one"}),
                                 "configure_mission": configure})
    assert dp._auto_create_session("aviary") == "fresh-one"
    assert not configure.calls


def test_a_replacement_for_a_dead_session_is_configured(fresh):
    fresh.data_store["dead_sessions"] = {"aviary": "gone"}
    configure = FakeTool("configure_mission")
    dp._registered_tools.update({"create_session": FakeTool("create_session", {"session_id": "replacement"}),
                                 "configure_mission": configure})
    assert dp._auto_create_session("aviary") == "replacement"
    assert configure.calls and configure.calls[0]["session_id"] == "replacement"
