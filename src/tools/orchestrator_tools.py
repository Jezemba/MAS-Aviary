"""Orchestrator-only tools for dynamic team creation and management.

Three tool classes available exclusively to the orchestrator agent:
- ListAvailableTools: discovers what tools exist in the system
- CreateAgent: creates a new specialist agent at runtime
- AssignTask: queues a task assignment for a worker agent

These tools operate on a shared OrchestratorContext that holds mutable
state (available tools, agent pool, assignment queue) across tool calls.
"""

import json
from dataclasses import dataclass, field
from typing import Callable

from smolagents import Tool, ToolCallingAgent
from smolagents.models import Model


@dataclass
class OrchestratorContext:
    """Shared mutable state for orchestrator tools.

    Created by OrchestratedStrategy during initialization and passed
    to each orchestrator tool so they all operate on the same state.
    """

    available_tools: dict  # name -> Tool instance (worker-assignable tools only)
    agents: dict  # name -> ToolCallingAgent (all agents, including orchestrator)
    model: Model
    created_agents: list = field(default_factory=list)  # names in creation order
    assignments: list = field(default_factory=list)  # [{agent_name, task, assigned_at_turn}]
    max_agents: int = 8
    worker_max_steps: int = 8
    turn_counter: int = 0
    system_agent_names: set = field(default_factory=set)  # pre-existing agent names
    worker_final_answer_checks: list[Callable] = field(default_factory=list)
    on_delegation_change: Callable | None = None  # callback after create/assign

    # Domain-agnostic phase coverage: maps phase name → list of tool names.
    # If non-empty, GatedFinalAnswer verifies that assigned agents collectively
    # cover every phase before accepting DELEGATION_COMPLETE.
    required_tool_phases: dict = field(default_factory=dict)

    # Result signals: GatedFinalAnswer blocks until all required signals are
    # present.  E.g. "simulation_succeeded" requires at least one
    # run_simulation tool call with success: true in the worker output.
    # Populated by OrchestratedStrategy scanning worker messages.
    required_result_signals: list = field(default_factory=list)
    result_signals: set = field(default_factory=set)

    # Orchestrator lifecycle: "active" keeps the orchestrator in the loop
    # so it can react to runtime signals (e.g. retry parameters after a
    # simulation failure); "setup_only" hands off to the execution_handler
    # after team creation and the orchestrator can't re-intervene. In
    # setup_only mode GatedFinalAnswer skips the result_signals check —
    # there's nothing the orchestrator can do to fix a simulation that
    # already failed, and re-rejecting DELEGATION_COMPLETE just produces
    # a wasteful re-creation loop (observed 2026-05-25 on
    # mdo_f25_orchestrated_staged_pipeline).
    lifecycle_mode: str = "active"


ORCHESTRATOR_TOOL_NAMES = frozenset(
    {
        "list_available_tools",
        "list_graph_roles",
        "create_agent",
        "assign_task",
        "final_answer",
    }
)

DELEGATION_COMPLETE = "DELEGATION_COMPLETE"


