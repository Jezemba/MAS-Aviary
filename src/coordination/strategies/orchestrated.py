"""Orchestrated coordination strategy.

An LLM-powered orchestrator agent dynamically creates a team of
specialist agents at runtime, assigns them roles and tool subsets,
and manages authority. This is the fourth strategy alongside
sequential, graph_routed, and networked.

Phases:
  1. Team creation — orchestrator reasons about the task, discovers
     tools, creates specialist agents, and assigns subtasks.
  2. Execution — workers execute assignments via an ExecutionHandler
     (placeholder or future operational methodology).

Modes:
  - lifecycle_mode: "active" (orchestrator stays in loop) or
    "setup_only" (orchestrator exits after team creation)
  - authority_mode: "orchestrator" (fixed), "delegated" (transfer
    after N prompts), or "manual" (user-specified)
  - information_mode: "transparent" (full output) or "opaque"
    (SUCCESS/FAILED only)
"""

import re

from src.coordination.completion_signal import signals_completion
from src.coordination.execution_handler import PlaceholderExecutor
from src.coordination.history import AgentMessage
from src.coordination.strategy import CoordinationAction, CoordinationStrategy
from src.llm.reliability import ReliabilityConfig, make_first_step_guardrail
from src.tools.orchestrator_tools import (
    DELEGATION_COMPLETE,
    ORCHESTRATOR_TOOL_NAMES,
    AssignTask,
    CreateAgent,
    GatedFinalAnswer,
    ListAvailableTools,
    ListGraphRoles,
    OrchestratorContext,
    _check_result_signals,
)

# Sentinel used by _sync_orchestrator_tools to store the GatedFinalAnswer
# instance so it can be added/removed from the agent's tool dict dynamically.
_FINAL_ANSWER_KEY = "final_answer"


def _inject_skill_reference(orchestrator_agent, skill_loader) -> None:
    """Append the data-plane coupling reference from the skill folder to
    the orchestrator's system prompt.

    Reads ``skills/<configured>/references/data_flow.md`` via SkillLoader
    and appends it under a clearly-marked section so the orchestrator
    knows the active data-plane couplings without those being baked into
    the YAML prompt. Different design task = different skill folder =
    different couplings; the orchestrator YAML stays generic.

    Silent no-op if no skill loader is configured, the skill folder is
    missing, or data_flow.md is empty. The orchestrator can still
    function from its YAML prompt alone.
    """
    if skill_loader is None or not getattr(skill_loader, "is_available", False):
        return
    reference_md = skill_loader.load_reference("data_flow.md")
    if not reference_md:
        return

    appendix = (
        "\n\n"
        "============================================================\n"
        "ACTIVE DATA-PLANE COUPLINGS (loaded from skill at runtime —\n"
        "not part of the generic orchestrator prompt). The framework's\n"
        "data plane captures values from one discipline's tool output\n"
        "and injects them into the next discipline's tool input\n"
        "automatically — but ONLY when the producing tool runs BEFORE\n"
        "the consuming tool. Read the coupling table below before you\n"
        "decide the order in which to create workers and assign tasks.\n"
        "============================================================\n\n"
    ) + reference_md

    mem = getattr(orchestrator_agent, "memory", None)
    sp_holder = getattr(mem, "system_prompt", None) if mem is not None else None
    if sp_holder is None:
        return
    if hasattr(sp_holder, "system_prompt"):
        # smolagents wraps the prompt in a holder object whose .system_prompt
        # attribute is the raw string. Append to that string.
        sp_holder.system_prompt = (sp_holder.system_prompt or "") + appendix
    else:
        # Older shape: memory.system_prompt is the string directly.
        mem.system_prompt = (str(sp_holder) if sp_holder else "") + appendix


