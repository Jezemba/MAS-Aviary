"""The agent must be able to LOOK UP the session id for the server it is calling.

Measured (budget_test 2026-08-16, orchestrated_graph_routed link 0): the design
state held the right answers the whole time --

    "sessions": {"su2": "b8db9e82-...", "aviary": "b06be523-..."}

-- and none of the 58 tools the agent was given could read it. Its only source
was the task preamble, which hands every worker the AVIARY session id and says
"use this session_id for ALL tool calls". The aero worker obeyed:

    set_mesh(session_id=<aviary id>) -> "Unknown session_id: db6bdca4-..."   (B53)

Read-only and uniform across all eight combinations, deliberately. Rewriting a
worker's wrong session id in the middleware would fix the symptom for the combos
that already succeed too, erasing a real behavioural difference -- whether an
agent looks up what it needs is itself a coordination behaviour worth measuring.
"""

import json

import pytest

from src.tools.design_state_tool import GetDesignState


class _DS:
    def __init__(self, sessions=None, store=None):
        self.sessions = sessions or {}
        self.data_store = store or {}


@pytest.fixture
def state(monkeypatch):
    def _install(ds):
        import src.tools.data_plane as dp
        monkeypatch.setattr(dp, "get_design_state", lambda: ds)
        return json.loads(GetDesignState().forward())
    return _install


class TestItAnswersTheQuestionThatWasUnanswerable:
    def test_returns_the_session_for_each_server(self, state):
        out = state(_DS(sessions={"su2": "su2-id", "aviary": "av-id"}))
        assert out["sessions"]["su2"] == "su2-id"
        assert out["sessions"]["aviary"] == "av-id"

    def test_says_namespaces_are_per_server(self, state):
        out = state(_DS(sessions={"su2": "x"}))
        assert "own session namespace" in out["note"]

    def test_names_the_call_that_creates_a_missing_session(self, state):
        out = state(_DS(sessions={"aviary": "x"}))
        assert "create_su2_session" in out["note"]

    def test_warns_that_design_parameters_are_not_su2_options(self, state):
        """The other half of the same defect (B54)."""
        out = state(_DS(sessions={}))
        assert "Aircraft.Wing.TAPER_RATIO" in out["note"]
        assert "NOT valid" in out["note"]


class TestItIsSafeToCall:
    def test_no_state_yet_is_not_an_error(self, monkeypatch):
        import src.tools.data_plane as dp
        monkeypatch.setattr(dp, "get_design_state", lambda: None)
        out = json.loads(GetDesignState().forward())
        assert out["sessions"] == {}

    def test_oversized_state_is_truncated_and_says_so(self, state):
        big = {f"var_{i}": "x" * 200 for i in range(100)}
        out = state(_DS(sessions={"su2": "x"}, store={"analysis_vars": big}))
        assert out["design_parameters"]["_truncated"] is True
        # sessions must survive truncation -- they are the reason to call it
        assert out["sessions"]["su2"] == "x"

    def test_reports_coupling_status(self, state):
        out = state(_DS(store={"aero_coupling_status": "injected"}))
        assert out["aero_coupling_status"] == "injected"


class TestEveryAgentGetsIt:
    def test_loader_appends_it_once(self):
        from src.tools.tool_loader import _with_design_state

        class _T:
            def __init__(self, n): self.name = n

        tools = _with_design_state([_T("run_su2_solver")])
        names = [t.name for t in tools]
        assert names.count("get_design_state") == 1

    def test_appending_twice_does_not_duplicate(self):
        from src.tools.tool_loader import _with_design_state
        once = _with_design_state([])
        twice = _with_design_state(once)
        assert len([t for t in twice if t.name == "get_design_state"]) == 1
