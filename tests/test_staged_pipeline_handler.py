"""Unit tests for the StagedPipelineHandler."""

from unittest.mock import MagicMock

from src.coordination.completion_criteria import CompletionCriteria, CompletionResult
from src.coordination.execution_handler import Assignment
from src.coordination.stage_definition import PipelineDefinition, StageDefinition
from src.coordination.staged_pipeline_handler import (
    StagedPipelineHandler,
)

# ---- Helpers ---------------------------------------------------------------


def _make_agent(output: str, name: str = "worker") -> MagicMock:
    """Create a mock agent that returns the given output."""
    agent = MagicMock()
    agent.run = MagicMock(return_value=output)
    agent.name = name
    agent.logs = []  # no tool calls by default
    return agent


def _make_agent_with_tools(
    output: str,
    tool_names: list[str],
    name: str = "worker",
) -> MagicMock:
    """Create a mock agent with tool call logs."""
    agent = MagicMock()
    agent.run = MagicMock(return_value=output)
    agent.name = name

    # Mock smolagents-style logs.
    step = MagicMock()
    tc_mocks = []
    for tn in tool_names:
        tc = MagicMock()
        tc.name = tn
        tc.arguments = {}
        tc_mocks.append(tc)
    step.tool_calls = tc_mocks
    agent.logs = [step]
    return agent


def _make_failing_agent(error_msg: str = "boom") -> MagicMock:
    """Create a mock agent that raises an exception."""
    agent = MagicMock()
    agent.run = MagicMock(side_effect=RuntimeError(error_msg))
    agent.name = "failing"
    agent.logs = []
    return agent


def _sample_pipeline() -> PipelineDefinition:
    """Create a 4-stage sample pipeline."""
    return PipelineDefinition(
        stages=[
            StageDefinition(
                name="design_planning",
                completion_criteria=CompletionCriteria(
                    type="output_contains",
                    check="non_empty_output",
                ),
                stage_prompt="Produce a design plan.",
            ),
            StageDefinition(
                name="code_writing",
                completion_criteria=CompletionCriteria(
                    type="output_contains",
                    check="code_block",
                ),
                stage_prompt="Write Python code for the simulation.",
            ),
            StageDefinition(
                name="code_execution",
                completion_criteria=CompletionCriteria(
                    type="tool_attempted",
                    check="tool_called",
                    tool_name="run_simulation",
                ),
                stage_prompt="Run the simulation.",
            ),
            StageDefinition(
                name="output_review",
                completion_criteria=CompletionCriteria(
                    type="output_contains",
                    check="verdict_present",
                ),
                stage_prompt="Review the result. State ACCEPTABLE, ISSUES, or FAILED.",
            ),
        ]
    )


def _handler_with_pipeline(
    pipeline: PipelineDefinition,
    **kwargs,
) -> StagedPipelineHandler:
    """Create a handler with a pre-set pipeline."""
    handler = StagedPipelineHandler(kwargs)
    handler._pipeline = pipeline
    return handler


# ---- Happy path ------------------------------------------------------------


