"""Staged Pipeline execution handler.

Deterministic assembly-line handler that moves agents through a fixed
sequence of stages.  Each stage has per-stage completion criteria that are
**observational only** — they never block advancement.  Output from stage N
(including errors) passes forward to stage N+1.

Implements the ExecutionHandler ABC.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from src.coordination.completion_criteria import (
    CompletionCriteria,
    CompletionResult,
    evaluate_completion,
)
from src.coordination.completion_signal import (
    signals_blackboard_done,
    signals_completion,
)
from src.coordination.execution_handler import Assignment, ExecutionHandler
from src.coordination.history import AgentMessage, ToolCallRecord
from src.coordination.stage_definition import (
    PipelineDefinition,
    StageDefinition,
    load_pipeline,
    load_pipeline_from_yaml,
    validate_pipeline_strict,
)
from src.logging.logger import InstrumentationLogger

# ---------------------------------------------------------------------------
# Token count extraction helper (Fix 7)
# ---------------------------------------------------------------------------


def _extract_token_count(agent: Any, content: str) -> int | None:
    """Return token count from smolagents agent, or char-based estimate."""
    for attr in ("token_count", "total_tokens"):
        val = getattr(agent, attr, None)
        if isinstance(val, int) and val > 0:
            return val
    mem = getattr(agent, "memory", None)
    steps = getattr(mem, "steps", None) if mem else None
    if steps:
        total = 0
        for step in steps:
            usage = getattr(step, "token_usage", None)
            if usage:
                if isinstance(usage, dict):
                    total += usage.get("total_tokens", 0) or (
                        usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                    )
                elif isinstance(usage, int):
                    total += usage
        if total:
            return total
    if content:
        return max(1, len(content) // 4)
    return None


# ---------------------------------------------------------------------------
# Per-stage result record (used by metrics later)
# ---------------------------------------------------------------------------


@dataclass
class StageResult:
    """Record of a single pipeline stage execution."""

    stage_name: str
    stage_index: int
    completion_met: bool
    completion_reason: str
    stage_duration: float = 0.0
    stage_tokens: int = 0
    tools_called: list[str] = field(default_factory=list)
    tools_succeeded: int = 0
    tools_failed: int = 0
    output_length: int = 0
    received_failed_input: bool = False
    agent_name: str = ""


# ---------------------------------------------------------------------------
# Context modes
# ---------------------------------------------------------------------------

_CONTEXT_MODES = {"last_only", "all_stages", "summary"}

_SUMMARY_TOKEN_LIMIT = 100  # approx tokens for summary mode


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class StagedPipelineHandler(ExecutionHandler):
    """Deterministic staged pipeline execution handler.

    Runs agents through a fixed sequence of stages.  Each stage's output
    (including errors and failures) passes forward to the next stage.
    Completion criteria are evaluated but **never block** advancement.
    """

    def __init__(self, config: dict | None = None):
        config = config or {}

        self._context_mode: str = config.get("context_mode", "last_only")
        if self._context_mode not in _CONTEXT_MODES:
            self._context_mode = "last_only"

        self._include_completion_status: bool = config.get(
            "include_completion_status",
            True,
        )
        self._termination_keyword: str = config.get(
            "termination_keyword",
            "TASK_COMPLETE",
        )

        # Abort pipeline early when a stage exhausts all its
        # set_aircraft_parameters attempts without ever getting valid=true.
        # set_aircraft_parameters runs the static checks + Aviary model
        # evaluation inline, so the "valid" boolean lives on every set
        # response (the standalone validate_parameters tool was removed
        # 2026-05-23).  Set to False to restore the original
        # unconditional-advance behaviour.
        self._abort_on_validation_exhaustion: bool = config.get(
            "abort_on_validation_exhaustion",
            True,
        )

        # Configurable patterns.
        self._code_block_patterns: list[str] | None = config.get(
            "code_block_patterns",
        )
        self._verdict_patterns: list[str] | None = config.get(
            "verdict_patterns",
        )

        # Pipeline definition.
        self._pipeline: PipelineDefinition | None = None
        pipeline_name = config.get("pipeline", "aviary")

        if pipeline_name == "custom":
            custom_stages = config.get("custom_stages", [])
            if custom_stages:
                self._pipeline = load_pipeline({"stages": custom_stages})
        elif "stages" in config:
            # Inline stages in the config dict itself.
            self._pipeline = load_pipeline(config)

        # Store raw config for deferred loading.
        self._config = config

        # Persistent stage cursor — tracks which stage we're on across
        # multiple execute() calls (coordinator calls execute() once per
        # strategy turn with a single assignment).
        self._stage_cursor: int = 0
        self._previous_outputs: list[tuple[str, str, CompletionResult]] = []

        # Pre-hook session_id — when set, Stage 1 (mission_architect) is
        # skipped because the session already exists with mission configured.
        self._session_id: str | None = None

    def set_session_id(self, session_id: str) -> None:
        """Inject a pre-created session_id from the stat_batch_runner
        pre-hook. The pre-hook calls create_session + configure_mission
        with the Aviary defaults (1500 nmi / 162 pax / Mach 0.785 /
        FL350).

        If the pipeline's Stage 1 IS ``mission_architect`` (the
        Aviary pipeline), this skips Stage 1 because the pre-hook
        already did its work. The cursor advances to 1 and the
        synthetic pre-hook output is pre-populated as Stage 1's
        result so downstream stages see the session_id in their
        "previous stage" context.

        If Stage 1 is anything else (e.g. ``geometry_engineer`` in
        the MDO F25 pipeline, where mission_architect is Stage 5 and
        reconfigures the mission for the F25 spec), the cursor stays
        at 0 — Stage 1 runs normally. The session_id is stored so
        downstream stages can still pick it up.
        """
        self._session_id = session_id

        pipeline = self._resolve_pipeline()
        if not pipeline.stages:
            return
        stage_1_name = pipeline.stages[0].name
        if stage_1_name != "mission_architect":
            # Pipeline doesn't start with the pre-hook target —
            # don't advance the cursor. session_id is stored for
            # downstream context injection only.
            return

        # Pre-populate Stage 1 output so downstream stages see the
        # session_id in their "previous stage" context.
        pre_output = (
            f"Session pre-created by system hook.\n"
            f"SESSION_ID: {session_id}\n"
            f"Mission: 1500 nmi, 162 pax, Mach 0.785, FL350.\n"
            f"Use this session_id for ALL subsequent tool calls."
        )
        result = CompletionResult(
            met=True,
            reason="pre-hook completed session setup",
            evidence=f"session_id={session_id}",
        )
        self._previous_outputs.append(("mission_architect", pre_output, result))
        self._stage_cursor = 1  # skip Stage 1

    # ------------------------------------------------------------------
    # ExecutionHandler interface
    # ------------------------------------------------------------------

    def execute(
        self,
        assignments: list[Assignment],
        agents: dict,
        logger: InstrumentationLogger | None,
        turn_offset: int = 0,
        action_metadata: dict | None = None,
    ) -> list[AgentMessage]:
        """Run agents through the staged pipeline.

        The pipeline stages are matched to assignments in order.  If there
        are more stages than assignments, surplus stages are skipped.  If
        there are more assignments than stages, surplus assignments use an
        ``always`` completion criteria.
        """
        _base_meta = dict(action_metadata or {})
        pipeline = self._resolve_pipeline()

        messages: list[AgentMessage] = []
        stage_results: list[StageResult] = []
        turn = turn_offset
        previous_outputs = self._previous_outputs
        # Each entry: (stage_name, content, completion_result)

        # Extract the original task from the first assignment BEFORE
        # reordering, so the structural-context propagation isn't
        # affected by name-based matching.
        original_task = assignments[0].task if assignments else ""

        num_stages = len(pipeline.stages)

        # Reorder assignments to pair each stage with its same-named
        # agent when possible. Order-based pairing was the cause of
        # the 2026-05-25 orchestrated_staged_pipeline cascade (wandb
        # 7wbgfu74): the orchestrator's assign_task order was
        # arbitrary, so Stage 1's geometry_engineer prompt was
        # delivered to whatever agent happened to be assigned first
        # (e.g. simulation_executor), which couldn't call open_cpacs
        # and the stage's completion criterion missed. For sequential
        # strategy this reorder is a no-op because stages and agents
        # are constructed in the same order with matching names.
        active_stages = pipeline.stages[self._stage_cursor:]
        name_to_assignment: dict[str, Assignment] = {}
        for a in assignments:
            # Preserve original-position priority on name collisions
            # (the orchestrator can call assign_task multiple times
            # for the same agent — keep the first).
            if a.agent_name not in name_to_assignment:
                name_to_assignment[a.agent_name] = a
        ordered: list[Assignment] = []
        consumed: set[int] = set()
        for stage in active_stages:
            matched = name_to_assignment.get(stage.name)
            if matched is not None:
                # Find the index in the original assignments list and
                # mark it consumed so the unmatched-fallback pass
                # below doesn't duplicate.
                for i, a in enumerate(assignments):
                    if a is matched and i not in consumed:
                        consumed.add(i)
                        ordered.append(a)
                        break
        for i, a in enumerate(assignments):
            if i not in consumed:
                ordered.append(a)
        assignments = ordered

        for assign_idx, assignment in enumerate(assignments):
            # Use persistent cursor so stage advances across execute() calls.
            idx = self._stage_cursor + assign_idx
            turn += 1
            stage: StageDefinition | None = pipeline.stages[idx] if idx < num_stages else None

            # Early-exit: if the incoming context already signals task
            # completion (e.g. task_complete written to the blackboard by
            # MarkTaskDone in a networked strategy), skip agent invocation
            # and return a pass-through message.  This is the programmatic
            # completion gate — mirrors graph_routed's terminal-state check.
            if self._is_task_complete_in_context(assignment.task, previous_outputs):
                stage_name = stage.name if stage else f"stage_{idx}"
                msg = AgentMessage(
                    agent_name=assignment.agent_name,
                    content=assignment.task,
                    turn_number=turn,
                    timestamp=time.time(),
                    metadata={
                        **_base_meta,
                        "stage_name": stage_name,
                        "stage_index": idx,
                        "completion_met": True,
                        "completion_reason": "task_complete_detected_in_context",
                        "received_failed_input": False,
                    },
                )
                messages.append(msg)
                if logger is not None:
                    logger.log_turn(msg)
                stage_results.append(
                    StageResult(
                        stage_name=stage_name,
                        stage_index=idx,
                        completion_met=True,
                        completion_reason="task_complete_detected_in_context",
                        agent_name=assignment.agent_name,
                    )
                )
                break

            # Build completion criteria (use stage's or fallback to always).
            criteria = (
                stage.completion_criteria if stage is not None else CompletionCriteria(type="any", check="always")
            )

            # Check if we received failed input from previous stage.
            received_failed = False
            if previous_outputs:
                _, _, prev_result = previous_outputs[-1]
                received_failed = not prev_result.met

            # Build context for this stage.
            context = self._build_context(
                idx=idx,
                original_task=original_task,
                assignment=assignment,
                stage=stage,
                previous_outputs=previous_outputs,
            )

            # Resolve agent.
            agent = agents.get(assignment.agent_name)
            if agent is None:
                # Agent missing — record error, still advance.
                _stage_name = stage.name if stage else f"stage_{idx}"
                msg = AgentMessage(
                    agent_name=assignment.agent_name,
                    content="",
                    turn_number=turn,
                    timestamp=time.time(),
                    error=f"Agent '{assignment.agent_name}' not found",
                    metadata={
                        **_base_meta,
                        "stage_name": _stage_name,
                        "stage_index": idx,
                        "completion_met": False,
                        "completion_reason": "Agent not found",
                        "received_failed_input": received_failed,
                    },
                )
                messages.append(msg)
                if logger is not None:
                    logger.log_turn(msg)

                result = CompletionResult(
                    met=False,
                    reason="Agent not found",
                    evidence=f"Agent '{assignment.agent_name}' not in agents dict",
                )
                previous_outputs.append(
                    (
                        stage.name if stage else f"stage_{idx}",
                        "",
                        result,
                    )
                )
                stage_results.append(
                    StageResult(
                        stage_name=stage.name if stage else f"stage_{idx}",
                        stage_index=idx,
                        completion_met=False,
                        completion_reason="Agent not found",
                        received_failed_input=received_failed,
                        agent_name=assignment.agent_name,
                    )
                )
                continue

            # Run the agent.
            start = time.monotonic()
            try:
                raw_result = agent.run(context)
                content = str(raw_result) if raw_result is not None else ""
            except Exception as e:
                content = f"Agent error: {e}"

            duration = time.monotonic() - start

            # Extract tool calls and token count from agent (Fix 6 + Fix 7).
            tool_calls = self._extract_tool_calls(agent)
            token_count = _extract_token_count(agent, content)
            stage_name_for_meta = stage.name if stage else f"stage_{idx}"

            # Build AgentMessage with pre-run stage context.
            msg = AgentMessage(
                agent_name=assignment.agent_name,
                content=content,
                turn_number=turn,
                timestamp=time.time(),
                duration_seconds=duration,
                tool_calls=tool_calls,
                token_count=token_count,
                metadata={
                    **_base_meta,
                    "stage_name": stage_name_for_meta,
                    "stage_index": idx,
                    "received_failed_input": received_failed,
                },
            )
            messages.append(msg)
            if logger is not None:
                logger.log_turn(msg)

            # Evaluate completion criteria (observational only).
            comp_result = evaluate_completion(
                criteria,
                content,
                tool_calls,
                code_block_patterns=self._code_block_patterns,
                verdict_patterns=self._verdict_patterns,
            )

            # Backfill completion outcome into message metadata.
            msg.metadata["completion_met"] = comp_result.met
            msg.metadata["completion_reason"] = comp_result.reason

            # Record stage output.
            stage_name = stage.name if stage else f"stage_{idx}"
            previous_outputs.append((stage_name, content, comp_result))

            # Record stage result for metrics.
            tools_called = [tc.tool_name for tc in tool_calls]
            tools_succeeded = sum(1 for tc in tool_calls if tc.error is None)
            tools_failed = sum(1 for tc in tool_calls if tc.error is not None)

            stage_results.append(
                StageResult(
                    stage_name=stage_name,
                    stage_index=idx,
                    completion_met=comp_result.met,
                    completion_reason=comp_result.reason,
                    stage_duration=duration,
                    stage_tokens=msg.token_count or 0,
                    tools_called=tools_called,
                    tools_succeeded=tools_succeeded,
                    tools_failed=tools_failed,
                    output_length=len(content),
                    received_failed_input=received_failed,
                    agent_name=assignment.agent_name,
                )
            )

            # Check for early termination keyword.
            if self._termination_keyword and self._termination_keyword in content:
                break

            # Abort if stage called set_aircraft_parameters but never
            # got valid=true on its inline validation (all attempts
            # exhausted). Avoids wasting time running simulation with
            # known-bad parameters.
            if self._abort_on_validation_exhaustion and self._validation_exhausted(tool_calls):
                msg.metadata["early_abort"] = "validation_exhausted"
                break

        # Advance persistent cursor for next execute() call.
        self._stage_cursor += len(assignments)
        self._previous_outputs = previous_outputs

        # If all pipeline stages are done, append TASK_COMPLETE to the
        # last message so the strategy's is_complete() detects termination.
        pipeline = self._resolve_pipeline()
        if self._stage_cursor >= len(pipeline.stages) and messages:
            last_msg = messages[-1]
            if self._termination_keyword and self._termination_keyword not in last_msg.content:
                last_msg.content += f"\n{self._termination_keyword}"

        # Attach stage results to handler for metrics collection.
        self._last_stage_results = stage_results

        return messages

    # ------------------------------------------------------------------
    # Public accessors (for metrics)
    # ------------------------------------------------------------------

    @property
    def last_stage_results(self) -> list[StageResult]:
        """Return the stage results from the most recent execute() call."""
        return getattr(self, "_last_stage_results", [])

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    def _build_context(
        self,
        idx: int,
        original_task: str,
        assignment: Assignment,
        stage: StageDefinition | None,
        previous_outputs: list[tuple[str, str, CompletionResult]],
    ) -> str:
        """Build the input context for a pipeline stage."""
        stage_name = stage.name if stage else f"stage_{idx}"
        stage_prompt = stage.stage_prompt if stage else ""

        if idx == 0:
            # First stage: original task + stage prompt.
            # When no real stage is configured (empty pipeline), skip the
            # "STAGE:" wrapper — it misleads agents into ignoring prior work
            # on the blackboard by framing them as "starting fresh".
            if stage is None:
                return assignment.task
            parts = [f"STAGE: {stage_name}"]
            parts.append(f"TASK: {assignment.task}")
            if stage_prompt:
                parts.append("")
                parts.append(stage_prompt)
            return "\n".join(parts)

        # Subsequent stages: original task + previous output(s) + stage prompt.
        #
        # The original task carries authoritative context that previous-
        # stage outputs can't fully reconstruct: CPACS file paths,
        # mission spec, constraint thresholds. Without it, downstream
        # workers in orchestrated_staged_pipeline (where each worker has
        # only the orchestrator-supplied persona, not the discipline
        # role from agents YAML) hallucinate filenames and parameters.
        # Sequential_staged_pipeline never showed the symptom because
        # its workers inherit detailed roles that pre-encode the path —
        # but the structural gap exists for both, and prepending the
        # task here is a uniform fix.
        parts = [f"STAGE: {stage_name}"]
        if original_task:
            parts.append(f"TASK: {original_task}")
            parts.append("")

        if self._context_mode == "last_only":
            self._append_last_output(parts, previous_outputs)
        elif self._context_mode == "all_stages":
            self._append_all_outputs(parts, previous_outputs)
        elif self._context_mode == "summary":
            self._append_summary(parts, previous_outputs)

        if stage_prompt:
            parts.append("")
            parts.append(stage_prompt)

        return "\n".join(parts)

    def _append_last_output(
        self,
        parts: list[str],
        previous_outputs: list[tuple[str, str, CompletionResult]],
    ) -> None:
        """Append only the last stage's output."""
        if not previous_outputs:
            return
        prev_name, prev_content, prev_result = previous_outputs[-1]
        parts.append(f"PREVIOUS STAGE: {prev_name}")

        if self._include_completion_status:
            status = "MET" if prev_result.met else "NOT MET"
            parts.append(f"COMPLETION STATUS: {status} — {prev_result.reason}")

        parts.append("")
        parts.append("Previous stage output:")
        parts.append(prev_content)

        if not prev_result.met and self._include_completion_status:
            parts.append("")
            parts.append("NOTE: The previous stage did not fully complete. Work with whatever output is available.")

    def _append_all_outputs(
        self,
        parts: list[str],
        previous_outputs: list[tuple[str, str, CompletionResult]],
    ) -> None:
        """Append all previous stages' outputs."""
        for i, (name, content, result) in enumerate(previous_outputs):
            if i > 0:
                parts.append("")
                parts.append("---")
            parts.append(f"PREVIOUS STAGE ({i + 1}): {name}")

            if self._include_completion_status:
                status = "MET" if result.met else "NOT MET"
                parts.append(f"COMPLETION STATUS: {status} — {result.reason}")

            parts.append("")
            parts.append(f"Stage {name} output:")
            parts.append(content)

    def _append_summary(
        self,
        parts: list[str],
        previous_outputs: list[tuple[str, str, CompletionResult]],
    ) -> None:
        """Append a summary of all previous stages."""
        parts.append("PREVIOUS STAGES SUMMARY:")
        for i, (name, content, result) in enumerate(previous_outputs):
            status = "MET" if result.met else "NOT MET"
            # Truncate content to approximate token limit.
            truncated = content[: _SUMMARY_TOKEN_LIMIT * 4]
            if len(content) > _SUMMARY_TOKEN_LIMIT * 4:
                truncated += "..."
            parts.append(f"  Stage {i + 1} ({name}): [{status}] {truncated}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_task_complete_in_context(
        self,
        task_context: str,
        previous_outputs: list[tuple[str, str, CompletionResult]],
    ) -> bool:
        """Return True if task completion has been signalled programmatically.

        Checks for the ``task_complete`` blackboard key (written by
        MarkTaskDone) in the incoming context string, and for the
        termination keyword in any prior stage output.  This mirrors the
        terminal-state gate in GraphRoutedHandler.

        Detection goes through :mod:`src.coordination.completion_signal`, which
        ignores (a) negated mentions and (b) the forwarded previous-link
        feedback block.  A bare substring scan here caused the phantom-link bug
        (.claude/BUGS.md B2): the integrator's ``"Not TASK_COMPLETE: ... fuel
        violates its limit"`` verdict, forwarded into link k>0's task by the
        cross-link feedback, matched — so every stage was skipped and the link
        ran in 0.17 s with 0 tokens while re-reporting the prior link's fuel.
        """
        # Blackboard entry written by MarkTaskDone: "[STATUS] task_complete"
        if signals_blackboard_done(task_context):
            return True

        # Termination keyword asserted anywhere in the incoming context.
        if signals_completion(task_context, self._termination_keyword):
            return True

        # Termination keyword in any prior stage output from this execute() call.
        for _, content, _ in previous_outputs:
            if signals_completion(content, self._termination_keyword):
                return True

        return False

    @staticmethod
    def _validation_exhausted(tool_calls: list[ToolCallRecord]) -> bool:
        """Return True if set_aircraft_parameters was called but never returned valid=true.

        set_aircraft_parameters runs the inline static-check + Aviary
        model-evaluation validation that the deprecated validate_parameters
        tool used to do, and surfaces the result on its "valid" field.
        """
        import json

        sp_calls = [tc for tc in tool_calls if tc.tool_name == "set_aircraft_parameters"]
        if not sp_calls:
            return False
        for tc in sp_calls:
            try:
                parsed = json.loads(tc.output) if tc.output else {}
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            if isinstance(parsed, dict) and parsed.get("valid") is True:
                return False  # at least one succeeded
        return True  # all calls returned valid=false (or unparseable)

    def _resolve_pipeline(self) -> PipelineDefinition:
        """Resolve pipeline definition, loading from config if needed."""
        if self._pipeline is not None:
            return self._pipeline

        # Try loading from pipeline_path config.
        pipeline_path = self._config.get("pipeline_path")
        if pipeline_path:
            self._pipeline = load_pipeline_from_yaml(pipeline_path)
            validate_pipeline_strict(self._pipeline)
            return self._pipeline

        # Fallback: create a minimal "always" pipeline.
        self._pipeline = PipelineDefinition(stages=[])
        return self._pipeline

    def _extract_tool_calls(self, agent: Any) -> list[ToolCallRecord]:
        """Extract tool call records from the agent after a run.

        smolagents agents store tool call info internally. We extract what
        we can. If the agent doesn't expose tool calls, return empty.
        """
        tool_calls: list[ToolCallRecord] = []

        logs = getattr(agent, "logs", None) or []
        if not logs:
            mem = getattr(agent, "memory", None)
            logs = getattr(mem, "steps", None) or []

        for entry in logs:
            step_tool_calls = getattr(entry, "tool_calls", None)
            if not step_tool_calls:
                continue
            obs = getattr(entry, "observations", "") or ""
            for tc in step_tool_calls:
                name = getattr(tc, "name", "") or getattr(tc, "tool_name", "")
                args = getattr(tc, "arguments", {}) or {}
                tool_calls.append(
                    ToolCallRecord(
                        tool_name=name,
                        inputs=args if isinstance(args, dict) else {},
                        output=str(obs)[:2000] if obs else "",
                        duration_seconds=0.0,
                    )
                )

        return tool_calls