class GatedFinalAnswer(Tool):
    """Replacement ``final_answer`` that forces delegation first.

    During the creation phase the orchestrator must create at least one
    agent **and** assign at least one task before it is allowed to call
    ``final_answer``.  Additionally, the answer content must contain
    the ``DELEGATION_COMPLETE`` signal — this structurally prevents
    the orchestrator from passing actual solutions through final_answer
    instead of delegating to workers.

    If preconditions are not met or the content is invalid, the tool
    raises ValueError so smolagents' agent loop continues (returning
    an error string would terminate the loop because smolagents treats
    any ``final_answer`` return as the final answer).
    """

    name = "final_answer"
    description = (
        "Signal that delegation is complete. "
        "Call this with 'DELEGATION_COMPLETE' after you have created agents "
        "and assigned tasks. Do NOT pass code or solutions — only the signal."
    )
    inputs = {
        "answer": {
            "type": "string",
            "description": "Must be 'DELEGATION_COMPLETE'",
        },
    }
    output_type = "string"

    def __init__(self, context: "OrchestratorContext", **kwargs):
        super().__init__(**kwargs)
        self._context = context

    def forward(self, answer: str) -> str:
        ctx = self._context
        if not ctx.created_agents:
            raise ValueError(
                "You have not created any agents yet. "
                "You MUST call list_available_tools first, then "
                "create_agent to build specialists, then assign_task "
                "to give them work. Do NOT call final_answer until "
                "delegation is complete."
            )
        if not ctx.assignments:
            agents = ", ".join(ctx.created_agents)
            raise ValueError(
                f"You created agents ({agents}) but have not "
                "assigned any tasks. Call assign_task for each agent "
                "before calling final_answer."
            )
        if DELEGATION_COMPLETE not in answer:
            raise ValueError(
                f"The orchestrator must signal '{DELEGATION_COMPLETE}' to "
                "hand off to workers. Do NOT provide a solution directly — "
                "the assigned workers will execute the tasks. "
                f"Call final_answer with '{DELEGATION_COMPLETE}'."
            )

        # Phase coverage check: verify assigned agents collectively cover
        # every required workflow phase (setup, execution, evaluation, etc.).
        # SKIPPED in per_stage mode — orchestrator only delegates one
        # stage at a time, so it can't satisfy all phases upfront.
        if ctx.lifecycle_mode == "per_stage":
            return answer
        missing = _check_phase_coverage(ctx)
        if missing:
            phase_detail = "; ".join(
                f"'{phase}' (needs one of: {', '.join(tools)})" for phase, tools in missing.items()
            )
            raise ValueError(
                f"Workflow phases not covered: {phase_detail}. "
                "Create agents with the missing tools and assign them tasks "
                "before calling final_answer."
            )

        # Result signal check: verify that required runtime signals have
        # been observed (e.g. at least one successful simulation run).
        # Only enforced when workers have already run and attempted the
        # relevant operation — on the first delegation (before any workers
        # execute), the check is skipped because no signals can exist yet.
        #
        # In setup_only lifecycle mode the orchestrator can't re-intervene
        # after handoff, so re-rejecting DELEGATION_COMPLETE here just
        # spins up a wasteful re-creation loop. Skip the check; the run
        # surfaces the simulation failure via its metrics regardless.
        if ctx.lifecycle_mode == "setup_only":
            return answer
        missing_signals = _check_result_signals(ctx)
        if missing_signals:
            detail = ", ".join(missing_signals)
            raise ValueError(
                f"Required result signals not yet observed: {detail}. "
                "Simulation has not succeeded — all run_simulation calls "
                "returned errors. Do not call final_answer until at least "
                "one simulation succeeds. Re-assign parameter agents to "
                "correct the parameter combination using the error details "
                "from simulation_executor's output."
            )

        return answer


class ListAvailableTools(Tool):
    """Let the orchestrator discover what tools exist in the system."""

    name = "list_available_tools"
    description = (
        "Lists all available tools that can be assigned to worker agents. "
        "Returns tool names, descriptions, and input schemas. "
        "Does NOT include orchestrator management tools."
    )
    inputs = {}
    output_type = "string"

    def __init__(self, context: OrchestratorContext, **kwargs):
        super().__init__(**kwargs)
        self._context = context

    def forward(self) -> str:
        tools_info = []
        for tool in self._context.available_tools.values():
            info = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": {k: v.get("type", "string") for k, v in (tool.inputs or {}).items()},
            }
            tools_info.append(info)

        return json.dumps(
            {
                "note": (
                    "These are WORKER tools. You CANNOT call them directly. "
                    "Use create_agent to build a specialist, assign these tools "
                    "to it, then use assign_task to give it work."
                ),
                "tools": tools_info,
                "total_count": len(tools_info),
            }
        )


