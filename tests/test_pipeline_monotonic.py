"""A staged pipeline runs forwards only.

Measured (orch_staged 2026-08-17, link 0) -- worker execution order:

    1 geometry_engineer     stage 1
    2 aerodynamics_analyst  stage 2
    3 aerodynamics_analyst  retry
    4 aerodynamics_analyst  retry
    5 structures_analyst    stage 3
    6 structures_analyst    retry
    7 propulsion_analyst    stage 4
    8 geometry_engineer     BACKWARDS to stage 1
    9 aerodynamics_analyst  stage 2 again

Nine of thirty orchestrator turns spent without ever reaching stages 5-7, so no
mission, no simulation, no verdict, no fuel figure.

`_transition_to_execution` reorders the INITIAL assignment batch into pipeline
order, but only once; afterwards `per_stage` mode let the cursor follow whatever
stage the orchestrator named. Retry and forward-skip are legitimate; rewinding is
what turns a pipeline into a free-form loop -- and it means
orchestrated_staged_pipeline and sequential_staged_pipeline are not running the
same experiment, so comparing them is confounded.
"""

import pytest

from src.coordination.strategies.orchestrated import OrchestratedStrategy

STAGES = ["geometry_engineer", "aerodynamics_analyst", "structures_analyst",
          "propulsion_analyst", "mission_architect", "simulation_executor",
          "mdo_integrator"]


@pytest.fixture
def strat():
    s = OrchestratedStrategy()
    s._pipeline_stage_names = list(STAGES)
    s._current_stage_idx = 0
    s._max_stage_reached = 0
    return s


class TestTheHighWaterMarkOnlyRises:
    def test_starts_at_the_first_stage(self, strat):
        assert strat._max_stage_reached == 0

    def test_forward_progress_raises_it(self, strat):
        for i, _ in enumerate(STAGES):
            strat._max_stage_reached = max(strat._max_stage_reached, i)
        assert strat._max_stage_reached == len(STAGES) - 1

    def test_a_retry_does_not_lower_it(self, strat):
        strat._max_stage_reached = 3          # reached propulsion
        strat._max_stage_reached = max(strat._max_stage_reached, 3)
        assert strat._max_stage_reached == 3

    def test_a_backward_index_is_below_the_mark(self, strat):
        """The condition the guard tests: stage 1 after stage 4."""
        strat._max_stage_reached = 3
        assert STAGES.index("geometry_engineer") < strat._max_stage_reached


class TestTheRejectionExplainsItself:
    """An error that only says "no" gets abandonment; one that names the correct
    next action gets one-step recovery -- the measured remedy boundary in this
    project."""

    def _note(self, strat, reached, attempted):
        strat._max_stage_reached = reached
        cur = STAGES[reached]
        nxt = STAGES[reached + 1] if reached + 1 < len(STAGES) else None
        idx = STAGES.index(attempted)
        return (
            f"REJECTED: {attempted} is stage {idx + 1} and the pipeline has "
            f"already completed stage {reached + 1} ({cur}). A staged "
            f"pipeline runs forwards only -- you may re-run {cur} or move on to "
            + (f"{nxt}." if nxt else "the final stage.")
        )

    def test_it_names_the_stage_that_was_refused(self, strat):
        assert "geometry_engineer is stage 1" in self._note(strat, 3, "geometry_engineer")

    def test_it_names_where_the_pipeline_actually_is(self, strat):
        assert "propulsion_analyst" in self._note(strat, 3, "geometry_engineer")

    def test_it_names_the_next_stage_to_assign(self, strat):
        assert "mission_architect" in self._note(strat, 3, "geometry_engineer")

    def test_at_the_final_stage_it_does_not_invent_a_next_one(self, strat):
        note = self._note(strat, len(STAGES) - 1, "geometry_engineer")
        assert "the final stage" in note


class TestWhatMustStillBeAllowed:
    def test_retrying_the_current_stage_is_not_backwards(self, strat):
        strat._max_stage_reached = 1
        assert not (STAGES.index("aerodynamics_analyst") < strat._max_stage_reached)

    def test_skipping_forward_is_not_backwards(self, strat):
        """The orchestrator may judge a stage unnecessary; that is its call."""
        strat._max_stage_reached = 1
        assert not (STAGES.index("simulation_executor") < strat._max_stage_reached)