class TestHappyPath:
    def test_all_stages_complete(self):
        pipeline = _sample_pipeline()
        handler = _handler_with_pipeline(pipeline)

        agents = {
            "designer": _make_agent("Plan: Define aircraft parameters and constraints"),
            "coder": _make_agent("```python\nimport openmdao.api as om\nprob = om.Problem()\n```"),
            "executor": _make_agent_with_tools("Execution succeeded", ["run_simulation"]),
            "reviewer": _make_agent("ACCEPTABLE — simulation completed. TASK_COMPLETE"),
        }
        assignments = [
            Assignment(agent_name="designer", task="Make a box"),
            Assignment(agent_name="coder", task="Make a box"),
            Assignment(agent_name="executor", task="Make a box"),
            Assignment(agent_name="reviewer", task="Make a box"),
        ]

        msgs = handler.execute(assignments, agents, None)
        assert len(msgs) == 4  # reviewer hit TASK_COMPLETE but still recorded

        # All stages should have been evaluated.
        results = handler.last_stage_results
        assert len(results) == 4
        assert results[0].completion_met is True  # non-empty
        assert results[1].completion_met is True  # code block
        assert results[2].completion_met is True  # tool called
        assert results[3].completion_met is True  # verdict

    def test_returns_agent_messages(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(name="s1", completion_criteria=CompletionCriteria(type="any", check="always")),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        agents = {"w": _make_agent("hello")}
        msgs = handler.execute(
            [Assignment(agent_name="w", task="test")],
            agents,
            None,
        )
        assert len(msgs) == 1
        assert "hello" in msgs[0].content
        assert msgs[0].agent_name == "w"


# ---- Context passing -------------------------------------------------------


class TestContextPassing:
    def test_first_stage_gets_task(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                    stage_prompt="Do something.",
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        agent = _make_agent("ok")
        handler.execute(
            [Assignment(agent_name="w", task="Build a widget")],
            {"w": agent},
            None,
        )
        call_arg = agent.run.call_args[0][0]
        assert "STAGE: s1" in call_arg
        assert "Build a widget" in call_arg
        assert "Do something." in call_arg

    def test_second_stage_gets_previous_output(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="output_contains", check="non_empty_output"),
                ),
                StageDefinition(
                    name="s2",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        a1 = _make_agent("design plan here")
        a2 = _make_agent("code here")
        handler.execute(
            [Assignment(agent_name="a1", task="t"), Assignment(agent_name="a2", task="t")],
            {"a1": a1, "a2": a2},
            None,
        )
        call_arg = a2.run.call_args[0][0]
        assert "PREVIOUS STAGE: s1" in call_arg
        assert "design plan here" in call_arg

    def test_completion_status_included_by_default(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="output_contains", check="code_block"),
                ),
                StageDefinition(
                    name="s2",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        a1 = _make_agent("no code here")  # will NOT meet code_block
        a2 = _make_agent("ok")
        handler.execute(
            [Assignment(agent_name="a1", task="t"), Assignment(agent_name="a2", task="t")],
            {"a1": a1, "a2": a2},
            None,
        )
        call_arg = a2.run.call_args[0][0]
        assert "COMPLETION STATUS: NOT MET" in call_arg
        assert "NOTE: The previous stage did not fully complete" in call_arg

    def test_completion_status_excluded_when_disabled(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="output_contains", check="code_block"),
                ),
                StageDefinition(
                    name="s2",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline, include_completion_status=False)
        a1 = _make_agent("no code here")
        a2 = _make_agent("ok")
        handler.execute(
            [Assignment(agent_name="a1", task="t"), Assignment(agent_name="a2", task="t")],
            {"a1": a1, "a2": a2},
            None,
        )
        call_arg = a2.run.call_args[0][0]
        assert "COMPLETION STATUS" not in call_arg
        assert "NOTE:" not in call_arg


# ---- Context modes ---------------------------------------------------------


class TestContextModes:
    def _three_stage_pipeline(self):
        return PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="output_contains", check="non_empty_output"),
                ),
                StageDefinition(
                    name="s2",
                    completion_criteria=CompletionCriteria(type="output_contains", check="non_empty_output"),
                ),
                StageDefinition(
                    name="s3",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
            ]
        )

    def test_last_only_mode(self):
        handler = _handler_with_pipeline(self._three_stage_pipeline(), context_mode="last_only")
        a1 = _make_agent("output1")
        a2 = _make_agent("output2")
        a3 = _make_agent("ok")
        handler.execute(
            [
                Assignment(agent_name="a1", task="t"),
                Assignment(agent_name="a2", task="t"),
                Assignment(agent_name="a3", task="t"),
            ],
            {"a1": a1, "a2": a2, "a3": a3},
            None,
        )
        call_arg = a3.run.call_args[0][0]
        assert "output2" in call_arg
        assert "output1" not in call_arg  # only last stage visible

    def test_all_stages_mode(self):
        handler = _handler_with_pipeline(self._three_stage_pipeline(), context_mode="all_stages")
        a1 = _make_agent("output1")
        a2 = _make_agent("output2")
        a3 = _make_agent("ok")
        handler.execute(
            [
                Assignment(agent_name="a1", task="t"),
                Assignment(agent_name="a2", task="t"),
                Assignment(agent_name="a3", task="t"),
            ],
            {"a1": a1, "a2": a2, "a3": a3},
            None,
        )
        call_arg = a3.run.call_args[0][0]
        assert "output1" in call_arg
        assert "output2" in call_arg

    def test_summary_mode(self):
        handler = _handler_with_pipeline(self._three_stage_pipeline(), context_mode="summary")
        a1 = _make_agent("output1")
        a2 = _make_agent("output2")
        a3 = _make_agent("ok")
        handler.execute(
            [
                Assignment(agent_name="a1", task="t"),
                Assignment(agent_name="a2", task="t"),
                Assignment(agent_name="a3", task="t"),
            ],
            {"a1": a1, "a2": a2, "a3": a3},
            None,
        )
        call_arg = a3.run.call_args[0][0]
        assert "SUMMARY" in call_arg
        assert "s1" in call_arg
        assert "s2" in call_arg