class ListGraphRoles(Tool):
    """Let the orchestrator discover what roles the execution graph requires.

    Returns the graph's role names so the orchestrator can create agents
    matching those roles.  If no graph is configured, returns an empty list.
    This is a deterministic function — not prompt-based — so the model
    cannot ignore or misinterpret the roles.
    """

    name = "list_graph_roles"
    description = (
        "Lists the agent roles required by the execution graph. "
        "If the execution handler uses a state machine, this returns "
        "the role names your agents should match (e.g. 'classifier', "
        "'coder', 'executor'). Name your agents with these exact role "
        "names so the graph router can find them."
    )
    inputs = {}
    output_type = "string"

    def __init__(self, graph_roles: list[str] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._roles = graph_roles or []

    def forward(self) -> str:
        if not self._roles:
            return json.dumps(
                {
                    "roles": [],
                    "note": "No graph roles configured. Name your agents freely.",
                }
            )
        return json.dumps(
            {
                "roles": self._roles,
                "note": (
                    "Create agents with these EXACT names so the graph router "
                    "can assign work to them. Each role corresponds to a state "
                    "in the execution graph."
                ),
            }
        )


class CreateAgent(Tool):
    """Create a new specialist agent at runtime."""

    name = "create_agent"
    description = (
        "Creates a new specialist agent with a unique name, persona "
        "(role description), and assigned tools. The agent becomes "
        "available for task assignment via assign_task."
    )
    inputs = {
        "name": {
            "type": "string",
            "description": "Unique identifier for this agent (e.g., 'geometry_planner')",
        },
        "persona": {
            "type": "string",
            "description": (
                "Natural language description of the agent's role and expertise. "
                "This becomes the agent's system prompt."
            ),
        },
        "tools": {
            "type": "any",
            "description": (
                "List of tool names to assign to this agent. Must be names from list_available_tools output."
            ),
        },
    }
    output_type = "string"

    def __init__(self, context: OrchestratorContext, **kwargs):
        super().__init__(**kwargs)
        self._context = context

    def forward(self, name: str, persona: str, tools) -> str:
        ctx = self._context

        # Parse tools parameter — may arrive as list, JSON string, or CSV.
        tool_names = _parse_tool_names(tools)

        # If the agent already exists (e.g. from a previous orchestrator
        # invocation within the same handler cycle), treat it as a no-op
        # success so the orchestrator can proceed to assign_task without
        # wasting a step on an error.
        if name in ctx.agents:
            return json.dumps(
                {
                    "success": True,
                    "agent_name": name,
                    "note": f"Agent '{name}' already exists and is ready for task assignment.",
                    "error": None,
                }
            )

        # Validate max agents.
        if len(ctx.created_agents) >= ctx.max_agents:
            return _error(name, f"Maximum agent limit ({ctx.max_agents}) reached")

        # Validate all tool names.
        for tn in tool_names:
            if tn not in ctx.available_tools:
                return _error(
                    name,
                    f"Tool '{tn}' not found. Available: {sorted(ctx.available_tools.keys())}",
                )

        # Complete the toolset for the worker's discipline before building it.
        tool_names, added_tools, discipline = _complete_toolset(
            name, persona, tool_names, ctx.available_tools
        )

        # Resolve tool objects.
        tool_objects = [ctx.available_tools[tn] for tn in tool_names]

        # Build system prompt from persona.
        system_prompt = f"Reasoning: medium\n{persona}"

        # Create the agent with optional reliability guardrails.
        agent = ToolCallingAgent(
            tools=tool_objects,
            model=ctx.model,
            name=name,
            description=persona,
            instructions=system_prompt,
            max_steps=ctx.worker_max_steps,
            add_base_tools=False,
            final_answer_checks=ctx.worker_final_answer_checks or None,
        )

        # Register in the shared agent pool.
        ctx.agents[name] = agent
        ctx.created_agents.append(name)

        # Notify strategy so it can sync tool visibility (e.g. inject
        # final_answer once preconditions are met).
        if ctx.on_delegation_change:
            ctx.on_delegation_change()

        result = {
            "success": True,
            "agent_name": name,
            "persona": persona,
            "tools_assigned": tool_names,
            "error": None,
        }
        if added_tools:
            result["tools_added"] = added_tools
            plural = "them" if len(added_tools) > 1 else "it"
            result["note"] = (
                f"Added {', '.join(added_tools)} -- a {discipline} worker cannot "
                f"complete its task without {plural}. Assign the task as planned; "
                "the agent now has what it needs."
            )
        return json.dumps(result)


# ---------------------------------------------------------------------------
# Discipline toolset completion
# ---------------------------------------------------------------------------

# A worker whose toolset cannot perform its role fails two steps later, as a
# missing artifact, far from the cause. Measured 2026-08-12:
#
#   B33  the aero worker was created with
#        ['create_su2_session', 'set_mesh', 'run_su2_solver'] and no way to
#        configure the solver. Responses named configure_from_cpacs five times;
#        the worker returned final_answer each time. Zero fuel.
#   B37  the geometry worker was created without generate_volume_mesh, which its
#        own graph state declares in TOOLS. It tried 10 times --
#        "Unknown tool generate_volume_mesh, should be one of: open_cpacs, ..."
#        -- then gave up. No mesh, so no solve, so no aero.
#
# create_agent already validated that requested tools EXIST; nothing checked that
# the set could do the job. These are the tools without which a role cannot
# complete at all -- not everything it might use.
#
# The response reports what was added rather than erroring, because an error here
# asks the caller to compose a different toolset, and the measured behaviour on a
# missing capability is abandonment, not correction (B33/B37 above). Supplying it
# is the same choice the data plane makes for session config and mesh payloads.
_DISCIPLINE_REQUIRED_TOOLS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # discipline: (name/persona keywords, tools it cannot work without)
    "geometry": (
        ("geometry", "geometric", "cpacs", "airframe", "wing_design"),
        ("open_cpacs", "generate_volume_mesh", "set_high_level_parameters"),
    ),
    "aero": (
        ("aero", "aerodynam", "cfd", "su2", "drag", "lift"),
        ("create_su2_session", "set_mesh", "configure_from_cpacs",
         "run_su2_solver", "read_history_csv"),
    ),
    "structures": (
        ("structur", "mass", "weight", "stress"),
        ("estimate_mass",),
    ),
    "propulsion": (
        ("propuls", "engine", "cycle", "thrust", "pycycle"),
        ("create_cycle_model", "run_cycle"),
    ),
    "mission": (
        ("mission", "aviary", "trajectory", "fuel", "simulat", "performance"),
        # create_session first: without it every other aviary call runs against
        # a session that does not exist. Measured (final4 run 1/8): a worker
        # given only run_simulation/get_results invented "aviary_session_1" and
        # made 11 calls against it. Omitting it here was the same mistake as
        # wiring the CPACS path into create_su2_session without the numerics --
        # supplying a capability while leaving out its prerequisite.
        ("create_session", "set_aircraft_parameters", "run_simulation", "get_results"),
    ),
}


