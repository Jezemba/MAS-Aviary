"""Networked coordination strategy — peer-based, blackboard-driven.

All agents are equals. No hierarchy, no orchestrator. Agents self-
organize by reading shared blackboard state to decide what work to do.
Communication happens through a shared Blackboard (mutable key-value
store) alongside the immutable SharedHistory.

Execution flow:
  1. initialize() creates N initial peer agents with full tool set +
     peer tools, initializes the blackboard with the task description.
  2. next_step() selects an agent via placeholder rotation, builds
     context from blackboard + history, returns a CoordinationAction.
  3. is_complete() checks for TASK_COMPLETE, max turns, or all agents
     reporting DONE on blackboard.

Toggles (all independent, any combination valid):
  - claiming_mode: "none" / "soft" / "hard"
  - peer_monitoring_visible: bool
  - trans_specialist_knowledge: bool
  - predictive_knowledge: bool
"""

from src.coordination.blackboard import Blackboard
from src.coordination.history import AgentMessage
from src.coordination.strategy import CoordinationAction, CoordinationStrategy
from src.tools.networked_tools import (
    PEER_TOOL_NAMES,
    MarkTaskDone,
    NetworkedContext,
    ReadBlackboard,
    SpawnPeer,
    WriteBlackboard,
)


class NetworkedStrategy(CoordinationStrategy):
    """Peer-based strategy where agents coordinate via shared blackboard."""

    def __init__(self):
        # Config (set during initialize).
        self._initial_agents: int = 5
        self._max_agents: int = 10
        self._agent_max_steps: int = 8
        self._claiming_mode: str = "soft"
        self._peer_monitoring_visible: bool = True
        self._trans_specialist_knowledge: bool = True
        self._predictive_knowledge: bool = False
        self._termination_keyword: str = "TASK_COMPLETE"
        self._max_turns: int = 40
        self._max_consecutive_errors: int = 3
        self._max_context_tokens: int = 4000
        self._max_recent_messages: int = 15

        # Runtime state.
        self._agents: dict = {}
        self._context: NetworkedContext | None = None
        self._blackboard: Blackboard | None = None
        self._agent_order: list[str] = []  # rotation order
        self._rotation_index: int = 0
        self._total_turns: int = 0
        self._consecutive_errors: int = 0
        self._task: str = ""

        # Peer prompt parts (loaded from config).
        self._base_prompt: str = ""
        self._soft_claiming_addition: str = ""
        self._hard_claiming_addition: str = ""
        self._prediction_addition: str = ""

        # Phase gating (optional, loaded from config).
        self._workflow_phases: list[dict] = []
        self._full_toolsets: dict[str, dict] = {}  # original tools per agent
        self._handler_is_staged_pipeline: bool = False

        # Graph-driven mode (optional, set when _graph_def is in config).
        self._graph = None  # GraphDefinition or None
        self._graph_current_state: str | None = None
        self._graph_state_dict: dict = {}
        self._graph_complete: bool = False

        # Selection mode. Three values:
        #   "round_robin" — default. Strategy picks one peer per turn in
        #     fixed rotation. Existing aviary-only combos use this.
        #   "volunteer" — strategy broadcasts a "want to act next?" vote
        #     prompt to every idle peer in parallel each turn, picks the
        #     strongest YES. Closer to AutoGen GroupChatManager and
        #     arxiv 2510.01285 / 2507.01701 patterns.
        #   "concurrent_blackboard" — CodeCRDT pattern (arxiv 2510.18893):
        #     all peers run concurrently in threads, race to claim TODOs
        #     from the shared blackboard. The strategy returns a single
        #     parallel_run action per turn; the Coordinator launches all
        #     peers in a ThreadPoolExecutor. Each peer's ReAct loop
        #     reads -> claims -> executes -> marks done in a tight cycle
        #     until all TODOs are done or its step budget runs out.
        self._selection_mode: str = "round_robin"
        self._volunteer_model = None  # Shared LLM for vote prompts.
        # concurrent_blackboard state.
        self._concurrent_runs_dispatched: int = 0
        self._max_concurrent_runs: int = 2  # configurable via coord YAML

    def initialize(self, agents: dict, config: dict) -> None:
        """Set up strategy from agents dict and coordination config.

        The agents dict is used as the shared reference (same as
        orchestrated strategy) so dynamically spawned agents are
        visible to the Coordinator's run loop.
        """
        net_config = config.get("networked", {})

        # Read config.
        self._initial_agents = net_config.get("initial_agents", 5)
        self._max_agents = net_config.get("max_agents", 10)
        self._agent_max_steps = net_config.get("agent_max_steps", 8)
        self._claiming_mode = net_config.get("claiming_mode", "soft")
        self._peer_monitoring_visible = net_config.get("peer_monitoring_visible", True)
        self._trans_specialist_knowledge = net_config.get("trans_specialist_knowledge", True)
        self._predictive_knowledge = net_config.get("predictive_knowledge", False)
        self._selection_mode = net_config.get("selection_mode", "round_robin")
        self._max_concurrent_runs = net_config.get("max_concurrent_runs", 2)

        term_config = config.get("termination", {})
        self._termination_keyword = term_config.get("keyword", "TASK_COMPLETE")
        self._max_turns = term_config.get("max_turns", 40)
        self._max_consecutive_errors = term_config.get("max_consecutive_errors", 3)

        ctx_config = config.get("context", {})
        self._max_context_tokens = ctx_config.get("max_context_tokens", 4000)
        self._max_recent_messages = ctx_config.get("max_recent_messages", 15)

        # Workflow phase gating (optional).
        # Skip phase gating when staged_pipeline handler is active — the
        # handler's stage definitions already enforce ordering through
        # completion criteria.  The two mechanisms are redundant and conflict.
        self._handler_is_staged_pipeline = config.get("execution_handler") == "staged_pipeline"
        self._workflow_phases = net_config.get("workflow_phases", [])

        # Load peer prompt templates from agent config.
        peer_template = config.get("peer_template", {})
        self._base_prompt = peer_template.get("base_system_prompt", _DEFAULT_BASE_PROMPT)
        self._soft_claiming_addition = peer_template.get("soft_claiming_addition", "")
        self._hard_claiming_addition = peer_template.get("hard_claiming_addition", "")
        self._prediction_addition = peer_template.get("prediction_prompt_addition", "")

        # Assemble the final peer prompt.
        peer_prompt = self._assemble_peer_prompt()

        # Collect worker tools from config (injected by Coordinator).
        worker_tools_dict = config.get("_worker_tools", {})
        worker_tools = list(worker_tools_dict.values()) if worker_tools_dict else []

        # Extract model from an existing agent, or from config.
        model = config.get("_model")
        if model is None:
            # Try to get model from first agent in dict.
            for agent in agents.values():
                if hasattr(agent, "model"):
                    model = agent.model
                    break

        # Initialize blackboard.
        self._blackboard = Blackboard(claiming_mode=self._claiming_mode)

        # Toggle config dict for context filtering.
        toggle_config = {
            "peer_monitoring_visible": self._peer_monitoring_visible,
            "trans_specialist_knowledge": self._trans_specialist_knowledge,
            "predictive_knowledge": self._predictive_knowledge,
        }

        # Use same dict reference so spawned agents are visible to Coordinator.
        self._agents = agents

        # Build shared context.
        self._context = NetworkedContext(
            blackboard=self._blackboard,
            agents=self._agents,
            model=model,
            all_tools=[],  # will be set after peer tools are created
            peer_prompt=peer_prompt,
            agent_max_steps=self._agent_max_steps,
            max_agents=self._max_agents,
            agent_counter=0,
            config=toggle_config,
        )

        # Create peer tools (need context reference).
        # Note: each agent gets its own WriteBlackboard and SpawnPeer
        # instances with agent_name set. ReadBlackboard is shared.
        # For tool creation, we'll create template instances;
        # per-agent instances are created in _create_peer_agent.

        # Build the full tool list for new agents (domain + peer tools).
        # Peer tools are created per-agent in _create_peer_agent.
        self._domain_tools = worker_tools

        # Create initial peer agents.
        self._agent_order = []
        for i in range(1, self._initial_agents + 1):
            name = f"agent_{i}"
            self._context.agent_counter = i
            self._create_peer_agent(name)

        # Volunteer-broadcast needs a shared model handle for the
        # parallel vote calls. All peers were constructed with the same
        # self._context.model so any one works as the vote model.
        if self._selection_mode == "volunteer":
            self._volunteer_model = self._context.model

        # Concurrent-blackboard mode: seed the TODO list at start so
        # peer threads have something to race for. The seed comes from
        # the coord YAML — different design tasks ship different seeds.
        # Format: list of {name, description} dicts.
        if self._selection_mode == "concurrent_blackboard":
            todo_seed = net_config.get("todo_seed", []) or []
            seed_pairs: list[tuple[str, str]] = []
            for entry in todo_seed:
                if isinstance(entry, dict) and "name" in entry:
                    seed_pairs.append((entry["name"], entry.get("description", "")))
                elif isinstance(entry, (list, tuple)) and len(entry) >= 1:
                    name = entry[0]
                    desc = entry[1] if len(entry) >= 2 else ""
                    seed_pairs.append((name, desc))
            if seed_pairs and self._blackboard is not None:
                self._blackboard.seed_todos(seed_pairs)
            self._concurrent_runs_dispatched = 0

        # Set all_tools in context for future spawns (includes peer tools).
        # The peer tool instances in all_tools are templates; SpawnPeer
        # creates proper instances at spawn time. For now, use the
        # domain tools + template peer tools.
        self._context.all_tools = self._build_tool_list("template_agent")

        # Snapshot each agent's full toolset for phase-gating restore.
        if self._workflow_phases:
            for name, agent in self._agents.items():
                self._full_toolsets[name] = dict(agent.tools)

        # Graph-driven mode: load graph definition if provided.
        graph_def = config.get("_graph_def")
        if graph_def is not None:
            self._graph = graph_def
            self._graph_current_state = graph_def.initial_state
            self._graph_state_dict = {
                "execution_success": False,
                "review_verdict": None,
                "review_passed": None,
                "states_visited": [],
            }
            self._graph_complete = False
            # Snapshot toolsets for graph-driven tool filtering.
            for name, agent in self._agents.items():
                self._full_toolsets[name] = dict(agent.tools)

        # Reset state.
        self._rotation_index = 0
        self._total_turns = 0
        self._consecutive_errors = 0

    def set_session_id(self, session_id: str) -> None:
        """Inject a pre-created session_id into the blackboard.

        Called by the coordinator when a pre-hook has already created the
        MCP session.  Writes the session_id as a result entry and pre-
        completes Phase 1 (session_setup) so phase gating advances agents
        directly to Phase 2 (parameter_setting).  Without this, agents
        get stuck in Phase 1 because the task text tells them NOT to call
        create_session, but phase gating only offers Phase 1 tools.
        """
        if self._blackboard is None:
            return

        # Write session_id to blackboard so all agents can read it.
        self._blackboard.write(
            key="session_id",
            value=session_id,
            author="system",
            entry_type="result",
        )

        # Pre-complete Phase 1 (session_setup) — the pre-hook already
        # called create_session + configure_mission.
        self._blackboard.write(
            key="phase_session_setup",
            value=f"completed by pre-hook (session_id={session_id})",
            author="system",
            entry_type="status",
        )

    def next_step(self, history: list, current_state: dict) -> CoordinationAction:
        """Select next agent via rotation and build context."""
        self._total_turns = len(history)

        # Store task from state on first call.
        if "task" in current_state and not self._task:
            self._task = current_state["task"]
            # Write task to blackboard.
            if self._blackboard is not None:
                self._blackboard.write("task", self._task, "system", "status")

        # Auto-complete workflow phases based on tool calls from last turn.
        self._auto_complete_phases(history)

        # Structural post-turn write: auto-post the previous agent's result to
        # the blackboard so subsequent agents see it in their context without
        # needing to call write_blackboard themselves.  Skips error turns and
        # entries already written (same agent, same key, same content).
        if history and self._blackboard:
            last = history[-1]
            if isinstance(last, AgentMessage) and last.content and not last.error:
                content = last.content
                if len(content) > 800:
                    content = content[:800] + "..."
                self._blackboard.write(
                    key=f"{last.agent_name}_result",
                    value=content,
                    author=last.agent_name,
                    entry_type="result",
                )

        # Track consecutive errors.
        if history:
            last = history[-1]
            if isinstance(last, AgentMessage) and last.error:
                self._consecutive_errors += 1
            else:
                self._consecutive_errors = 0

        if not self._agent_order:
            return CoordinationAction(
                action_type="error",
                agent_name=None,
                input_context="No agents available",
            )

        # ----- Graph-driven mode: one graph state per turn -----
        if self._graph is not None:
            return self._graph_driven_next_step(history, current_state)

        # Update agent order to include any spawned agents.
        current_names = [n for n in self._agent_order if n in self._agents]
        new_names = [n for n in self._agents if n not in current_names and n != "system"]
        self._agent_order = current_names + new_names

        # Selection: concurrent_blackboard fires ALL peers in parallel each
        # turn (the CodeCRDT-style pattern). The Coordinator interprets the
        # parallel_run action by launching peers in a ThreadPoolExecutor.
        # We dispatch up to _max_concurrent_runs cycles, after which we
        # terminate even if not every TODO has been marked done (peers
        # ran out of step budget).
        if self._selection_mode == "concurrent_blackboard":
            if self._concurrent_runs_dispatched >= self._max_concurrent_runs:
                return CoordinationAction(
                    action_type="terminate",
                    agent_name=None,
                    input_context="",
                    metadata={
                        "reason": "concurrent_blackboard max cycles reached",
                        "cycles_dispatched": self._concurrent_runs_dispatched,
                    },
                )
            self._concurrent_runs_dispatched += 1
            return CoordinationAction(
                action_type="parallel_run",
                agent_name=None,
                input_context=self._task or current_state.get("task", ""),
                metadata={
                    "peers": list(self._agent_order),
                    "turn": self._total_turns + 1,
                    "cycle": self._concurrent_runs_dispatched,
                    "selection_mode": "concurrent_blackboard",
                },
            )

        # Selection: volunteer broadcast OR round-robin.
        if self._selection_mode == "volunteer" and self._volunteer_model is not None:
            picked = self._select_via_volunteer(history, current_state)
            if picked is None:
                # Couldn't pick (e.g. no candidates). Fall back to round-robin.
                if self._rotation_index >= len(self._agent_order):
                    self._rotation_index = 0
                agent_name = self._agent_order[self._rotation_index]
                self._rotation_index += 1
            else:
                agent_name = picked
                # Keep rotation_index advancing too so any code that reads
                # it (metrics, snapshots) sees forward progress.
                self._rotation_index = (self._rotation_index + 1) % max(1, len(self._agent_order))
        else:
            # Placeholder rotation: simple round-robin.
            if self._rotation_index >= len(self._agent_order):
                self._rotation_index = 0
            agent_name = self._agent_order[self._rotation_index]
            self._rotation_index += 1

        # Snapshot toolset for newly spawned agents (phase gating needs it).
        if self._workflow_phases and agent_name not in self._full_toolsets:
            agent = self._agents.get(agent_name)
            if agent:
                self._full_toolsets[agent_name] = dict(agent.tools)

        # Apply phase gating: filter agent's tools to current phase(s).
        active_phase = self._apply_phase_gate(agent_name)

        # Build input context.
        input_context = self._build_context(
            agent_name,
            history,
            current_state,
            active_phase=active_phase,
        )

        return CoordinationAction(
            action_type="invoke_agent",
            agent_name=agent_name,
            input_context=input_context,
            metadata={
                "turn": self._total_turns + 1,
                "rotation_index": self._rotation_index,
                "total_agents": len(self._agent_order),
            },
        )

    def is_complete(self, history: list, current_state: dict) -> bool:
        """Check if the task is finished."""
        # Check termination keyword in last message.
        if history:
            last = history[-1]
            content = last.content if isinstance(last, AgentMessage) else str(last)
            if self._termination_keyword and self._termination_keyword in content:
                return True

        # Check max turns.
        if len(history) >= self._max_turns:
            return True

        # Check consecutive errors.
        if self._consecutive_errors >= self._max_consecutive_errors:
            return True

        # Check if any agent called mark_task_done (single-signal completion).
        if self._blackboard:
            entry = self._blackboard.get("task_complete")
            if entry is not None and "DONE" in entry.value.upper():
                return True

        # Check if all agents report DONE on blackboard (fallback).
        if self._blackboard and self._agent_order:
            done_agents = set()
            for entry in self._blackboard.read_by_type("status"):
                if "DONE" in entry.value.upper():
                    done_agents.add(entry.author)
            if done_agents >= set(self._agent_order):
                return True

        # Check if graph-driven mode completed (strategy drove the graph).
        if self._graph_complete:
            return True

        # concurrent_blackboard mode: terminate as soon as every TODO is
        # done. Peers can race; the first cycle that closes out the board
        # ends the run.
        if (
            self._selection_mode == "concurrent_blackboard"
            and self._blackboard is not None
            and self._blackboard.all_todos_done()
        ):
            return True

        # Check if graph-routed handler signalled completion (handler drove the graph).
        if history:
            for msg in reversed(history):
                if isinstance(msg, AgentMessage):
                    if msg.metadata.get("graph_complete"):
                        return True
                    if "graph_state" not in msg.metadata:
                        break

        return False

    # -- Phase gating -----------------------------------------------------------

    def _get_open_phases(self) -> list[dict]:
        """Return workflow phases that are currently available.

        A phase is "open" when:
        - All previous phases' board_keys exist on the blackboard.
        - This phase's board_key does NOT exist on the blackboard.
        """
        if not self._workflow_phases or not self._blackboard:
            return []

        open_phases = []
        for i, phase in enumerate(self._workflow_phases):
            board_key = phase["board_key"]
            # Already completed?
            if self._blackboard.get(board_key) is not None:
                continue
            # Check all previous phases are completed.
            prereqs_met = True
            for prev in self._workflow_phases[:i]:
                if self._blackboard.get(prev["board_key"]) is None:
                    prereqs_met = False
                    break
            if prereqs_met:
                open_phases.append(phase)
        return open_phases

    def _apply_phase_gate(self, agent_name: str) -> str | None:
        """Filter agent's tools to only the current open phase(s).

        Returns a short description of the active phase for context
        injection, or None if gating is not active.
        """
        if not self._workflow_phases:
            return None

        # When staged_pipeline handler is active, skip tool restriction.
        # The handler's completion criteria already enforce ordering.
        if self._handler_is_staged_pipeline:
            return None

        agent = self._agents.get(agent_name)
        if agent is None:
            return None

        # Restore full toolset first (in case it was filtered last turn).
        full_tools = self._full_toolsets.get(agent_name)
        if full_tools:
            agent.tools = dict(full_tools)

        open_phases = self._get_open_phases()

        if not open_phases:
            # All phases done — only keep peer tools + final_answer.
            agent.tools = {
                name: tool for name, tool in agent.tools.items() if name in PEER_TOOL_NAMES or name == "final_answer"
            }
            return "ALL_PHASES_COMPLETE"

        # Collect allowed domain tool names from open phases.
        allowed_domain_tools: set[str] = set()
        phase_names = []
        for phase in open_phases:
            allowed_domain_tools.update(phase.get("tools", []))
            phase_names.append(phase["name"])

        # Filter: keep allowed domain tools + peer tools + final_answer.
        # Exclude mark_task_done until all phases are complete — prevents
        # premature termination mid-pipeline.
        agent.tools = {
            name: tool
            for name, tool in agent.tools.items()
            if name in allowed_domain_tools
            or (name in PEER_TOOL_NAMES and name != "mark_task_done")
            or name == "final_answer"
        }

        return ", ".join(phase_names)

    def _auto_complete_phases(self, history: list) -> None:
        """After an agent's turn, check if any phase's tools were called
        successfully and auto-write the phase board_key to the blackboard."""
        if not self._workflow_phases or not self._blackboard or not history:
            return

        last = history[-1]
        if not isinstance(last, AgentMessage) or last.error:
            return

        # Collect tool names from the last agent's tool calls.
        called_tools: set[str] = set()
        tool_calls = getattr(last, "tool_calls", None) or []
        for tc in tool_calls:
            tool_name = getattr(tc, "tool_name", None) or getattr(tc, "name", "")
            if tool_name and not getattr(tc, "error", None):
                called_tools.add(tool_name)

        if not called_tools:
            return

        # Check each incomplete phase: if any of its tools were called,
        # mark the phase complete.  Special case: parameter_setting phase
        # requires set_aircraft_parameters to return valid:true before
        # the phase advances. set_aircraft_parameters runs the inline
        # validation (static checks + ~5-10s Aviary model eval) that
        # the deprecated validate_parameters tool used to do, so its
        # "valid" field is the gate — calling it once with invalid
        # values does NOT advance the phase.
        for phase in self._workflow_phases:
            board_key = phase["board_key"]
            if self._blackboard.get(board_key) is not None:
                continue  # already complete
            phase_tools = set(phase.get("tools", []))
            if not (called_tools & phase_tools):
                continue

            # Gate: if this phase includes set_aircraft_parameters, only
            # auto-complete when its inline validation returned valid:true.
            if "set_aircraft_parameters" in phase_tools:
                validation_passed = False
                for tc in tool_calls:
                    tc_name = getattr(tc, "tool_name", None) or getattr(tc, "name", "")
                    tc_output = getattr(tc, "output", "") or ""
                    normalized = tc_output.replace(" ", "").replace("'", '"')
                    if tc_name == "set_aircraft_parameters" and '"valid":true' in normalized:
                        validation_passed = True
                        break
                if not validation_passed:
                    continue  # don't auto-complete until validation passes

            self._blackboard.write(
                key=board_key,
                value=f"completed by {last.agent_name}",
                author="system",
                entry_type="status",
            )

    # -- Internal helpers ------------------------------------------------------

    def _assemble_peer_prompt(self) -> str:
        """Build the final system prompt from base + toggle additions."""
        prompt = self._base_prompt

        if self._claiming_mode == "soft" and self._soft_claiming_addition:
            prompt += "\n" + self._soft_claiming_addition
        elif self._claiming_mode == "hard" and self._hard_claiming_addition:
            prompt += "\n" + self._hard_claiming_addition

        if self._predictive_knowledge and self._prediction_addition:
            prompt += "\n" + self._prediction_addition

        return prompt

    def _create_peer_agent(self, name: str) -> None:
        """Create a peer agent with full tools and register it."""
        tools = self._build_tool_list(name)

        from smolagents import ToolCallingAgent

        agent = ToolCallingAgent(
            tools=tools,
            model=self._context.model,
            name=name,
            description=f"Peer agent {name}",
            instructions=self._context.peer_prompt,
            max_steps=self._agent_max_steps,
            add_base_tools=False,
        )

        self._agents[name] = agent
        self._agent_order.append(name)

    def _build_tool_list(self, agent_name: str) -> list:
        """Build domain tools + per-agent peer tools."""
        from src.tools.networked_tools import (
            ClaimTodo,
            MarkTodoDone,
            MarkTodoFailed,
            ReadTodos,
        )

        peer_tools = [
            ReadBlackboard(self._context),
            WriteBlackboard(self._context, agent_name=agent_name),
            SpawnPeer(self._context, agent_name=agent_name),
            MarkTaskDone(self._context, agent_name=agent_name),
            # TODO-claim tools for the concurrent-blackboard selection mode.
            # Carried by every peer regardless of selection_mode so a YAML
            # tweak alone is enough to switch modes; the tools are no-ops
            # if the TODO board is empty.
            ReadTodos(self._context),
            ClaimTodo(self._context, agent_name=agent_name),
            MarkTodoDone(self._context, agent_name=agent_name),
            MarkTodoFailed(self._context, agent_name=agent_name),
        ]
        return list(self._domain_tools) + peer_tools

    # ------------------------------------------------------------------
    # Volunteer-broadcast selection (selection_mode == "volunteer")
    # ------------------------------------------------------------------
    #
    # Replaces the default round-robin pick with a parallel "do you want
    # to act next?" vote sent to every idle peer. Each peer reads a
    # compact blackboard snapshot + the last completed action and casts
    # VOTE: YES/NO + SCORE + REASON. The strongest YES is invoked.
    # Closer to the academic blackboard MAS pattern (arxiv 2510.01285 /
    # 2507.01701) and to AutoGen's GroupChatManager than fixed rotation.
    # Discovered 2026-05-25 after wandb 6fhhcem3 showed fixed round-robin
    # interacts badly with the iterative_feedback handler's per-turn
    # retries — agent_1 monopolized all 37 disciplinary calls because the
    # handler kept retrying it within a single strategy turn. Combined
    # fix: set iterative_feedback.max_retries: 1 for networked combos
    # AND use volunteer selection so the next strategy turn picks a
    # different peer based on what's on the blackboard, not the previous
    # turn's rotation index.

    _VOTE_PROMPT_TEMPLATE = (
        "You are {peer_name}, one of {peer_count} peer agents collaborating on "
        "a multi-disciplinary aircraft design task via a shared blackboard. "
        "There is NO orchestrator and NO fixed execution order. Each turn, "
        "every peer votes on whether to take the next action.\n\n"
        "Recent blackboard activity (most recent first):\n{board_summary}\n\n"
        "Last completed action (by another peer):\n{last_content}\n\n"
        "Should YOU act next? Vote honestly:\n"
        "- Vote YES with a HIGH score (0.7-1.0) if you have a clearly useful "
        "next step the team needs and no other peer has obviously claimed it.\n"
        "- Vote YES with a LOW score (0.3-0.6) if you could act but another "
        "peer might also be a fit.\n"
        "- Vote NO if there is no useful next step you can take, if you would "
        "duplicate work already on the board, or if you have repeatedly acted "
        "and another peer should get a turn.\n\n"
        "Reply in EXACTLY this format on three lines:\n"
        "VOTE: YES\n"
        "SCORE: 0.85\n"
        "REASON: one short sentence describing what you would do.\n"
        "(or VOTE: NO, no score required)"
    )

    def _render_blackboard_summary(self, max_entries: int = 8, max_chars_per: int = 200) -> str:
        """Compact blackboard render for the vote prompt — most-recent
        entries first, truncated content. Cheap to embed in many parallel
        prompts."""
        if self._blackboard is None:
            return "(blackboard empty — no entries yet)"
        try:
            entries = self._blackboard.read_all()
        except Exception:
            return "(blackboard read failed)"
        if not entries:
            return "(blackboard empty — no entries yet)"
        # Walk newest-first, truncate.
        lines: list[str] = []
        for entry in reversed(entries[-max_entries:]):
            author = getattr(entry, "author", "?")
            etype = getattr(entry, "entry_type", "?")
            key = getattr(entry, "key", "?")
            value = str(getattr(entry, "value", ""))[:max_chars_per]
            lines.append(f"- [{etype}] {key} by {author}: {value}")
        return "\n".join(lines)

    def _parse_vote(self, peer_name: str, content: str) -> tuple[str, float, str]:
        """Parse a peer's vote response. Returns (name, score, reason).
        score is 0.0 for NO/abstain, positive for YES."""
        import re

        if not content:
            return (peer_name, 0.0, "empty response")
        vote_match = re.search(r"VOTE:\s*(YES|NO)", content, re.IGNORECASE)
        if vote_match is None or vote_match.group(1).upper() == "NO":
            return (peer_name, 0.0, "voted NO")
        score_match = re.search(r"SCORE:\s*([0-9]*\.?[0-9]+)", content)
        score = float(score_match.group(1)) if score_match else 0.5
        # Clamp to [0.01, 1.0] so a YES never has zero weight.
        score = max(0.01, min(1.0, score))
        reason_match = re.search(r"REASON:\s*(.+?)(?:\n|$)", content)
        reason = reason_match.group(1).strip() if reason_match else ""
        return (peer_name, score, reason)

    def _cast_vote(self, peer_name: str, board_summary: str, last_content: str) -> tuple[str, float, str]:
        """Make a single lightweight LLM call asking one peer to vote."""
        prompt = self._VOTE_PROMPT_TEMPLATE.format(
            peer_name=peer_name,
            peer_count=len(self._agent_order),
            board_summary=board_summary,
            last_content=(last_content or "(none — first turn)")[:600],
        )
        # Use the shared model. Build a smolagents-compatible message dict
        # so we don't depend on ChatMessage class shape across versions.
        messages = [{"role": "user", "content": prompt}]
        try:
            response = self._volunteer_model.generate(messages)
        except Exception as e:
            return (peer_name, 0.0, f"vote error: {type(e).__name__}")
        content = getattr(response, "content", None) or str(response)
        if isinstance(content, list):
            # Some model backends return content as a list of parts.
            content = " ".join(
                str(p.get("text", p)) if isinstance(p, dict) else str(p)
                for p in content
            )
        return self._parse_vote(peer_name, str(content))

    def _select_via_volunteer(self, history: list, current_state: dict) -> str | None:
        """Run parallel volunteer broadcast. Returns the agent name of the
        strongest YES vote, or None if no idle peer can be found.

        Excludes the peer that JUST acted (`history[-1].agent_name`) so
        the team doesn't immediately re-pick the same peer. If every
        candidate votes NO, falls back to the first idle peer so the run
        doesn't deadlock."""
        if self._volunteer_model is None or not self._agent_order:
            return None

        last_agent = history[-1].agent_name if history else None
        last_content = history[-1].content if history else ""

        candidates = [
            n for n in self._agent_order
            if n != last_agent and n in self._agents and n != "system"
        ]
        if not candidates:
            # Edge case: only one peer total. Allow re-picking.
            candidates = [n for n in self._agent_order if n in self._agents and n != "system"]
        if not candidates:
            return None

        board_summary = self._render_blackboard_summary()

        # Parallel vote calls. ThreadPoolExecutor is safe because each
        # call goes to a separate HTTP request; the underlying Anthropic
        # SDK is thread-safe at the request level.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(candidates)) as ex:
            votes = list(
                ex.map(
                    lambda name: self._cast_vote(name, board_summary, last_content),
                    candidates,
                )
            )

        positive = [v for v in votes if v[1] > 0.0]
        if positive:
            # Highest score wins. Ties broken by agent_order (deterministic).
            positive.sort(key=lambda v: (-v[1], candidates.index(v[0])))
            return positive[0][0]
        # All voted NO — fall back to first candidate to avoid deadlock.
        return candidates[0]

    # -- Graph-driven mode ------------------------------------------------------

    def _graph_driven_next_step(
        self,
        history: list,
        current_state: dict,
    ) -> CoordinationAction:
        """Execute one graph state per turn, strategy-driven.

        Instead of delegating to the graph-routed handler (which runs the
        full graph in one call), the strategy drives the state machine:
        one state per coordinator turn.  This lets the networked rotation,
        blackboard, and peer tools operate between states.
        """
        # Advance graph from previous turn's output.
        if history:
            self._advance_graph_state(history)

        # Terminal check.
        if self._graph_current_state is None or self._graph_current_state in self._graph.terminal_states:
            self._graph_complete = True
            return CoordinationAction(
                action_type="terminate",
                agent_name=None,
                input_context="Graph reached terminal state",
            )

        state_def = self._graph.states.get(self._graph_current_state)
        if state_def is None:
            return CoordinationAction(
                action_type="error",
                agent_name=None,
                input_context=f"Graph state {self._graph_current_state!r} not found",
            )

        # Skip routing-only states (agent=None) — evaluate transitions
        # immediately and recurse.
        if state_def.agent is None:
            self._graph_state_dict["states_visited"].append(
                self._graph_current_state,
            )
            next_state = self._evaluate_graph_transitions(state_def)
            if next_state is None:
                self._graph_complete = True
                return CoordinationAction(
                    action_type="terminate",
                    agent_name=None,
                    input_context="Graph stuck at routing state",
                )
            self._graph_current_state = next_state
            return self._graph_driven_next_step(history, current_state)

        # Resolve agent: look up role alias in agents dict.
        agent_name = state_def.agent
        if agent_name not in self._agents:
            # Try fuzzy resolution (the aliases may have been registered).
            from src.coordination.graph_definition import resolve_agent_for_role

            try:
                resolved = resolve_agent_for_role(
                    agent_name,
                    self._agents,
                    self._graph_current_state,
                )
                # Find the key in the agents dict for this resolved agent.
                for key, val in self._agents.items():
                    if val is resolved:
                        agent_name = key
                        break
            except ValueError:
                # Fallback: round-robin among peers.
                peer_names = [k for k in self._agent_order if k.startswith("agent_")]
                if peer_names:
                    idx = self._rotation_index % len(peer_names)
                    agent_name = peer_names[idx]

        # Restore full toolset, but gate mark_task_done until graph reaches
        # a terminal state (prevents premature completion mid-pipeline).
        agent_obj = self._agents.get(agent_name)
        if agent_obj is not None:
            full_tools = self._full_toolsets.get(agent_name)
            if full_tools:
                agent_obj.tools = {name: tool for name, tool in full_tools.items() if name != "mark_task_done"}

        # Build context: focused graph state prompt only — do NOT include
        # the full task description, as that causes agents to run the
        # entire pipeline instead of just their assigned step.
        prompt = state_def.agent_prompt or ""
        try:
            prompt = prompt.format(**self._graph_state_dict)
        except KeyError:
            pass  # Missing keys left as-is

        parts = [
            "You are part of a networked team of agents. Each agent handles "
            "ONE step of the workflow. Complete ONLY the step below, write "
            "your result to the blackboard, then stop. Do NOT call tools "
            "for other steps — your peers will handle those.",
            f"GRAPH STATE: {self._graph_current_state}\nRole: {state_def.agent}\n\n{prompt}",
        ]

        # Blackboard context.
        if self._blackboard:
            toggle_config = {
                "peer_monitoring_visible": self._peer_monitoring_visible,
                "trans_specialist_knowledge": self._trans_specialist_knowledge,
                "predictive_knowledge": self._predictive_knowledge,
            }
            bb_ctx = self._blackboard.to_context_string(
                agent_name,
                toggle_config,
            )
            parts.append(f"Current Blackboard State:\n{bb_ctx}")

        # Recent history.
        if history:
            recent = history[-self._max_recent_messages :]
            lines = []
            for msg in recent:
                if isinstance(msg, AgentMessage):
                    status = f" [ERROR: {msg.error}]" if msg.error else ""
                    lines.append(f"[Turn {msg.turn_number}] {msg.agent_name}: {msg.content[:500]}{status}")
            if lines:
                parts.append("Recent History:\n" + "\n".join(lines))

        input_context = "\n\n".join(parts)

        self._rotation_index += 1

        # Resolve the actual peer agent name behind the role alias
        # so the viewer can distinguish real agents from role labels.
        peer_name = agent_name
        if agent_obj is not None:
            for key, val in self._agents.items():
                if val is agent_obj and key.startswith("agent_"):
                    peer_name = key
                    break

        # Build metadata with graph + resource state for SDA metrics.
        meta = {
            "turn": self._total_turns + 1,
            "rotation_index": self._rotation_index,
            "total_agents": len(self._agent_order),
            "graph_state": self._graph_current_state,
            "graph_role": state_def.agent,
            "peer_agent": peer_name,
            "bypass_handler": True,
        }
        # SDA fields: complexity and resource utilization.
        complexity = self._graph_state_dict.get("complexity")
        if complexity:
            meta["complexity"] = complexity
        visited = self._graph_state_dict.get("states_visited", [])
        total_states = len([s for s in self._graph.states.values() if s.agent is not None])
        meta["passes_remaining"] = max(0, total_states - len(visited) - 1)
        meta["passes_max"] = total_states

        return CoordinationAction(
            action_type="invoke_agent",
            agent_name=agent_name,
            input_context=input_context,
            metadata=meta,
        )

    def _advance_graph_state(self, history: list) -> None:
        """Advance the graph state machine based on the last turn's output."""
        if not history or self._graph is None:
            return

        last = history[-1]
        if not isinstance(last, AgentMessage) or last.error:
            return

        content = last.content or ""

        # Import extraction helpers from the graph handler module.
        import re

        from src.coordination.graph_routed_handler import (
            _extract_complexity,
            _extract_execution_result,
            _extract_review_result,
        )

        # Extract structured data from agent output.
        complexity = _extract_complexity(content)
        if complexity is not None:
            self._graph_state_dict["complexity"] = complexity

        review = _extract_review_result(content)
        if review:
            self._graph_state_dict.update(review)

        exec_result = _extract_execution_result(content)
        if exec_result:
            self._graph_state_dict.update(exec_result)

        # Session ID extraction.
        session_match = re.search(
            r"(?:SESSION_ID|session_id)['\"\s:=]+([0-9a-f-]{36})",
            content,
            re.IGNORECASE,
        )
        if session_match:
            self._graph_state_dict["session_id"] = session_match.group(1)

        # Also try tool call outputs for session_id.
        if "session_id" not in self._graph_state_dict:
            tool_calls = getattr(last, "tool_calls", None) or []
            for tc in tool_calls:
                if getattr(tc, "tool_name", "") == "create_session":
                    output = getattr(tc, "output", "") or ""
                    uuid_match = re.search(
                        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
                        r"-[0-9a-f]{4}-[0-9a-f]{12}",
                        output,
                        re.IGNORECASE,
                    )
                    if uuid_match:
                        self._graph_state_dict["session_id"] = uuid_match.group(0)
                        break

        # Convergence.
        lower = content.lower()
        if "converged" in lower:
            if "not converge" in lower or "failed to converge" in lower:
                self._graph_state_dict["converged"] = False
            else:
                self._graph_state_dict["converged"] = True

        # Record state visit.
        self._graph_state_dict["states_visited"].append(
            self._graph_current_state,
        )

        # Write graph state result to blackboard for peer visibility.
        if self._blackboard and content:
            self._blackboard.write(
                key=f"graph_{self._graph_current_state}",
                value=content[:800] if len(content) > 800 else content,
                author=last.agent_name,
                entry_type="result",
            )

        # Evaluate transitions.
        state_def = self._graph.states.get(self._graph_current_state)
        if state_def is not None:
            next_state = self._evaluate_graph_transitions(state_def)
            if next_state is not None:
                self._graph_current_state = next_state
            else:
                # No transition matched — stuck.  Mark complete to avoid loop.
                self._graph_complete = True

    def _evaluate_graph_transitions(self, state_def) -> str | None:
        """Evaluate transitions from a graph state, return next state."""
        from src.coordination.condition_evaluator import (
            ConditionParseError,
            evaluate_condition,
        )

        for trans in state_def.transitions:
            try:
                result = evaluate_condition(
                    trans.condition,
                    self._graph_state_dict,
                )
            except ConditionParseError:
                continue
            if result.matched:
                return trans.target
        return None

    def _build_context(
        self,
        agent_name: str,
        history: list,
        current_state: dict,
        active_phase: str | None = None,
    ) -> str:
        """Build the input context string for an agent's turn."""
        parts = []

        # Task description.
        task = current_state.get("task", self._task)
        if task:
            parts.append(f"Task: {task}")

        # Phase gating context — tell agents which phase they're in AND
        # show the full workflow so they understand what comes next.
        if active_phase == "ALL_PHASES_COMPLETE":
            parts.append(
                "PHASE STATUS: All workflow phases are complete. "
                "If results look good, call mark_task_done with the final metrics."
            )
        elif active_phase and self._workflow_phases:
            phase_map = "\n".join(
                f"  {'>> ' if p['name'] in active_phase else '   '}"
                f"Phase {i + 1} — {p['name']}: {', '.join(p.get('tools', []))}"
                f"{' [DONE]' if self._blackboard and self._blackboard.get(p['board_key']) else ''}"
                for i, p in enumerate(self._workflow_phases)
            )
            parts.append(
                f"YOUR CURRENT PHASE: {active_phase}\n"
                f"You can ONLY use tools for this phase. Complete it and "
                f"post results to the blackboard.\n\n"
                f"FULL WORKFLOW:\n{phase_map}\n"
                f"NOTE: parameter_setting phase requires set_aircraft_parameters "
                f"to return valid:true before advancing (validation runs inline)."
            )
        elif active_phase:
            parts.append(
                f"YOUR CURRENT PHASE: {active_phase}\n"
                "You can ONLY use tools for this phase. Complete it and "
                "post results to the blackboard. Do NOT attempt other phases."
            )

        # Blackboard state (filtered by toggles).
        if self._blackboard:
            toggle_config = {
                "peer_monitoring_visible": self._peer_monitoring_visible,
                "trans_specialist_knowledge": self._trans_specialist_knowledge,
                "predictive_knowledge": self._predictive_knowledge,
            }
            bb_context = self._blackboard.to_context_string(agent_name, toggle_config)
            parts.append(f"Current Blackboard State:\n{bb_context}")

        # Recent history (truncated).
        if history:
            recent = history[-self._max_recent_messages :]
            history_lines = []
            for msg in recent:
                if isinstance(msg, AgentMessage):
                    status = f" [ERROR: {msg.error}]" if msg.error else ""
                    history_lines.append(f"[Turn {msg.turn_number}] {msg.agent_name}: {msg.content[:500]}{status}")
            if history_lines:
                parts.append("Recent History:\n" + "\n".join(history_lines))

        return "\n\n".join(parts)

    # -- Properties ------------------------------------------------------------

    @property
    def blackboard(self) -> Blackboard | None:
        return self._blackboard

    @property
    def context(self) -> NetworkedContext | None:
        return self._context

    @property
    def agent_order(self) -> list[str]:
        return list(self._agent_order)

    @property
    def peer_prompt(self) -> str:
        return self._context.peer_prompt if self._context else ""


# -- Default prompt (used when config doesn't provide one) ---------------------

_DEFAULT_BASE_PROMPT = """\
You are a peer agent in a collaborative team. There is no manager \
or leader — all agents are equals working together to solve a task.

You have access to a shared blackboard where you and your peers \
post status updates, share results, and flag gaps.

Your process each turn:
1. Call read_blackboard to see what's happening
2. Look at what's been completed and what gaps exist
3. Decide what you can contribute
4. Do your work using your available tools
5. Post your results to the blackboard with write_blackboard \
   using entry_type "result"
6. Update your status with write_blackboard using entry_type "status"
7. If you see a gap that no existing agent can fill and more agents \
   would help, call spawn_peer

When the overall task is fully solved (all subtasks completed and \
results posted), call mark_task_done with a brief summary of what \
was accomplished."""