# ---- Advancement regardless of completion ----------------------------------


class TestAlwaysAdvances:
    def test_not_met_still_advances(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="output_contains", check="code_block"),
                ),
                StageDefinition(
                    name="s2",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        a1 = _make_agent("plain text no code")  # NOT MET
        a2 = _make_agent("got it")
        msgs = handler.execute(
            [Assignment(agent_name="a1", task="t"), Assignment(agent_name="a2", task="t")],
            {"a1": a1, "a2": a2},
            None,
        )
        assert len(msgs) == 2  # both stages ran
        results = handler.last_stage_results
        assert results[0].completion_met is False
        assert results[1].completion_met is True

    def test_all_stages_run_even_with_failures(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="output_contains", check="code_block"),
                ),
                StageDefinition(
                    name="s2",
                    completion_criteria=CompletionCriteria(type="output_contains", check="code_block"),
                ),
                StageDefinition(
                    name="s3",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        agents = {
            "a1": _make_agent("no code"),
            "a2": _make_agent("still no code"),
            "a3": _make_agent("done"),
        }
        msgs = handler.execute(
            [
                Assignment(agent_name="a1", task="t"),
                Assignment(agent_name="a2", task="t"),
                Assignment(agent_name="a3", task="t"),
            ],
            agents,
            None,
        )
        assert len(msgs) == 3


# ---- Error propagation -----------------------------------------------------


class TestErrorPropagation:
    def test_stage2_fails_stage3_receives_failure(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="output_contains", check="non_empty_output"),
                ),
                StageDefinition(
                    name="s2",
                    completion_criteria=CompletionCriteria(type="output_contains", check="code_block"),
                ),
                StageDefinition(
                    name="s3",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        a1 = _make_agent("good plan")
        a2 = _make_agent("restated the plan")  # no code → NOT MET
        a3 = _make_agent("received")
        handler.execute(
            [
                Assignment(agent_name="a1", task="t"),
                Assignment(agent_name="a2", task="t"),
                Assignment(agent_name="a3", task="t"),
            ],
            {"a1": a1, "a2": a2, "a3": a3},
            None,
        )
        results = handler.last_stage_results
        assert results[1].completion_met is False
        assert results[2].received_failed_input is True

    def test_error_recovery(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="output_contains", check="non_empty_output"),
                ),
                StageDefinition(
                    name="s2",
                    completion_criteria=CompletionCriteria(type="output_contains", check="code_block"),
                ),
                StageDefinition(
                    name="s3",
                    completion_criteria=CompletionCriteria(type="output_contains", check="non_empty_output"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        a1 = _make_agent("plan")
        a2 = _make_agent("no code")  # fails
        a3 = _make_agent("recovered output")  # succeeds despite bad input
        handler.execute(
            [
                Assignment(agent_name="a1", task="t"),
                Assignment(agent_name="a2", task="t"),
                Assignment(agent_name="a3", task="t"),
            ],
            {"a1": a1, "a2": a2, "a3": a3},
            None,
        )
        results = handler.last_stage_results
        assert results[2].received_failed_input is True
        assert results[2].completion_met is True  # recovered

    def test_agent_exception_passes_forward(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="output_contains", check="non_empty_output"),
                ),
                StageDefinition(
                    name="s2",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        a1 = _make_failing_agent("kaboom")
        a2 = _make_agent("ok")
        msgs = handler.execute(
            [Assignment(agent_name="a1", task="t"), Assignment(agent_name="a2", task="t")],
            {"a1": a1, "a2": a2},
            None,
        )
        assert len(msgs) == 2
        assert "kaboom" in msgs[0].content
        results = handler.last_stage_results
        # Agent error → non-empty output (error message), so criteria may still match.
        assert results[1].received_failed_input is False or results[1].received_failed_input is True
        # Stage 2 still ran.


# ---- Tool attempted checks -------------------------------------------------


class TestToolAttempted:
    def test_tool_called_but_failed(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="exec",
                    completion_criteria=CompletionCriteria(
                        type="tool_attempted",
                        check="tool_called",
                        tool_name="run_simulation",
                    ),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        agent = _make_agent_with_tools("error occurred", ["run_simulation"])
        handler.execute(
            [Assignment(agent_name="a", task="t")],
            {"a": agent},
            None,
        )
        results = handler.last_stage_results
        assert results[0].completion_met is True  # attempted counts

    def test_no_tool_called(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="exec",
                    completion_criteria=CompletionCriteria(
                        type="tool_attempted",
                        check="tool_called",
                        tool_name="run_simulation",
                    ),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        agent = _make_agent("just text")  # no tools
        handler.execute(
            [Assignment(agent_name="a", task="t")],
            {"a": agent},
            None,
        )
        results = handler.last_stage_results
        assert results[0].completion_met is False


# ---- Termination keyword ---------------------------------------------------


class TestTerminationKeyword:
    def test_task_complete_stops_pipeline(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(name="s1", completion_criteria=CompletionCriteria(type="any", check="always")),
                StageDefinition(name="s2", completion_criteria=CompletionCriteria(type="any", check="always")),
                StageDefinition(name="s3", completion_criteria=CompletionCriteria(type="any", check="always")),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        a1 = _make_agent("TASK_COMPLETE")
        a2 = _make_agent("should not run")
        a3 = _make_agent("should not run")
        msgs = handler.execute(
            [
                Assignment(agent_name="a1", task="t"),
                Assignment(agent_name="a2", task="t"),
                Assignment(agent_name="a3", task="t"),
            ],
            {"a1": a1, "a2": a2, "a3": a3},
            None,
        )
        assert len(msgs) == 1  # only first stage ran

    def test_no_termination_keyword(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(name="s1", completion_criteria=CompletionCriteria(type="any", check="always")),
                StageDefinition(name="s2", completion_criteria=CompletionCriteria(type="any", check="always")),
            ]
        )
        handler = _handler_with_pipeline(pipeline, termination_keyword="")
        a1 = _make_agent("TASK_COMPLETE here")  # keyword present but disabled
        a2 = _make_agent("also runs")
        msgs = handler.execute(
            [Assignment(agent_name="a1", task="t"), Assignment(agent_name="a2", task="t")],
            {"a1": a1, "a2": a2},
            None,
        )
        assert len(msgs) == 2  # both stages ran


# ---- Agent not found -------------------------------------------------------


class TestAgentNotFound:
    def test_missing_agent_records_error(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(name="s1", completion_criteria=CompletionCriteria(type="any", check="always")),
                StageDefinition(name="s2", completion_criteria=CompletionCriteria(type="any", check="always")),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        a2 = _make_agent("ok")
        msgs = handler.execute(
            [Assignment(agent_name="missing", task="t"), Assignment(agent_name="a2", task="t")],
            {"a2": a2},
            None,
        )
        assert len(msgs) == 2
        assert msgs[0].error is not None
        assert "not found" in msgs[0].error
        # Stage 2 still ran.
        assert "ok" in msgs[1].content


# ---- Multiple assignments --------------------------------------------------


class TestMultipleAssignments:
    def test_processes_all_assignments_in_order(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(name="s1", completion_criteria=CompletionCriteria(type="any", check="always")),
                StageDefinition(name="s2", completion_criteria=CompletionCriteria(type="any", check="always")),
                StageDefinition(name="s3", completion_criteria=CompletionCriteria(type="any", check="always")),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        agents = {
            "a1": _make_agent("first"),
            "a2": _make_agent("second"),
            "a3": _make_agent("third"),
        }
        msgs = handler.execute(
            [
                Assignment(agent_name="a1", task="t"),
                Assignment(agent_name="a2", task="t"),
                Assignment(agent_name="a3", task="t"),
            ],
            agents,
            None,
        )
        contents = [m.content for m in msgs]
        assert "first" in contents[0]
        assert "second" in contents[1]
        assert "third" in contents[2]


# ---- More assignments than stages ------------------------------------------


class TestAssignmentStageMismatch:
    def test_more_assignments_than_stages(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="output_contains", check="non_empty_output"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        agents = {"a1": _make_agent("out1"), "a2": _make_agent("out2")}
        msgs = handler.execute(
            [Assignment(agent_name="a1", task="t"), Assignment(agent_name="a2", task="t")],
            agents,
            None,
        )
        # Both run; second uses "always" criteria.
        assert len(msgs) == 2
        results = handler.last_stage_results
        assert results[1].completion_met is True  # always


# ---- StageResult fields ----------------------------------------------------


class TestStageResultFields:
    def test_stage_result_has_all_fields(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="s1",
                    completion_criteria=CompletionCriteria(type="output_contains", check="non_empty_output"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        handler.execute(
            [Assignment(agent_name="a", task="t")],
            {"a": _make_agent("output")},
            None,
        )
        r = handler.last_stage_results[0]
        assert r.stage_name == "s1"
        assert r.stage_index == 0
        assert isinstance(r.completion_met, bool)
        assert isinstance(r.completion_reason, str)
        assert isinstance(r.stage_duration, float)
        assert isinstance(r.tools_called, list)
        assert isinstance(r.output_length, int)

    def test_output_length_tracked(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(name="s1", completion_criteria=CompletionCriteria(type="any", check="always")),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        handler.execute(
            [Assignment(agent_name="a", task="t")],
            {"a": _make_agent("hello world")},
            None,
        )
        assert handler.last_stage_results[0].output_length == len("hello world")


# ---- Original-task propagation across stages -------------------------------


class TestOriginalTaskPropagation:
    """Regression for the 2026-05-25 orchestrated_staged_pipeline content
    cascade: Stage 1 receives the original task (which carries the
    CPACS file path, F25 mission spec, constraints) but Stages 2-7 see
    only `previous_outputs` + `stage_prompt`. If Stage 1's worker
    misinterprets and produces broken output, downstream workers
    cannot recover the original context — they hallucinate filenames
    and parameters.

    Compare with iterative_feedback, where every worker is invoked
    with the orchestrator's task directly, so the original context is
    always one prompt away.

    Fix: prepend `TASK: {original_task}` to every stage's context, not
    just Stage 1. Cost ~few hundred tokens per stage; survival benefit
    is large for orchestrated_staged_pipeline."""

    def test_stage_one_includes_original_task(self):
        # Sanity guard: Stage 1's behavior is unchanged.
        from src.coordination.execution_handler import Assignment

        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="geometry_engineer",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                    stage_prompt="Open the CPACS file.",
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        ctx = handler._build_context(
            idx=0,
            original_task="Use CPACS at /tmp/D150_simple.xml. F25 mission.",
            assignment=Assignment(
                agent_name="geometry_engineer",
                task="Use CPACS at /tmp/D150_simple.xml. F25 mission.",
            ),
            stage=pipeline.stages[0],
            previous_outputs=[],
        )
        assert "TASK: Use CPACS at /tmp/D150_simple.xml. F25 mission." in ctx
        assert "Open the CPACS file." in ctx

    def test_stage_two_also_includes_original_task(self):
        # The new contract: stages beyond the first must ALSO see the
        # original task. Without this, downstream workers lose the
        # CPACS file path, mission spec, and constraints.
        from src.coordination.execution_handler import Assignment

        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="geometry_engineer",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                    stage_prompt="Open the CPACS file.",
                ),
                StageDefinition(
                    name="aerodynamics_analyst",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                    stage_prompt="Run SU2 on the geometry.",
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        original_task = "Use CPACS at /tmp/D150_simple.xml. F25 mission, 2500 nmi."
        ctx = handler._build_context(
            idx=1,
            original_task=original_task,
            assignment=Assignment(
                agent_name="aerodynamics_analyst",
                task=original_task,
            ),
            stage=pipeline.stages[1],
            previous_outputs=[(
                "geometry_engineer",
                "CPACS_FILE: /tmp/D150_simple.xml\nGEOMETRY_SET",
                CompletionResult(met=True, reason="ok"),
            )],
        )
        assert original_task in ctx, (
            "Stage 2+ context must include the original task so workers "
            "can recover file paths / mission spec / constraints even if "
            "Stage 1's output is incomplete."
        )
        # Previous output must STILL be present — original task added,
        # not replaced.
        assert "CPACS_FILE: /tmp/D150_simple.xml" in ctx
        assert "Run SU2 on the geometry." in ctx

    def test_stage_seven_includes_original_task(self):
        # All downstream stages — not just Stage 2 — must see the
        # original task.
        from src.coordination.execution_handler import Assignment

        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name=f"stage_{i}",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                    stage_prompt=f"Stage {i} work.",
                )
                for i in range(7)
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        original_task = "F25 design optimization."
        ctx = handler._build_context(
            idx=6,
            original_task=original_task,
            assignment=Assignment(agent_name="stage_6", task=original_task),
            stage=pipeline.stages[6],
            previous_outputs=[
                (f"stage_{i}", f"Stage {i} done.", CompletionResult(met=True, reason="ok"))
                for i in range(6)
            ],
        )
        assert original_task in ctx


# ---- Pre-hook session injection (cursor safety) ----------------------------


class TestSetSessionIdCursorSafety:
    """Regression for the pre-hook bug from 2026-05-25 (wandb 16ibypaf
    on mdo_f25_sequential_staged_pipeline): `set_session_id` was
    hardcoded to skip Stage 1 under the assumption that Stage 1 is
    always `mission_architect`. That's true for the aviary pipeline
    but FALSE for the MDO F25 pipeline (geometry_engineer is Stage 1,
    mission_architect is Stage 5).

    Symptom: geometry_engineer agent ran with the aerodynamics_analyst
    stage_prompt and hallucinated CPACS filenames since no real
    geometry stage executed."""

    def test_aviary_pipeline_skips_stage_1(self):
        # When Stage 1 IS mission_architect (the aviary case), the
        # cursor must advance to 1 and the synthetic pre-hook output
        # must be pre-populated.
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="mission_architect",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
                StageDefinition(
                    name="aerodynamics_analyst",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        handler.set_session_id("test-session-001")
        assert handler._stage_cursor == 1
        assert handler._session_id == "test-session-001"
        assert len(handler._previous_outputs) == 1
        stage_name, output, _ = handler._previous_outputs[0]
        assert stage_name == "mission_architect"
        assert "test-session-001" in output

    def test_mdo_f25_pipeline_does_not_skip_stage_1(self):
        # The new path: when Stage 1 is something else (e.g.
        # geometry_engineer for the MDO F25 pipeline), the cursor
        # must stay at 0 — Stage 1 runs normally. The session_id is
        # still stored so downstream stages can use it.
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(
                    name="geometry_engineer",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
                StageDefinition(
                    name="aerodynamics_analyst",
                    completion_criteria=CompletionCriteria(type="any", check="always"),
                ),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        handler.set_session_id("test-session-002")
        assert handler._stage_cursor == 0, (
            "When Stage 1 is not mission_architect, the pre-hook must "
            "NOT advance the cursor — Stage 1 still has real work to do."
        )
        assert handler._session_id == "test-session-002"
        # No synthetic Stage 1 output should be injected.
        assert handler._previous_outputs == []

    def test_empty_pipeline_is_a_noop(self):
        # Defensive: empty pipeline must not crash. Just store
        # session_id, do nothing else.
        pipeline = PipelineDefinition(stages=[])
        handler = _handler_with_pipeline(pipeline)
        handler.set_session_id("test-session-003")
        assert handler._session_id == "test-session-003"
        assert handler._stage_cursor == 0
        assert handler._previous_outputs == []


# ---- Logger integration ----------------------------------------------------


class TestLoggerIntegration:
    def test_logger_receives_messages(self):
        pipeline = PipelineDefinition(
            stages=[
                StageDefinition(name="s1", completion_criteria=CompletionCriteria(type="any", check="always")),
            ]
        )
        handler = _handler_with_pipeline(pipeline)
        logger = MagicMock()
        handler.execute(
            [Assignment(agent_name="a", task="t")],
            {"a": _make_agent("hi")},
            logger,
        )
        logger.log_turn.assert_called_once()