def _complete_toolset(name: str, persona: str, tool_names: list[str],
                      available: dict) -> tuple[list[str], list[str], str | None]:
    """Add the tools the worker's role cannot function without.

    Returns (tool_names, added, discipline). Only tools that actually exist in
    ``available`` are added, and only when the worker already carries at least
    one tool of that discipline -- so a deliberately narrow helper agent is not
    silently promoted into a full discipline worker.
    """
    haystack = f"{name} {persona}".lower()
    present = set(tool_names)
    added: list[str] = []
    matched: str | None = None

    for discipline, (keywords, required) in _DISCIPLINE_REQUIRED_TOOLS.items():
        by_keyword = any(k in haystack for k in keywords)
        by_tools = any(r in present for r in required)
        if not (by_keyword and by_tools):
            continue
        matched = discipline
        for tool in required:
            if tool not in present and tool in available:
                tool_names.append(tool)
                present.add(tool)
                added.append(tool)
    return tool_names, added, matched


class AssignTask(Tool):
    """Record a task assignment for a worker agent."""

    name = "assign_task"
    description = (
        "Assigns a specific task to a worker agent created via create_agent. "
        "This queues the task for execution — it does not run the agent immediately."
    )
    inputs = {
        "agent_name": {
            "type": "string",
            "description": "Name of the agent to assign work to (must have been created via create_agent)",
        },
        "task": {
            "type": "string",
            "description": "Natural language description of what this agent should do",
        },
    }
    output_type = "string"

    def __init__(self, context: OrchestratorContext, **kwargs):
        super().__init__(**kwargs)
        self._context = context

    def forward(self, agent_name: str, task: str) -> str:
        ctx = self._context

        # Validate agent exists.
        if agent_name not in ctx.agents:
            return _error(agent_name, f"Agent '{agent_name}' not found")

        # Validate agent was created by orchestrator (not a system agent).
        if agent_name in ctx.system_agent_names:
            return _error(
                agent_name,
                f"Agent '{agent_name}' is a system agent, not a worker",
            )

        # Record assignment.
        assignment = {
            "agent_name": agent_name,
            "task": task,
            "assigned_at_turn": ctx.turn_counter,
        }
        ctx.assignments.append(assignment)

        # Notify strategy so it can sync tool visibility.
        if ctx.on_delegation_change:
            ctx.on_delegation_change()

        return json.dumps(
            {
                "success": True,
                "agent_name": agent_name,
                "task": task,
                "queue_position": len(ctx.assignments),
            }
        )


