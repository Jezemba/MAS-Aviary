"""A missing session must be created, not forwarded as a fiction.

The data plane already CORRECTS a wrong session id -- it overrides hallucinated
ids with the captured one -- but it could not CREATE a missing one. The whole
override block is guarded on `sessions[mcp]` being populated, which only happens
once the session-creation tool has been called.

Measured 2026-08-12 (final4 run 1/8): the mission worker never called
create_session, invented "aviary_session_1", and configure_mission,
set_aircraft_parameters x6 and run_simulation x4 all went to the server against a
session that did not exist. Zero data-plane overrides fired, because there was
nothing to substitute. The run recorded zero fuel.

Every other prerequisite in this pipeline is now supplied rather than requested
-- CPACS path, SU2 numerics, mesh payload, session config -- so the session the
whole mission depends on is supplied too.
"""

from __future__ import annotations

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools import data_plane
from src.tools.data_plane import init_data_plane, register_tools, resolve_request

TOOL_SERVER_MAP = {
    "create_session": "aviary",
    "run_simulation": "aviary",
    "set_aircraft_parameters": "aviary",
}


class _CreateSession:
    name = "create_session"

    def __init__(self, sid="real-sid-123"):
        self.sid = sid
        self.calls = 0

    def forward(self, **kwargs):
        self.calls += 1
        return json.dumps({"success": True, "session_id": self.sid})


@pytest.fixture(autouse=True)
def _state():
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    data_plane._registered_tools = {}
    yield
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    data_plane._registered_tools = {}


class TestAutoCreation:
    def test_missing_session_is_created(self):
        tool = _CreateSession()
        register_tools([tool])
        out = resolve_request("run_simulation", {"session_id": "aviary_session_1"})
        assert out["session_id"] == "real-sid-123"
        assert tool.calls == 1

    def test_the_invented_id_never_reaches_the_server(self):
        register_tools([_CreateSession()])
        out = resolve_request("set_aircraft_parameters", {"session_id": "aviary_session_1"})
        assert out["session_id"] != "aviary_session_1"

    def test_created_once_then_reused(self):
        """A session per call would be worse than none."""
        tool = _CreateSession()
        register_tools([tool])
        resolve_request("run_simulation", {"session_id": "x"})
        resolve_request("set_aircraft_parameters", {"session_id": "y"})
        assert tool.calls == 1

    def test_an_existing_session_is_not_replaced(self):
        tool = _CreateSession()
        register_tools([tool])
        data_plane.get_design_state().sessions["aviary"] = "already-here"
        out = resolve_request("run_simulation", {"session_id": "bogus"})
        assert out["session_id"] == "already-here"
        assert tool.calls == 0


class TestItDegradesSafely:
    def test_no_registered_tool_means_no_crash(self):
        """Nothing registered: the call proceeds and the server reports the
        real problem, exactly as before."""
        out = resolve_request("run_simulation", {"session_id": "aviary_session_1"})
        assert out["session_id"] == "aviary_session_1"

    def test_a_failing_creator_does_not_break_the_run(self):
        class _Boom:
            name = "create_session"

            def forward(self, **kwargs):
                raise RuntimeError("server down")

        register_tools([_Boom()])
        out = resolve_request("run_simulation", {"session_id": "aviary_session_1"})
        assert out["session_id"] == "aviary_session_1"

    def test_a_creator_returning_no_id_is_ignored(self):
        class _Empty:
            name = "create_session"

            def forward(self, **kwargs):
                return json.dumps({"success": False})

        register_tools([_Empty()])
        out = resolve_request("run_simulation", {"session_id": "aviary_session_1"})
        assert out["session_id"] == "aviary_session_1"

    def test_only_whitelisted_mcps_auto_create(self):
        """su2/tigl sessions carry setup that must not be silently invented."""
        assert set(data_plane._AUTO_SESSION_TOOLS) == {"aviary"}
