"""Two changes from 2026-08-17, both feedback rather than enforcement.

1. Warn when the design has not been ASSESSED. Measured: orchestrated_staged_pipeline
   never reached mdo_integrator in EITHER link, so both produced a fuel number with no
   verdict and nothing to carry into the next chain link.

2. Scope the task preamble and point at discovery TOOLS. The uncoupled runs traced back
   to a geometry worker that never called generate_volume_mesh -- it burned its turns on
   a session-id mismatch ("maybe the initial SESSION_ID was a typo") while
   get_design_state, built for exactly that, was called ZERO times all sweep.
"""

import pytest

from src.coordination.strategies.orchestrated import _unassessed_note
from src.coordination.iterative_feedback_handler import IterativeFeedbackHandler

PHASES = {
    "geometry_setup": ["open_cpacs"],
    "aerodynamic_analysis": ["run_su2_solver"],
    "evaluation": ["get_results", "check_constraints"],
}


class TestUnassessedWarning:
    def test_it_fires_when_the_final_phase_has_not_run(self):
        note = _unassessed_note(PHASES, {"geometry_setup"})
        assert "NOT been assessed" in note and "evaluation" in note

    def test_it_says_what_is_lost(self):
        note = _unassessed_note(PHASES, set())
        assert "verdict" in note and "recommendation" in note

    def test_it_is_silent_once_the_assessment_has_run(self):
        assert _unassessed_note(PHASES, {"evaluation"}) == ""

    def test_no_phases_declared_is_silent(self):
        assert _unassessed_note({}, set()) == ""

    def test_it_is_information_not_a_gate(self):
        """It must READ as advice; nothing here may block a stage."""
        note = _unassessed_note(PHASES, set())
        assert note.startswith("NOTE:")
        for word in ("REJECTED", "must not", "forbidden", "blocked"):
            assert word not in note


class TestHandlerCarriesItToo:
    """iterative_feedback builds its own context (B43), so it needs the warning
    independently of the strategy."""

    def _handler(self, done_tools):
        h = IterativeFeedbackHandler({"_required_tool_phases": PHASES})
        h._tools_seen = set(done_tools)
        return h

    def test_warns_when_evaluation_is_pending(self):
        status = self._handler({"open_cpacs"})._format_phase_status()
        assert "not yet run" in status

    def test_names_the_assessment_when_it_is_the_last_pending(self):
        status = self._handler({"open_cpacs", "run_su2_solver"})._format_phase_status()
        assert "NOT been assessed" in status
        assert "evaluation" in status

    def test_silent_when_everything_has_run(self):
        h = self._handler({"open_cpacs", "run_su2_solver", "get_results", "check_constraints"})
        assert "NOT been assessed" not in h._format_phase_status()


class TestTaskPreamble:
    """The preamble told every worker to use ONE session id 'for ALL tool calls'
    and warned it off creating sessions -- true for aviary, false for the other
    four servers."""

    @pytest.fixture
    def task(self):
        from scripts.stat_batch_runner import build_task_with_session
        return build_task_with_session("do the MDO", "aviary-sid-123", {})

    def test_the_session_is_labelled_as_aviary(self, task):
        assert "AVIARY session_id" in task

    def test_it_says_other_servers_have_their_own(self, task):
        assert "OWN SESSIONS" in task or "own sessions" in task.lower()
        assert "create_su2_session" in task

    def test_it_points_at_get_design_state(self, task):
        """The tool existed all sweep and was called zero times."""
        assert "get_design_state" in task

    def test_it_tells_the_agent_to_look_before_guessing(self, task):
        assert "LOOK FOR A TOOL FIRST" in task
        assert "get_design_space" in task and "get_valid_config_options" in task

    def test_the_blanket_all_tool_calls_instruction_is_gone(self, task):
        assert "Use this session_id for ALL tool calls" not in task