class TestTheGuardItself:
    """Exercises _per_stage_execution, not a restatement of its condition.

    The other tests in this file mirror the comparison the guard makes, which
    proves the arithmetic and nothing about the code that runs. Twice today a
    green suite of exactly that shape let a real defect through -- B49 (the
    handler the RUNNER builds never got its config) and a NameError in the live
    heartbeat. So this one drives the real method and asserts on what it returns.
    """

    @staticmethod
    def _ctx(assignments):
        class _C:
            pass
        c = _C()
        c.assignments = list(assignments)
        c.turn_counter = 1
        # The rejection path routes back to the orchestrator, which builds the
        # per-stage context -- so the stub needs what that builder reads.
        c.required_tool_phases = {}
        c.required_result_signals = []
        c.created_agents = []
        c.available_tools = {}
        c.agents = {}
        return c

    def _strategy_at(self, reached, assignments):
        s = OrchestratedStrategy()
        s._pipeline_stage_names = list(STAGES)
        s._max_stage_reached = reached
        s._current_stage_idx = reached
        s._context = self._ctx(assignments)
        s._phase = "execution"
        s._lifecycle_mode = "per_stage"
        s._orchestrator_name = "orchestrator"
        s._agents = {}
        return s

    def test_a_backward_assignment_is_not_executed(self):
        """Stage 1 assigned after stage 4 completed must not run."""
        s = self._strategy_at(3, [{"agent_name": "geometry_engineer", "task": "redo geometry"}])
        action = s._per_stage_execution([], {"task": "t"})
        assert action.agent_name != "geometry_engineer"

    def test_the_offending_assignment_is_dropped(self):
        """If it stayed in the list the same one is re-selected forever."""
        s = self._strategy_at(3, [{"agent_name": "geometry_engineer", "task": "redo geometry"}])
        s._per_stage_execution([], {"task": "t"})
        assert not [a for a in s._context.assignments
                    if a["agent_name"] == "geometry_engineer"]

    def test_the_orchestrator_is_told_why(self):
        """Assert on what the orchestrator RECEIVES, not on the note field.

        _pending_phase_note is consumed by the context builder -- it is appended
        to the orchestrator's prompt and cleared -- so checking the field after
        the call reads None even though the message was delivered. The delivered
        text is the thing that matters.
        """
        s = self._strategy_at(3, [{"agent_name": "geometry_engineer", "task": "redo geometry"}])
        action = s._per_stage_execution([], {"task": "t"})
        ctx = action.input_context or ""
        assert "REJECTED" in ctx
        assert "mission_architect" in ctx      # names the stage to assign instead
        assert "propulsion_analyst" in ctx     # names where the pipeline actually is

    def test_the_high_water_mark_is_not_rewound(self):
        s = self._strategy_at(3, [{"agent_name": "geometry_engineer", "task": "x"}])
        s._per_stage_execution([], {"task": "t"})
        assert s._max_stage_reached == 3

    def test_a_forward_assignment_still_runs(self):
        s = self._strategy_at(3, [{"agent_name": "mission_architect", "task": "configure"}])
        action = s._per_stage_execution([], {"task": "t"})
        assert action.agent_name == "mission_architect"
        assert s._max_stage_reached == 4

    def test_retrying_the_current_stage_still_runs(self):
        s = self._strategy_at(3, [{"agent_name": "propulsion_analyst", "task": "retry"}])
        action = s._per_stage_execution([], {"task": "t"})
        assert action.agent_name == "propulsion_analyst"
        assert s._max_stage_reached == 3


class TestTheGuardInActiveMode:
    """The mode orchestrated+staged_pipeline ACTUALLY runs in.

    The first version of this fix was placed in _per_stage_execution and never
    executed once: config/orchestrated.yaml sets `lifecycle_mode: active`. I had
    inferred per_stage from PHASES lines in the log, but B40 added those to
    _format_context_for_orchestrator as well, so they appear in BOTH modes -- a
    symptom common to both branches read as proof of one.

    These tests drive _active_execution, which is where the worker is dispatched.
    """

    @staticmethod
    def _ctx(assignments):
        class _C:
            pass
        c = _C()
        c.assignments = list(assignments)
        c.turn_counter = 1
        c.required_tool_phases = {}
        c.required_result_signals = []
        c.created_agents = []
        c.available_tools = {}
        c.agents = {}
        # _active_execution consults result_signals once past the first
        # assignment; the stub needs it or the guard test dies before reaching
        # the guard.
        c.result_signals = {}
        return c

    def _strategy(self, reached, assignments, exec_index=0):
        s = OrchestratedStrategy()
        s._pipeline_stage_names = list(STAGES)
        s._max_stage_reached = reached
        s._context = self._ctx(assignments)
        s._phase = "execution"
        s._lifecycle_mode = "active"
        s._orchestrator_name = "orchestrator"
        s._agents = {}
        s._execution_index = exec_index
        s._graph_roles = None
        return s

    def test_backward_assignment_is_not_dispatched(self):
        s = self._strategy(3, [{"agent_name": "geometry_engineer", "task": "redo"}])
        action = s._active_execution([], {"task": "t"})
        assert action.agent_name != "geometry_engineer"

    def test_the_orchestrator_is_told_which_stage_to_assign(self):
        s = self._strategy(3, [{"agent_name": "geometry_engineer", "task": "redo"}])
        s._active_execution([], {"task": "t"})
        note = s._pending_phase_note or ""
        assert "REJECTED" in note
        assert "propulsion_analyst" in note     # where the pipeline is
        assert "mission_architect" in note      # what to assign next

    def test_forward_assignment_dispatches_and_advances_the_mark(self):
        s = self._strategy(3, [{"agent_name": "mission_architect", "task": "configure"}])
        action = s._active_execution([], {"task": "t"})
        assert action.agent_name == "mission_architect"
        assert s._max_stage_reached == 4

    def test_same_stage_retry_still_dispatches(self):
        s = self._strategy(3, [{"agent_name": "propulsion_analyst", "task": "retry"}])
        action = s._active_execution([], {"task": "t"})
        assert action.agent_name == "propulsion_analyst"
        assert s._max_stage_reached == 3

    def test_the_observed_sequence_is_now_blocked(self):
        """The exact order from the failing run: stages 1,2,3,4 then back to 1."""
        s = self._strategy(0, [])
        for name in ("geometry_engineer", "aerodynamics_analyst",
                     "structures_analyst", "propulsion_analyst"):
            s._context.assignments.append({"agent_name": name, "task": "x"})
            act = s._active_execution([], {"task": "t"})
            assert act.agent_name == name, f"{name} should have run"
        s._context.assignments.append({"agent_name": "geometry_engineer", "task": "rewind"})
        act = s._active_execution([], {"task": "t"})
        assert act.agent_name != "geometry_engineer", "the rewind was not blocked"

    def test_a_non_pipeline_agent_is_unaffected(self):
        """Workers outside the pipeline must not be caught by the guard."""
        s = self._strategy(3, [{"agent_name": "some_helper", "task": "x"}])
        action = s._active_execution([], {"task": "t"})
        assert action.agent_name == "some_helper"
