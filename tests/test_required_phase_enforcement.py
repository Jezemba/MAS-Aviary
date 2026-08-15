"""The orchestrator must be TOLD about an unrun phase -- not forced to run it.

Working out the stage order is precisely what an orchestrated structure is being
measured on, so enforcing it in the framework would replace the variable under
study with our own sequencing. orchestrated+graph_routed already couples 4/6
because its GRAPH supplies that structure legitimately, while
orchestrated+iterative_feedback (0/8) and orchestrated+staged_pipeline (0/7)
must infer it -- and the same handlers couple 6/8 and 2/6 under sequential.

So the feedback fires ONCE and hands the turn back. If the orchestrator concludes
anyway, the run ends and `_phase_gap_ignored` records that it was told and chose
to finish -- a coordination observation, not a defect to paper over.

`required_tool_phases` in the orchestrator agents YAML names the phases a run
must complete and the tools each needs. It was used ONLY to build a prompt hint;
nothing checked that a declared phase ever executed.

Measured 2026-08-12, both orchestrated_staged_pipeline runs (49 and 50 steps,
ZERO errors of any kind):

    structures_analyst    assigned 3x   (all succeeded)
    aerodynamics_analyst  assigned 2x   (all succeeded)
    mission_setup / simulation          NEVER assigned

The framework knew mission_setup was required, watched it never happen, and
reported the run complete with zero fuel.

Declaration order is dependency order -- geometry before aero (aero needs a
mesh), aero before mission (the mission needs the coefficients) -- so the first
unexecuted phase answers both "are we done?" and "what next?". That second half
addresses the other orchestrated failure: everything assigned up front, with the
mission executing before geometry and aero existed.
"""

from __future__ import annotations

import pytest

from src.coordination.history import AgentMessage, ToolCallRecord
from src.coordination.strategies.orchestrated import OrchestratedStrategy

PHASES = {
    "geometry_setup": ["open_cpacs", "generate_volume_mesh"],
    "aerodynamic_analysis": ["create_su2_session", "run_su2_solver"],
    "mission_setup": ["create_session", "configure_mission"],
}


def _msg(content, tools=()):
    return AgentMessage(
        agent_name="w", content=content, turn_number=1, timestamp=1.0,
        tool_calls=[ToolCallRecord(tool_name=t, inputs={}, output="", duration_seconds=0.0)
                    for t in tools],
    )


class _Ctx:
    required_tool_phases = PHASES


def _strategy():
    s = OrchestratedStrategy.__new__(OrchestratedStrategy)
    s._phase = "creation"
    s._termination_keyword = "TASK_COMPLETE"
    s._pending_phase_note = None
    s._phase_gap_reported = False
    s._phase_gap_pending = None
    s._phase_gap_ignored = None
    s._max_turns = 100
    s._context = _Ctx()
    return s


class TestTheOrchestratorIsInformedOnce:
    def test_first_completion_attempt_is_handed_back(self):
        """Geometry + aero done, mission never run -> one chance to reconsider."""
        s = _strategy()
        history = [
            _msg("geo", ["open_cpacs", "generate_volume_mesh"]),
            _msg("aero", ["create_su2_session", "run_su2_solver"]),
            _msg("TASK_COMPLETE"),
        ]
        assert s.is_complete(history, {}) is False

    def test_a_second_attempt_is_allowed_through(self):
        """Told once, the orchestrator may still decide to finish -- and that
        decision is the measurement."""
        s = _strategy()
        history = [
            _msg("geo", ["open_cpacs", "generate_volume_mesh"]),
            _msg("TASK_COMPLETE"),
        ]
        assert s.is_complete(history, {}) is False      # informed
        assert s.is_complete(history, {}) is True       # its call
        assert s._phase_gap_ignored == "aerodynamic_analysis"

    def test_the_note_names_the_missing_phase_and_its_tools(self):
        s = _strategy()
        history = [
            _msg("geo", ["open_cpacs", "generate_volume_mesh"]),
            _msg("TASK_COMPLETE"),
        ]
        s.is_complete(history, {})
        note = s._pending_phase_note
        assert "aerodynamic_analysis" in note
        assert "create_su2_session" in note

    def test_completion_allowed_once_every_phase_has_run(self):
        s = _strategy()
        history = [
            _msg("geo", ["open_cpacs", "generate_volume_mesh"]),
            _msg("aero", ["create_su2_session", "run_su2_solver"]),
            _msg("mission", ["create_session", "configure_mission"]),
            _msg("TASK_COMPLETE"),
        ]
        assert s.is_complete(history, {}) is True

    def test_a_partially_executed_phase_does_not_count(self):
        """One of two tools is not the phase."""
        s = _strategy()
        history = [_msg("geo", ["open_cpacs"]), _msg("TASK_COMPLETE")]
        assert s.is_complete(history, {}) is False


class TestOrderingGuidance:
    def test_next_phase_follows_declaration_order(self):
        """Declaration order is dependency order."""
        s = _strategy()
        phase, tools = s._next_required_phase([])
        assert phase == "geometry_setup"

    def test_next_advances_as_phases_complete(self):
        s = _strategy()
        history = [_msg("geo", ["open_cpacs", "generate_volume_mesh"])]
        assert s._next_required_phase(history)[0] == "aerodynamic_analysis"

    def test_none_when_all_done(self):
        s = _strategy()
        history = [_msg("all", ["open_cpacs", "generate_volume_mesh",
                                "create_su2_session", "run_su2_solver",
                                "create_session", "configure_mission"])]
        assert s._next_required_phase(history)[0] is None


class TestItCannotHangTheRun:
    def test_max_turns_still_terminates(self):
        """The guard must never outrank the turn limit, or a run that cannot
        reach a phase would never stop."""
        s = _strategy()
        s._max_turns = 2
        history = [_msg("x"), _msg("TASK_COMPLETE")]
        assert s.is_complete(history, {}) is True

    def test_no_declared_phases_means_no_enforcement(self):
        s = _strategy()
        s._context.required_tool_phases = {}
        assert s.is_complete([_msg("TASK_COMPLETE")], {}) is True
        s._context.required_tool_phases = PHASES