class OrchestratedStrategy(CoordinationStrategy):
    """Dynamic team-creation strategy with orchestrator agent."""

    def __init__(self):
        # Config.
        self._authority_mode: str = "orchestrator"
        self._authority_transfer_after: int = 5
        self._manual_authority_agent: str | None = None
        self._information_mode: str = "transparent"
        self._lifecycle_mode: str = "active"
        self._max_agents: int = 8
        self._max_orchestrator_turns: int = 5
        self._worker_max_steps: int = 8
        self._termination_keyword: str = "TASK_COMPLETE"
        self._pending_phase_note: str | None = None
        self._phase_gap_reported: bool = False
        self._phase_gap_pending: str | None = None
        self._phase_gap_ignored: str | None = None
        self._max_turns: int = 30
        self._stall_threshold: int = 2  # consecutive no-progress turns before terminating

        # Runtime state.
        self._phase: str = "creation"  # "creation" | "execution" | "done"
        self._orchestrator_name: str = "orchestrator"
        self._orchestrator_turns_used: int = 0
        self._execution_index: int = 0  # for active mode worker iteration
        self._agents: dict = {}
        self._context: OrchestratorContext | None = None
        self._executor = None
        self._total_turns: int = 0
        self._graph_roles: list[str] | None = None  # roles from graph-routed handler
        self._pipeline_stage_names: list[str] | None = None  # stage names from staged_pipeline handler

        # Per-stage delegation (lifecycle_mode="per_stage"): the
        # orchestrator delegates ONE pipeline stage at a time, with
        # the previous stage's output/error in its input context.
        # Advances after each stage runs. Designed for orchestrated
        # + staged_pipeline where the orchestrator can't reliably
        # plan all 7 disciplines' tool needs upfront.
        self._current_stage_idx: int = 0
        # Furthest pipeline stage the run has reached. A staged pipeline is a
        # LINEAR sequence, so progression must be monotonic: retry the current
        # stage yes, skip forward yes, go BACKWARDS no. Measured 2026-08-17
        # (B57): after completing stages 1-4 the orchestrator re-assigned
        # geometry_engineer (stage 1) and the cursor simply followed it, turning
        # the pipeline into a free-form loop -- 9 of 30 turns spent without ever
        # reaching stages 5-7, so no mission, no verdict and no fuel figure.
        self._max_stage_reached: int = 0

        # Delegation progress tracking (detects stalls / direct completion).
        self._prev_created_count: int = 0
        self._prev_assignment_count: int = 0
        self._stall_turns: int = 0

        # Result signal scanning: track how far we've scanned in history.
        self._signals_scanned_up_to: int = 0
        # Number of times we've retried via orchestrator due to failed signals.
        self._signal_retry_count: int = 0
        self._max_signal_retries: int = 2  # max orchestrator re-invocations

        # Authority transfer state.
        self._prompts_completed: int = 0
        self._authority_agent: str = "orchestrator"

        # Direct session_id injection (set via set_session_id).
        self._session_id: str | None = None

    def set_session_id(self, session_id: str) -> None:
        """Inject session_id directly — avoids regex extraction from history."""
        self._session_id = session_id

    def initialize(self, agents: dict, config: dict) -> None:
        """Set up strategy from agents dict and coordination config."""
        orch_config = config.get("orchestrated", {})

        # Read config values.
        self._authority_mode = orch_config.get("authority_mode", "orchestrator")
        self._authority_transfer_after = orch_config.get("authority_transfer_after", 5)
        self._manual_authority_agent = orch_config.get("manual_authority_agent")
        self._information_mode = orch_config.get("information_mode", "transparent")
        self._lifecycle_mode = orch_config.get("lifecycle_mode", "active")
        self._max_agents = orch_config.get("max_agents", 8)
        self._max_orchestrator_turns = orch_config.get("max_orchestrator_turns", 5)
        self._worker_max_steps = orch_config.get("worker_max_steps", 8)
        self._stall_threshold = orch_config.get("stall_threshold", 2)

        term_config = config.get("termination", {})
        self._termination_keyword = term_config.get("keyword", "TASK_COMPLETE")
        self._max_turns = term_config.get("max_turns", 30)

        # Identify orchestrator and extract model.
        self._orchestrator_name = orch_config.get("orchestrator_agent", "orchestrator")
        if self._orchestrator_name not in agents:
            raise ValueError(
                f"Orchestrator agent '{self._orchestrator_name}' not found in agents. Available: {list(agents.keys())}"
            )

        orchestrator_agent = agents[self._orchestrator_name]
        model = orchestrator_agent.model
        self._model = model  # Store for thinking toggle.

        # Skill content (data-plane coupling map etc.) — load at init
        # time and append to the orchestrator's system prompt. This keeps
        # task-specific coupling info OUT of the generic orchestrator
        # YAML; a different design task swaps the skill folder. Plumbed
        # in from Coordinator.from_config when config.skills.path is set.
        _inject_skill_reference(orchestrator_agent, config.get("_skill_loader"))

        # Collect available worker tools from config or extract from agents.
        worker_tools = config.get("_worker_tools", {})
        if not worker_tools:
            # Fallback: collect tools from all agents, excluding orchestrator tools.
            for agent in agents.values():
                for tname, tool in agent.tools.items():
                    if tname not in ORCHESTRATOR_TOOL_NAMES and tname != "final_answer":
                        worker_tools[tname] = tool

        # Build shared context. Use the SAME dict reference so dynamically
        # created agents are visible to the Coordinator's run loop.
        system_agent_names = set(agents.keys())
        self._agents = agents

        # Determine if first-step guardrail should be enabled.  Works for
        # both ThinkingModel (has _reliability attr) and OpenAIServerModel
        # (check reliability config passed through coordinator).
        guardrail_checks: list = []
        use_guardrail = False
        reliability_cfg = getattr(model, "_reliability", None)
        if isinstance(reliability_cfg, ReliabilityConfig):
            use_guardrail = reliability_cfg.first_step_guardrail
        else:
            rel = config.get("_reliability_config") or {}
            use_guardrail = rel.get("first_step_guardrail", False)
        if use_guardrail:
            guardrail_checks = make_first_step_guardrail()

        self._context = OrchestratorContext(
            available_tools=worker_tools,
            agents=self._agents,
            model=model,
            system_agent_names=system_agent_names,
            max_agents=self._max_agents,
            worker_max_steps=self._worker_max_steps,
            worker_final_answer_checks=guardrail_checks,
            on_delegation_change=self._sync_orchestrator_tools,
            required_tool_phases=config.get("_required_tool_phases", {}),
            required_result_signals=config.get("_required_result_signals", []),
            lifecycle_mode=self._lifecycle_mode,
        )

        # Create orchestrator management tools.
        # GatedFinalAnswer is held separately — it is only injected into the
        # agent's tool dict once the preconditions are satisfied (at least one
        # agent created AND one task assigned).  By removing it from the
        # schema entirely the model physically cannot generate a final_answer
        # call until delegation has occurred.  This is a model-level
        # constraint (structured tool gating), not prompt-level guidance.
        self._gated_final_answer = GatedFinalAnswer(self._context)
        orch_tools = [
            ListAvailableTools(self._context),
            CreateAgent(self._context),
            AssignTask(self._context),
        ]

        # If graph roles are configured, give the orchestrator a tool to
        # discover them.  This is a deterministic Python function (not a
        # prompt hint) so the model gets structured role data it can act on.
        graph_roles = config.get("_graph_roles")
        if graph_roles:
            orch_tools.append(ListGraphRoles(graph_roles=graph_roles))
        else:
            # Without roles there is no tool, so the orchestrator must not be
            # told it exists -- it called list_graph_roles 4 times across the
            # 2026-08-12 sweeps and got "Unknown tool" each time. The prompt is
            # built from the tools actually attached, so simply not attaching it
            # is sufficient; this comment marks that the omission is deliberate.
            pass

        for tool in orch_tools:
            orchestrator_agent.tools[tool.name] = tool
        # Remove any built-in final_answer so the model cannot call it
        # until _sync_orchestrator_tools adds it back.
        orchestrator_agent.tools.pop(_FINAL_ANSWER_KEY, None)

        # Handle manual authority mode.
        if self._authority_mode == "manual" and self._manual_authority_agent:
            self._authority_agent = self._manual_authority_agent
            if self._manual_authority_agent in agents:
                # Transfer orchestrator tools to the designated agent.
                manual_agent = agents[self._manual_authority_agent]
                for tool in orch_tools:
                    manual_agent.tools[tool.name] = tool
                self._orchestrator_name = self._manual_authority_agent
        else:
            self._authority_agent = self._orchestrator_name

        # Set up placeholder executor for setup_only mode.
        self._executor = PlaceholderExecutor(
            termination_keyword=self._termination_keyword,
            max_turns=self._max_turns,
        )

        # Graph role hints — when using a graph-routed execution handler,
        # inject required role names so the orchestrator creates matching agents.
        self._graph_roles = config.get("_graph_roles")

        # Pipeline stage names — when using a staged_pipeline execution
        # handler, reorder ctx.assignments at execution-phase entry so
        # each stage gets the worker whose name matches it. Without
        # this, the orchestrator's arbitrary assign_task order pairs
        # the wrong worker with each stage_prompt (observed 2026-05-25
        # in wandb 7wbgfu74: simulation_executor ran Stage 1's
        # geometry_engineer prompt and never called open_cpacs).
        self._pipeline_stage_names = config.get("_pipeline_stage_names")

        # Reset phase state.
        self._phase = "creation"
        self._orchestrator_turns_used = 0
        self._execution_index = 0
        self._total_turns = 0
        self._prev_created_count = 0
        self._prev_assignment_count = 0
        self._stall_turns = 0
        self._current_stage_idx = 0
        self._max_stage_reached = 0
        self._signals_scanned_up_to = 0
        self._signal_retry_count = 0

    def _update_result_signals(self, history: list) -> None:
        """Scan new history entries for result signals.

        Currently detects:
        - ``simulation_attempted``: any ``run_simulation`` tool call was
          observed (regardless of outcome).
        - ``simulation_succeeded``: a ``run_simulation`` tool call whose
          output contains ``"success": true`` or ``"success":true``.
        """
        if not self._context or not self._context.required_result_signals:
            return

        for msg in history[self._signals_scanned_up_to :]:
            if not isinstance(msg, AgentMessage):
                continue
            for tc in msg.tool_calls:
                if tc.tool_name == "run_simulation":
                    self._context.result_signals.add("simulation_attempted")
                    if tc.output and not tc.error:
                        out = tc.output
                        if '"success": true' in out or '"success":true' in out or "'success': True" in out:
                            self._context.result_signals.add("simulation_succeeded")
        self._signals_scanned_up_to = len(history)

    def next_step(self, history: list, current_state: dict) -> CoordinationAction:
        """Decide the next action based on current phase."""
        self._total_turns = len(history)
        self._update_result_signals(history)

        if self._phase == "creation":
            action = self._creation_step(history, current_state)
        elif self._phase == "execution":
            action = self._execution_step(history, current_state)
        else:
            action = CoordinationAction(
                action_type="terminate",
                agent_name=None,
                input_context="",
                metadata={"reason": "orchestration_complete"},
            )

        # Toggle thinking mode: OFF for orchestrator (needs reliable JSON
        # for CreateAgent/AssignTask), ON for workers (benefit from
        # reasoning before parameter decisions).
        if hasattr(self._model, "thinking_enabled"):
            is_orchestrator = action.agent_name == self._orchestrator_name
            self._model.thinking_enabled = not is_orchestrator

        return action


    # -- Required-phase enforcement --------------------------------------------

    def _phases_done(self, history: list) -> set:
        """Which declared phases have actually executed, from observed calls."""
        phases = (self._context.required_tool_phases if self._context else None) or {}
        if not phases:
            return set()
        called: set = set()
        for msg in history or []:
            for tc in getattr(msg, "tool_calls", None) or []:
                name = getattr(tc, "tool_name", None) or getattr(tc, "name", None)
                if name:
                    called.add(name)
        return {
            phase for phase, tools in phases.items()
            if tools and all(tool in called for tool in tools)
        }

    def _next_required_phase(self, history: list):
        """The first declared phase, IN ORDER, that has not executed.

        `required_tool_phases` is an ordered mapping in the agents YAML, and that
        order IS the dependency order: geometry before aero (aero needs a mesh),
        aero before mission (the mission needs the coefficients). Returning the
        FIRST unexecuted phase therefore answers both "are we done?" and "what
        comes next?" with one lookup.
        """
        phases = (self._context.required_tool_phases if self._context else None) or {}
        done = self._phases_done(history)
        for phase, tools in phases.items():
            if phase not in done:
                return phase, tools
        return None, None

    def is_complete(self, history: list, current_state: dict) -> bool:
        """Check if the orchestration is finished."""
        if self._phase == "done":
            return True

        # Check termination keyword ASSERTED in last message — negated
        # mentions and quoted previous-link feedback do not count.
        # See src/coordination/completion_signal.py and .claude/BUGS.md B2.
        if history:
            last = history[-1]
            content = last.content if isinstance(last, AgentMessage) else str(last)
            if signals_completion(content, self._termination_keyword):
                # `required_tool_phases` declares the phases a run must complete
                # and the tools each needs, but was only ever used to build a
                # prompt hint -- nothing checked that a declared phase ran.
                #
                # Measured 2026-08-12, both orchestrated_staged_pipeline runs
                # (49 and 50 steps, ZERO errors): the orchestrator re-assigned
                # structures 3x and aero 2x, never assigned mission_setup or
                # simulation, and the run finished reporting zero fuel. The
                # framework knew mission_setup was required, watched it never
                # happen, and called the run complete.
                #
                # Declaration order is dependency order, so the first unexecuted
                # phase is also the one to do next -- which addresses the other
                # orchestrated failure too (B1-ORCH: everything assigned up front,
                # mission executing before geometry and aero exist).
                pending, tools = self._next_required_phase(history)
                # Only worth saying while a turn remains to act on it. (This
                # guard was originally added for the enforcing version, where it
                # was wrong -- it disabled the check exactly when it mattered.
                # For advisory feedback it is right: informing an orchestrator
                # that cannot act only delays termination.)
                _turns_left = len(history) < self._max_turns
                if pending is not None and not self._phase_gap_reported and _turns_left:
                    # INFORM, then let the orchestrator decide. Working out the
                    # stage order is precisely what an orchestrated structure is
                    # being measured on, so enforcing it here would replace the
                    # variable under study with the framework's own sequencing --
                    # and orchestrated+graph_routed already couples 4/6 because
                    # its GRAPH supplies that structure legitimately.
                    #
                    # So this fires ONCE: the orchestrator is told what is
                    # missing and given the turn back. If it concludes anyway,
                    # the run ends and `phase_gap_ignored` records that it was
                    # told and chose to finish -- which is a coordination
                    # observation, not a defect to paper over.
                    self._phase_gap_reported = True
                    self._phase_gap_pending = pending
                    self._pending_phase_note = (
                        f"Before concluding: the '{pending}' phase has not run "
                        f"(it uses {', '.join(tools)}), so the result will be "
                        "missing its output. Phases are listed in dependency "
                        "order. Delegate it if the task needs it, or state why "
                        "you are finishing without it."
                    )
                    return False
                if pending is not None:
                    self._phase_gap_ignored = pending
                return True

        # Check max turns.
        if len(history) >= self._max_turns:
            return True

        return False

    # -- Tool gating -----------------------------------------------------------

    def _sync_orchestrator_tools(self) -> None:
        """Dynamically add/remove ``final_answer`` from the orchestrator's tool
        dict based on the current delegation state.

        This is a **model-level constraint**: when ``final_answer`` is absent
        from the tool dict the tokenizer chat-template will not include it in
        the tool schema, so the model physically cannot generate a call to it.
        Once the orchestrator has created at least one agent AND assigned at
        least one task, ``final_answer`` is injected so the model can signal
        DELEGATION_COMPLETE.
        """
        if not self._context:
            return
        orch = self._agents.get(self._orchestrator_name)
        if orch is None:
            return

        has_agents = bool(self._context.created_agents)
        has_assignments = bool(self._context.assignments)

        if has_agents and has_assignments:
            # Preconditions met — allow final_answer.
            if _FINAL_ANSWER_KEY not in orch.tools:
                orch.tools[_FINAL_ANSWER_KEY] = self._gated_final_answer
        else:
            # Preconditions NOT met — hide final_answer.
            orch.tools.pop(_FINAL_ANSWER_KEY, None)

    # -- Graph role aliasing ---------------------------------------------------

    def _register_graph_role_aliases(self) -> None:
        """Map orchestrator-created agents to graph role names.

        Called when transitioning from creation to execution phase and a
        graph-routed handler is in use.  For each graph role that isn't
        already present in ``self._agents``, tries:

        1. Fuzzy matching (substring/token overlap on name + description).
        2. Tool-based matching (role → agent that has the most relevant tool).
        3. Fallback to the first available worker.

        This lets the orchestrator create agents with arbitrary names
        (e.g. ``cad_code_executor``) while the graph handler resolves
        them by role (e.g. ``coder``, ``executor``).
        """
        from src.coordination.graph_definition import resolve_agent_for_role

        worker_names = self._context.created_agents if self._context else []
        workers = [self._agents[n] for n in worker_names if n in self._agents]
        if not workers:
            return

        # Build a tool→agent index for tool-based fallback.
        tool_index: dict[str, list] = {}
        for agent in workers:
            for tname in getattr(agent, "tools", {}):
                tool_index.setdefault(tname, []).append(agent)

        # Role → preferred tool names (heuristic, covers common graph roles).
        _ROLE_TOOL_HINTS: dict[str, list[str]] = {
            "executor": ["run_simulation"],
            "coder": ["run_simulation"],
            "output_reviewer": ["get_results", "check_output_files"],
        }

        for role in self._graph_roles:
            if role in self._agents:
                continue

            # 1. Fuzzy matching.
            try:
                agent = resolve_agent_for_role(role, self._agents, "_alias")
                self._agents[role] = agent
                continue
            except ValueError:
                pass

            # 2. Tool-based matching.
            hint_tools = _ROLE_TOOL_HINTS.get(role, [])
            for ht in hint_tools:
                candidates = tool_index.get(ht, [])
                if candidates:
                    self._agents[role] = candidates[0]
                    break
            if role in self._agents:
                continue

            # 3. Fallback to first worker (any LLM can do text tasks).
            self._agents[role] = workers[0]

    # -- Session ID extraction (framework-level pass-through) -----------------

    # UUID v4 pattern — matches session IDs from MCP tool observations.
    _UUID_RE = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        re.IGNORECASE,
    )

    def _extract_session_id_from_history(self, history: list) -> str | None:
        """Extract session_id from ``create_session`` tool observations.

        Scans ToolCallRecords on all AgentMessages for a
        ``create_session`` call whose observation contains a UUID.
        This comes directly from the MCP server response — it cannot
        be hallucinated by the LLM.

        Returns the first UUID found, or None.
        """
        for msg in history:
            if not isinstance(msg, AgentMessage):
                continue
            for tc in msg.tool_calls:
                if tc.tool_name == "create_session" and tc.output:
                    match = self._UUID_RE.search(tc.output)
                    if match:
                        return match.group(0)
        return None

    # -- Phase handlers --------------------------------------------------------

    def _creation_step(self, history: list, current_state: dict) -> CoordinationAction:
        """Phase 1: orchestrator creates team and assigns tasks."""
        if history:
            last = history[-1]
            content = last.content if isinstance(last, AgentMessage) else str(last)

            # 1. Explicit delegation-complete signal.
            if DELEGATION_COMPLETE in content:
                return self._transition_to_execution(history, current_state)

            # 2. Stall detection: if the orchestrator made zero delegation
            #    progress (no new create_agent / assign_task calls) for
            #    `_stall_threshold` consecutive turns, it likely completed
            #    the task directly or is stuck.  The threshold is
            #    configurable via orchestrated.stall_threshold in YAML.
            if self._orchestrator_turns_used > 0 and self._context:
                cur_created = len(self._context.created_agents)
                cur_assigned = len(self._context.assignments)
                if cur_created == self._prev_created_count and cur_assigned == self._prev_assignment_count:
                    self._stall_turns += 1
                else:
                    self._stall_turns = 0
                self._prev_created_count = cur_created
                self._prev_assignment_count = cur_assigned

                # Implicit delegation: orchestrator stalled but has
                # queued assignments — transition to execution so the
                # dynamically created workers actually run.
                if self._stall_turns >= 1 and self._context.assignments:
                    return self._transition_to_execution(history, current_state)

                if self._stall_turns >= self._stall_threshold:
                    # No assignments and no progress — orchestrator
                    # completed the task directly or is stuck; terminate.
                    self._phase = "done"
                    return CoordinationAction(
                        action_type="terminate",
                        agent_name=None,
                        input_context=content,
                        metadata={"reason": "orchestrator_no_delegation_progress"},
                    )

        # Pre-empt consecutive-error termination: if recent creation
        # turns all errored (e.g. model JSON formatting failures) but
        # the orchestrator DID create agents and assign tasks via tool
        # calls during those turns, transition to execution instead of
        # risking another error that would trigger the termination
        # checker's max_consecutive_errors threshold.
        if self._context and self._context.assignments and self._orchestrator_turns_used >= 2 and len(history) >= 2:
            recent = history[-2:]
            if all(isinstance(m, AgentMessage) and m.error for m in recent):
                return self._transition_to_execution(history, current_state)

        # Check orchestrator turn limit.
        if self._orchestrator_turns_used >= self._max_orchestrator_turns:
            return self._transition_to_execution(history, current_state)

        self._orchestrator_turns_used += 1
        if self._context:
            self._context.turn_counter = len(history) + 1

        # Sync tool visibility before invoking the orchestrator — this is the
        # model-level constraint that prevents premature final_answer calls.
        self._sync_orchestrator_tools()

        # Build input context for orchestrator.
        if self._lifecycle_mode == "per_stage" and self._pipeline_stage_names:
            input_context = self._build_per_stage_context(history, current_state)
        elif not history:
            input_context = current_state.get("task", "")
        else:
            input_context = self._format_context_for_orchestrator(history)

        return CoordinationAction(
            action_type="invoke_agent",
            agent_name=self._orchestrator_name,
            input_context=input_context,
            metadata={"phase": "creation", "orchestrator_turn": self._orchestrator_turns_used},
        )

    def _execution_step(self, history: list, current_state: dict) -> CoordinationAction:
        """Phase 2: execute assigned tasks."""
        if self._lifecycle_mode == "per_stage":
            return self._per_stage_execution(history, current_state)
        if self._lifecycle_mode == "setup_only":
            return self._setup_only_execution(history, current_state)
        else:
            return self._active_execution(history, current_state)

    def _transition_to_execution(self, history, current_state) -> CoordinationAction:
        """Switch from creation phase to execution phase."""
        self._phase = "execution"
        self._execution_index = 0

        # Register graph role aliases so the graph-routed handler can
        # resolve orchestrator-created agents by role name.
        if self._graph_roles and self._context:
            self._register_graph_role_aliases()

        # Reorder assignments to match pipeline stage order when a
        # staged_pipeline handler is in use. The orchestrator's
        # assign_task order is arbitrary; the handler pairs assignments
        # to stages by index, so without this reorder each stage_prompt
        # is delivered to the wrong worker and the discipline tools
        # never fire (the v9 cascade: open_cpacs / run_su2_solver /
        # estimate_mass / run_cycle all reported "Expected tool ... was
        # not called" while the pipeline advanced over empty work).
        if self._pipeline_stage_names and self._context and self._context.assignments:
            self._reorder_assignments_by_stage_name()

        if not self._context or not self._context.assignments:
            self._phase = "done"
            return CoordinationAction(
                action_type="terminate",
                agent_name=None,
                input_context="No assignments were created",
                metadata={"reason": "no_assignments"},
            )

        return self._execution_step(history, current_state)

    def _reorder_assignments_by_stage_name(self) -> None:
        """Reorder ctx.assignments so each pipeline stage's same-named
        worker runs first. Called once at execution-phase entry when
        _pipeline_stage_names is set (staged_pipeline handler in use).

        Mutates ctx.assignments in place. Assignments whose agent_name
        doesn't match any stage keep their relative order at the end
        of the list (preserves the existing fallback for surplus
        workers).
        """
        if not self._context or not self._pipeline_stage_names:
            return
        original = list(self._context.assignments)
        name_to_assignment: dict[str, dict] = {}
        for a in original:
            agent_name = a.get("agent_name") if isinstance(a, dict) else getattr(a, "agent_name", None)
            if agent_name and agent_name not in name_to_assignment:
                name_to_assignment[agent_name] = a

        ordered: list[dict] = []
        consumed_ids: set[int] = set()
        for stage_name in self._pipeline_stage_names:
            matched = name_to_assignment.get(stage_name)
            if matched is not None and id(matched) not in consumed_ids:
                ordered.append(matched)
                consumed_ids.add(id(matched))

        for a in original:
            if id(a) not in consumed_ids:
                ordered.append(a)

        self._context.assignments.clear()
        self._context.assignments.extend(ordered)

    def _retry_via_orchestrator(self, history: list) -> CoordinationAction:
        """Loop back to the orchestrator after workers failed a required signal.

        Resets the creation-phase state so the orchestrator can re-assign
        parameter agents.  The existing agents and their tool assignments
        remain intact — the orchestrator just needs to issue new
        ``assign_task`` calls with corrected instructions.
        """
        self._signal_retry_count += 1
        if self._signal_retry_count > self._max_signal_retries:
            self._phase = "done"
            return CoordinationAction(
                action_type="terminate",
                agent_name=None,
                input_context="",
                metadata={"reason": "max_signal_retries_exceeded"},
            )

        self._phase = "creation"
        self._execution_index = 0
        # Allow the orchestrator a fresh budget of turns.
        self._orchestrator_turns_used = 0
        self._stall_turns = 0
        # Clear previous assignments so GatedFinalAnswer requires new ones.
        if self._context:
            self._context.assignments.clear()
        # Remove final_answer so the orchestrator must delegate again.
        self._sync_orchestrator_tools()

        context = (
            "SIMULATION FAILED — all run_simulation calls returned errors. "
            "The parameter combination set by your workers was invalid. "
            "Re-assign parameter agents (aerodynamics_analyst, "
            "propulsion_analyst) with corrected instructions. They should "
            "read the 'valid' boolean returned by set_aircraft_parameters "
            "(validation runs inline) and adjust parameters until valid:true "
            "before handing off to simulation_executor again.\n\n"
            + self._format_context_for_orchestrator(history)
        )
        self._orchestrator_turns_used += 1

        return CoordinationAction(
            action_type="invoke_agent",
            agent_name=self._orchestrator_name,
            input_context=context,
            metadata={
                "phase": "creation",
                "orchestrator_turn": self._orchestrator_turns_used,
                "retry_reason": "simulation_failed",
            },
        )

    def _build_per_stage_context(self, history: list, current_state: dict) -> str:
        """Build the orchestrator's input context for per-stage mode.

        Two cases:
          (a) FIRST turn (no worker history yet): tell the orchestrator
              about Stage 1 and ask it to delegate.
          (b) FOLLOWING a worker run: include the previous stage's
              output AND error context. Ask the orchestrator to decide
              whether to RETRY the just-failed stage or ADVANCE to the
              next one. The orchestrator's choice is implicit in
              which agent_name it assign_tasks for next."""
        parts: list[str] = []
        # Phase status must appear in BOTH context builders. It was added only
        # to _format_context_for_orchestrator, and staged_pipeline runs in
        # per_stage lifecycle mode and never calls that one -- so the feedback
        # never arrived (B42). This is information for the orchestrator to act
        # on, not a constraint: deciding the stage order is what the structure
        # is being measured on.
        if self._pending_phase_note:
            parts.append(self._pending_phase_note)
            self._pending_phase_note = None
        _phases = (self._context.required_tool_phases if self._context else None) or {}
        if _phases:
            _done = self._phases_done(history)
            _pending, _ = self._next_required_phase(history)
            parts.append(
                "PHASES run so far: "
                + (", ".join(p for p in _phases if p in _done) or "none")
                + ("; not yet run: " + _pending if _pending else "; all phases have run")
            )
        stages = self._pipeline_stage_names or []
        total = len(stages)

        # Find the most recent worker message and its outcome.
        last_worker_msg = None
        for m in reversed(history):
            if isinstance(m, AgentMessage) and m.agent_name != self._orchestrator_name:
                last_worker_msg = m
                break

        if last_worker_msg is None:
            # First turn — original task + Stage 1 delegation.
            parts.append(current_state.get("task", ""))
            parts.append("")
            stage_name = stages[0] if stages else "stage_1"
            parts.append(
                f"PER-STAGE DELEGATION — Stage 1 of {total}: `{stage_name}`."
            )
            hint = self._stage_tool_hint(stage_name)
            if hint:
                parts.append(f"This stage needs one of these tools: {hint}.")
            parts.append(
                "Create a single worker for THIS stage with the right "
                "tools, assign_task to it, then call final_answer with "
                "DELEGATION_COMPLETE. Do NOT create workers for later "
                "stages — you'll be called again for each."
            )
            return "\n".join(parts).strip()

        # A worker just ran. Report what happened and present BOTH
        # retry and advance options every time — the orchestrator
        # reads the output and decides. Don't try to guess success
        # ourselves with brittle keyword detection; we already had
        # one false-negative (the worker paraphrased "file not found"
        # as natural language and our heuristic missed it).
        prev_stage_name = last_worker_msg.agent_name
        try:
            prev_idx = stages.index(prev_stage_name)
        except ValueError:
            prev_idx = -1
        prev_content = last_worker_msg.content or ""
        prev_error = last_worker_msg.error or ""

        parts.append(
            f"PREVIOUS STAGE: `{prev_stage_name}` (Stage {prev_idx + 1} of "
            f"{total}) just ran."
        )
        if prev_error:
            parts.append(f"ERROR FIELD: {prev_error}")
        if prev_content:
            parts.append(f"OUTPUT:\n{prev_content[:1500]}")
        parts.append("")
        parts.append(
            "READ THE OUTPUT ABOVE CAREFULLY. Did the stage's signature "
            "tool actually succeed? Examples of failure: "
            "\"File not found\", \"does not exist\", \"Error calling tool\", "
            "\"success: false\", \"INVALID_SESSION\", parameter rejected, "
            "or any natural-language paraphrase of those. If you're "
            "unsure, treat it as failed."
        )
        parts.append("")
        parts.append("YOUR DECISION:")

        if prev_idx >= 0:
            retry_hint = self._stage_tool_hint(prev_stage_name)
            retry_line = (
                f"  - If the stage FAILED: create a NEW worker named "
                f"`{prev_stage_name}` (or modify) with the right tools "
            )
            if retry_hint:
                retry_line += f"({retry_hint}) "
            retry_line += (
                "and assign_task with a CORRECTED approach (e.g. exact "
                "file paths verbatim, discover UIDs via list_geometric_"
                "components before set_high_level_parameters, etc.). "
                "Call DELEGATION_COMPLETE. The pipeline will retry this stage."
            )
            parts.append(retry_line)

        if prev_idx + 1 < total:
            next_stage = stages[prev_idx + 1]
            next_hint = self._stage_tool_hint(next_stage)
            advance_line = (
                f"  - If the stage SUCCEEDED: create a worker named "
                f"`{next_stage}` "
            )
            if next_hint:
                advance_line += f"with tools ({next_hint}) "
            advance_line += (
                "and assign_task. Call DELEGATION_COMPLETE. The pipeline "
                "will advance to Stage " + str(prev_idx + 2) + "."
            )
            parts.append(advance_line)
        else:
            parts.append(
                "  - If the stage SUCCEEDED: this is the LAST stage. "
                "Call final_answer('TASK_COMPLETE') to terminate."
            )

        return "\n".join(parts).strip()

    def _stage_tool_hint(self, stage_name: str) -> str:
        """Best-effort: map a pipeline stage name to a tool-list hint
        based on the required_tool_phases dict (defined in the
        orchestrator agents YAML). Falls back to the empty string if
        no obvious match."""
        if not self._context or not self._context.required_tool_phases:
            return ""
        # Simple substring matching: phase "geometry_setup" matches
        # stage "geometry_engineer"; "aerodynamic_analysis" matches
        # "aerodynamics_analyst"; etc.
        stage_stem = stage_name.split("_")[0].lower()  # "geometry", "aerodynamics", ...
        for phase, tools in self._context.required_tool_phases.items():
            if stage_stem in phase.lower() or phase.lower().startswith(stage_stem[:4]):
                return ", ".join(tools)
        return ""

    def _per_stage_execution(self, history, current_state) -> CoordinationAction:
        """Per-stage mode: the ORCHESTRATOR drives stage progression
        via its assignments. After each worker run, control routes
        back to the orchestrator with the worker's output/error so
        the orchestrator can decide:
          - retry the same stage (create a new worker for the same
            stage name with corrected approach)
          - advance to the next stage (create a worker for the next
            stage's name)
          - skip a stage (delegate for a later stage)

        The strategy's job: pick the assignment whose name matches
        a pipeline stage, run it, capture the result. The cursor
        (_current_stage_idx) follows the orchestrator's choice — it
        is set from the agent_name of the assignment we run, not
        auto-incremented.

        Termination is via the existing stall detection in
        _creation_step: when the orchestrator emits DELEGATION_COMPLETE
        without adding new assignments for stall_threshold turns, the
        loop exits.
        """
        if not self._pipeline_stage_names:
            # Defensive: per_stage mode without stage names is a
            # mis-configuration; fall back to setup_only behavior.
            return self._setup_only_execution(history, current_state)

        # If the previous turn was a worker (not the orchestrator),
        # the stage just RAN. Route back to the orchestrator so it
        # can react to the output / error and decide next steps.
        # Critically: do NOT advance the cursor here. The cursor
        # follows the orchestrator's assignment, not a counter.
        last_was_worker = (
            history
            and isinstance(history[-1], AgentMessage)
            and history[-1].agent_name != self._orchestrator_name
        )
        if last_was_worker:
            self._phase = "creation"
            self._orchestrator_turns_used = 0
            self._stall_turns = 0
            return self._creation_step(history, current_state)

        # Otherwise, find an unrun assignment for some pipeline
        # stage. Prefer the MOST RECENT assignment whose agent_name
        # matches any pipeline stage. The orchestrator's most recent
        # choice tells us which stage to run.
        assignments = (self._context.assignments if self._context else []) or []
        stage_names_set = set(self._pipeline_stage_names)
        match = None
        for a in reversed(assignments):
            if a.get("agent_name") in stage_names_set:
                match = a
                break
        if match is None:
            # Orchestrator hasn't (yet) assigned for any pipeline
            # stage. Route back to it.
            self._phase = "creation"
            self._orchestrator_turns_used = 0
            return self._creation_step(history, current_state)

        # Refuse to move BACKWARDS through the pipeline (B57).
        #
        # The cursor used to be set to whatever stage the orchestrator named,
        # with no comparison to where the pipeline already was, so an assignment
        # to an earlier stage silently rewound it. Retrying the CURRENT stage is
        # legitimate (a stage that failed should be re-run) and skipping FORWARD
        # is a decision the orchestrator is entitled to make; going back to a
        # completed stage is neither, and it is what separates
        # orchestrated_staged_pipeline from sequential_staged_pipeline running
        # the same experiment.
        try:
            _idx = self._pipeline_stage_names.index(match["agent_name"])
        except ValueError:
            _idx = None
        if _idx is not None and _idx < self._max_stage_reached:
            _cur = self._pipeline_stage_names[self._max_stage_reached]
            _next = (self._pipeline_stage_names[self._max_stage_reached + 1]
                     if self._max_stage_reached + 1 < len(self._pipeline_stage_names)
                     else None)
            self._pending_phase_note = (
                f"REJECTED: {match['agent_name']} is stage {_idx + 1} and the pipeline has "
                f"already completed stage {self._max_stage_reached + 1} ({_cur}). A staged "
                f"pipeline runs forwards only -- you may re-run {_cur} or move on to "
                + (f"{_next}." if _next else "the final stage.")
                + " Assign the next stage, not an earlier one."
            )
            # Drop it, or the same assignment is re-selected forever.
            if self._context and match in self._context.assignments:
                self._context.assignments.remove(match)
            self._phase = "creation"
            self._orchestrator_turns_used = 0
            return self._creation_step(history, current_state)

        # Sync cursor to whatever stage we're about to run, so other
        # code paths (logs, metadata) see the correct stage.
        if _idx is not None:
            self._current_stage_idx = _idx
            self._max_stage_reached = max(self._max_stage_reached, _idx)

        # Build worker input. Just task + previous-stage output. NO
        # SESSION_ID injection (workers confuse it with file paths;
        # see test_worker_input_does_not_leak_session_uuid).
        prev = ""
        if history:
            last = history[-1]
            prev = last.content if isinstance(last, AgentMessage) else str(last)
        input_context = (
            f"{match['task']}\n\nContext from previous agent:\n{prev}"
            if prev
            else match['task']
        )

        return CoordinationAction(
            action_type="invoke_agent",
            agent_name=match["agent_name"],
            input_context=input_context,
            metadata={
                "phase": "execution",
                "lifecycle_mode": "per_stage",
                "current_stage_idx": self._current_stage_idx,
                "stage_name": match["agent_name"],
            },
        )

    def _setup_only_execution(self, history, current_state) -> CoordinationAction:
        """Run workers sequentially in creation order (setup_only mode)."""
        assignments = self._context.assignments

        # Graph-routed: full workflow runs in one handler call.
        if self._graph_roles and self._execution_index > 0:
            self._execution_index = len(assignments)

        # Early signal check: if simulation was attempted but failed,
        # don't waste time on remaining assignments (e.g. mdo_integrator).
        # Route back to the orchestrator immediately for parameter retry.
        if (
            self._execution_index > 0
            and self._execution_index < len(assignments)
            and self._context
            and "simulation_attempted" in self._context.result_signals
        ):
            missing = _check_result_signals(self._context)
            if missing:
                return self._retry_via_orchestrator(history)

        if self._execution_index >= len(assignments):
            # Check result signals before terminating — if a required
            # signal was attempted but not achieved (e.g. simulation ran
            # but all calls failed), loop back to the orchestrator so it
            # can re-assign parameter agents for a retry.
            if self._context and _check_result_signals(self._context):
                return self._retry_via_orchestrator(history)

            self._phase = "done"
            return CoordinationAction(
                action_type="terminate",
                agent_name=None,
                input_context="",
                metadata={"reason": "all_assignments_executed"},
            )

        assignment = assignments[self._execution_index]
        self._execution_index += 1

        # Graph-routed handoff (mirrors the sequential strategy, which
        # passes the RAW user task to the handler). The graph_routed
        # handler runs the ENTIRE state machine from one overall task
        # and drives each state with its OWN per-state prompt. Feeding
        # it a per-agent assignment (e.g. mission_architect's "optimize
        # parameters" task) pollutes every state's context: TASK_CLASSIFIED
        # then sees an optimization task instead of a classify prompt,
        # mission_architect optimizes instead of emitting a complexity
        # word, no transition fires, and the graph stalls. Hand the
        # handler the raw user task so its per-state prompts drive flow.
        if self._graph_roles:
            raw_task = current_state.get("task", "") or assignment["task"]
            return CoordinationAction(
                action_type="invoke_agent",
                agent_name=assignment["agent_name"],
                input_context=raw_task,
                metadata={
                    "phase": "execution",
                    "assignment_index": self._execution_index,
                    "graph_routed_raw_task": True,
                },
            )

        # Build input: task + previous agent output.
        if history:
            last = history[-1]
            prev = last.content if isinstance(last, AgentMessage) else str(last)

            # Inject session_id: prefer direct injection, fall back to history.
            session_id = self._session_id or self._extract_session_id_from_history(history)
            session_line = f"SESSION_ID: {session_id}\n\n" if session_id else ""

            input_context = f"{session_line}{assignment['task']}\n\nContext from previous agent:\n{prev}"
        else:
            input_context = assignment["task"]

        return CoordinationAction(
            action_type="invoke_agent",
            agent_name=assignment["agent_name"],
            input_context=input_context,
            metadata={"phase": "execution", "assignment_index": self._execution_index},
        )

    def _active_execution(self, history, current_state) -> CoordinationAction:
        """Active mode: alternate between workers and orchestrator review."""
        assignments = self._context.assignments

        # When a graph-routed handler is active, the graph's state
        # machine covers the ENTIRE workflow end-to-end.  The first
        # handler call runs the full graph; subsequent assignments
        # would just re-run the same graph from scratch.  After the
        # first assignment executes, skip the rest.
        if self._graph_roles and self._execution_index > 0:
            self._execution_index = len(assignments)

        # Early signal check: if simulation was attempted but failed,
        # don't waste time on remaining assignments.  Route back to
        # the orchestrator immediately for parameter retry.
        if (
            self._execution_index > 0
            and self._execution_index < len(assignments)
            and self._context
            and "simulation_attempted" in self._context.result_signals
        ):
            missing = _check_result_signals(self._context)
            if missing:
                return self._retry_via_orchestrator(history)

        # If all workers have executed, let orchestrator review.
        if self._execution_index >= len(assignments):
            # After orchestrator reviews, we're done — unless a required
            # result signal is missing (e.g. simulation failed).
            if history:
                last = history[-1]
                last.content if isinstance(last, AgentMessage) else str(last)
                last_agent = last.agent_name if isinstance(last, AgentMessage) else ""
                if last_agent == self._orchestrator_name:
                    if self._context and _check_result_signals(self._context):
                        return self._retry_via_orchestrator(history)
                    self._phase = "done"
                    return CoordinationAction(
                        action_type="terminate",
                        agent_name=None,
                        input_context="",
                        metadata={"reason": "active_execution_complete"},
                    )

            # Orchestrator review turn.  Bypass the execution handler so the
            # orchestrator runs directly — otherwise a graph_routed handler
            # would intercept this action and run a full graph traversal
            # instead of letting the orchestrator review.
            context = self._format_context_for_orchestrator(history)
            return CoordinationAction(
                action_type="invoke_agent",
                agent_name=self._orchestrator_name,
                input_context=context,
                metadata={
                    "phase": "execution",
                    "role": "orchestrator_review",
                    "bypass_handler": True,
                },
            )

        # Run next worker.
        assignment = assignments[self._execution_index]
        self._execution_index += 1

        # Graph-routed handoff — see the matching block in
        # _setup_only_execution. Hand the handler the RAW user task so
        # its per-state prompts (not the orchestrator's per-agent task)
        # drive the state machine.
        if self._graph_roles:
            raw_task = current_state.get("task", "") or assignment["task"]
            return CoordinationAction(
                action_type="invoke_agent",
                agent_name=assignment["agent_name"],
                input_context=raw_task,
                metadata={
                    "phase": "execution",
                    "assignment_index": self._execution_index,
                    "graph_routed_raw_task": True,
                },
            )

        if history:
            last = history[-1]
            prev = last.content if isinstance(last, AgentMessage) else str(last)

            # Inject session_id: prefer direct injection, fall back to history.
            session_id = self._session_id or self._extract_session_id_from_history(history)
            session_line = f"SESSION_ID: {session_id}\n\n" if session_id else ""

            input_context = f"{session_line}{assignment['task']}\n\nContext from previous agent:\n{prev}"
        else:
            input_context = assignment["task"]

        return CoordinationAction(
            action_type="invoke_agent",
            agent_name=assignment["agent_name"],
            input_context=input_context,
            metadata={"phase": "execution", "assignment_index": self._execution_index},
        )

    # -- Information mode formatting -------------------------------------------

    def _format_context_for_orchestrator(self, history: list) -> str:
        """Format history for the orchestrator based on information mode."""
        if not history:
            return ""

        lines = []
        # A refused completion must say WHY and what remains, or the orchestrator
        # re-issues the same finish and burns turns. Placed first so it is not
        # buried under the worker transcript.
        if self._pending_phase_note:
            lines.append(self._pending_phase_note)
            self._pending_phase_note = None
        # Standing progress line: which declared phases are done, and which is
        # next in dependency order. Both orchestrated failures were bookkeeping
        # -- re-running finished phases, and assigning the mission before its
        # inputs existed -- so the orchestrator is told the state it kept losing.
        phases = (self._context.required_tool_phases if self._context else None) or {}
        if phases:
            done = self._phases_done(history)
            pending, _ = self._next_required_phase(history)
            lines.append(
                "PHASES run so far: " + (", ".join(p for p in phases if p in done) or "none")
                + ("; not yet run: " + pending if pending else "; all phases have run")
            )
        for msg in history:
            if not isinstance(msg, AgentMessage):
                lines.append(str(msg))
                continue

            if self._information_mode == "opaque":
                status = "FAILED" if msg.error else "SUCCESS"
                lines.append(
                    f"[Worker: {msg.agent_name}, Turn {msg.turn_number}]\n"
                    f"Status: {status}\n"
                    f"Duration: {msg.duration_seconds:.1f}s"
                )
            else:
                # Transparent mode: full output.
                tool_lines = ""
                if msg.tool_calls:
                    calls = []
                    for tc in msg.tool_calls:
                        status = f"error: {tc.error}" if tc.error else f"success, {tc.duration_seconds:.1f}s"
                        calls.append(f"{tc.tool_name}({tc.inputs}) → {status}")
                    tool_lines = f"\nTool calls: [{'; '.join(calls)}]"

                lines.append(
                    f"[Worker: {msg.agent_name}, Turn {msg.turn_number}]\n"
                    f"Task output: {msg.content}"
                    f"{tool_lines}\n"
                    f"Duration: {msg.duration_seconds:.1f}s"
                )

        return "\n\n".join(lines)

    # -- Authority transfer ----------------------------------------------------

    def compute_authority_scores(self, history: list) -> dict[str, float]:
        """Compute per-agent authority scores from history.

        Score = (1 - tool_error_rate) * 0.5
              + (1 - retry_rate) * 0.3
              + task_completion_rate * 0.2
        """
        agent_stats: dict[str, dict] = {}

        for msg in history:
            if not isinstance(msg, AgentMessage):
                continue
            name = msg.agent_name
            if name not in agent_stats:
                agent_stats[name] = {
                    "total_turns": 0,
                    "retries": 0,
                    "errors": 0,
                    "total_tool_calls": 0,
                    "failed_tool_calls": 0,
                }
            stats = agent_stats[name]
            stats["total_turns"] += 1
            if msg.is_retry:
                stats["retries"] += 1
            if msg.error:
                stats["errors"] += 1
            for tc in msg.tool_calls:
                stats["total_tool_calls"] += 1
                if tc.error:
                    stats["failed_tool_calls"] += 1

        scores = {}
        for name, stats in agent_stats.items():
            total = stats["total_turns"]
            if total == 0:
                continue
            tool_err = stats["failed_tool_calls"] / stats["total_tool_calls"] if stats["total_tool_calls"] > 0 else 0.0
            retry_rate = stats["retries"] / total
            completion_rate = 1.0 - (stats["errors"] / total)
            score = (1 - tool_err) * 0.5 + (1 - retry_rate) * 0.3 + completion_rate * 0.2
            scores[name] = round(score, 4)

        return scores

    def check_authority_transfer(self, history: list) -> dict | None:
        """Check if authority should transfer. Returns transfer event or None."""
        if self._authority_mode != "delegated":
            return None

        self._prompts_completed += 1
        if self._prompts_completed < self._authority_transfer_after:
            return None

        scores = self.compute_authority_scores(history)
        if not scores:
            return None

        current_auth = self._authority_agent
        current_score = scores.get(current_auth, 0.0)

        # Find best worker (exclude current authority).
        worker_scores = {
            name: score
            for name, score in scores.items()
            if name != current_auth and name in (self._context.created_agents if self._context else [])
        }
        if not worker_scores:
            return None

        best_worker = max(worker_scores, key=worker_scores.get)
        best_score = worker_scores[best_worker]

        if best_score > current_score:
            self._authority_agent = best_worker
            self._orchestrator_name = best_worker
            self._prompts_completed = 0  # reset counter
            return {
                "event": "authority_transfer",
                "from": current_auth,
                "to": best_worker,
                "scores": scores,
                "prompt_number": self._prompts_completed,
            }

        return None

    # -- Properties ------------------------------------------------------------

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def context(self) -> OrchestratorContext | None:
        return self._context

    @property
    def orchestrator_name(self) -> str:
        return self._orchestrator_name