# --- Helpers ------------------------------------------------------------------


def _parse_tool_names(tools) -> list[str]:
    """Normalize tool names from various input formats."""
    if isinstance(tools, list):
        return [str(t).strip() for t in tools if str(t).strip()]
    if isinstance(tools, str):
        # Try JSON array first.
        try:
            parsed = json.loads(tools)
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except (json.JSONDecodeError, TypeError):
            pass
        # Fall back to comma-separated.
        return [t.strip() for t in tools.split(",") if t.strip()]
    # Single value fallback.
    return [str(tools)]


def _check_phase_coverage(ctx: OrchestratorContext) -> dict[str, list[str]]:
    """Check that assigned agents collectively cover all required phases.

    Returns a dict of {phase_name: [required_tools]} for phases that are
    NOT covered.  An empty dict means all phases are covered.
    """
    phases = ctx.required_tool_phases
    if not phases:
        return {}

    # Build set of tool names across all assigned agents.
    assigned_names = {a["agent_name"] for a in ctx.assignments}
    covered_tools: set[str] = set()
    for name in assigned_names:
        agent = ctx.agents.get(name)
        if agent is not None:
            covered_tools.update(getattr(agent, "tools", {}).keys())

    missing: dict[str, list[str]] = {}
    for phase, tool_names in phases.items():
        if not any(t in covered_tools for t in tool_names):
            missing[phase] = tool_names
    return missing


def _check_result_signals(ctx: OrchestratorContext) -> list[str]:
    """Check required result signals, returning names of missing ones.

    Only signals whose corresponding ``_attempted`` marker is present are
    enforced.  For example, ``simulation_succeeded`` is only checked when
    ``simulation_attempted`` is in ``ctx.result_signals`` — this means a
    ``run_simulation`` call was observed (successful or not).  On the
    first delegation before any workers run, no attempt markers exist, so
    the check passes and delegation proceeds normally.
    """
    missing: list[str] = []
    for signal in ctx.required_result_signals:
        # Derive the attempted-marker name:
        #   "simulation_succeeded" → "simulation_attempted"
        parts = signal.rsplit("_", 1)
        attempted_key = f"{parts[0]}_attempted" if len(parts) == 2 else f"{signal}_attempted"
        # Only enforce if the operation was actually attempted.
        if attempted_key in ctx.result_signals and signal not in ctx.result_signals:
            missing.append(signal)
    return missing


def _error(agent_name: str, message: str) -> str:
    """Return a JSON error response."""
    return json.dumps(
        {
            "success": False,
            "agent_name": agent_name,
            "error": message,
        }
    )
