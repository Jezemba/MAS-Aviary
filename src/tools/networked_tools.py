"""Peer tools for the networked coordination strategy.

Three Tool subclasses available to ALL peer agents (in addition to
whatever domain tools they have):
  - ReadBlackboard: read shared blackboard state
  - WriteBlackboard: post status, results, claims, gaps, predictions
  - SpawnPeer: create a new peer agent when a gap is identified

These tools operate on a shared NetworkedContext that holds mutable
state (blackboard, agent pool, model, config) across tool calls.
"""

import json
from dataclasses import dataclass, field

from smolagents import Tool, ToolCallingAgent
from smolagents.models import Model

from src.coordination.blackboard import Blackboard
from src.tools.artifact_checks import with_artifact_checks

PEER_TOOL_NAMES = frozenset({
    "read_blackboard",
    "write_blackboard",
    "spawn_peer",
    "mark_task_done",
    # TODO-claim tools for the concurrent-blackboard selection mode.
    "read_todos",
    "claim_todo",
    "mark_todo_done",
    "mark_todo_failed",
    "wait_for_board",
})


@dataclass
class NetworkedContext:
    """Shared mutable state for networked peer tools.

    Created by NetworkedStrategy during initialization and passed
    to each peer tool so they all operate on the same state.
    """

    blackboard: Blackboard
    agents: dict  # name -> ToolCallingAgent (all agents)
    model: Model
    all_tools: list  # full tool set (domain + peer tools) for new agents
    peer_prompt: str  # assembled system prompt for new agents
    agent_max_steps: int = 8
    max_agents: int = 10
    agent_counter: int = 0  # for auto-naming: agent_1, agent_2, ...
    spawned_agents: list = field(default_factory=list)  # names in spawn order
    config: dict = field(default_factory=dict)  # toggle config for filtering


class ReadBlackboard(Tool):
    """Let an agent see the current state of the shared blackboard."""

    name = "read_blackboard"
    description = (
        "Reads the shared blackboard to see what peers are working on, "
        "what's been completed, active claims, and identified gaps. "
        "Optionally filter by entry type."
    )
    inputs = {
        "entry_type": {
            "type": "string",
            "description": (
                'Filter entries by type: "status", "claim", "result", "gap", "prediction", or "all" (default "all").'
            ),
            "nullable": True,
        },
    }
    output_type = "string"

    def __init__(self, context: NetworkedContext, **kwargs):
        super().__init__(**kwargs)
        self._context = context

    def forward(self, entry_type: str | None = None) -> str:
        ctx = self._context
        bb = ctx.blackboard

        if entry_type and entry_type != "all":
            entries = bb.read_by_type(entry_type)
        else:
            entries = bb.read_all()

        # Apply toggle filtering via the blackboard's context renderer.
        # We re-filter manually to return structured JSON.
        filtered = []
        predictive = ctx.config.get("predictive_knowledge", False)
        trans_specialist = ctx.config.get("trans_specialist_knowledge", True)
        peer_monitoring = ctx.config.get("peer_monitoring_visible", True)

        from src.coordination.blackboard import _strip_metrics, _truncate_result

        for e in entries:
            if e.entry_type == "prediction" and not predictive:
                continue

            display_value = e.value
            if e.entry_type == "result" and not trans_specialist:
                display_value = _truncate_result(display_value)
            if not peer_monitoring:
                display_value = _strip_metrics(display_value)

            filtered.append(
                {
                    "key": e.key,
                    "value": display_value,
                    "author": e.author,
                    "entry_type": e.entry_type,
                    "timestamp": e.timestamp,
                    "version": e.version,
                }
            )

        # Summary info.
        claims = bb.get_claims()
        active_claims = [c.key for c in claims]
        gaps = bb.read_by_type("gap")
        identified_gaps = [g.key for g in gaps]

        return json.dumps(
            {
                "entries": filtered,
                "total_entries": len(filtered),
                "active_claims": active_claims,
                "identified_gaps": identified_gaps,
            }
        )


class WriteBlackboard(Tool):
    """Post status, results, claims, or identified gaps to the blackboard."""

    name = "write_blackboard"
    description = (
        "Write an entry to the shared blackboard. Use entry_type 'status' "
        "to report what you're doing, 'claim' to claim a subtask, 'result' "
        "to post completed work, 'gap' to flag unaddressed work, or "
        "'prediction' to predict what another agent will do."
    )
    inputs = {
        "key": {
            "type": "string",
            "description": ('Unique identifier for this entry (e.g., "geometry_planning", "agent_2_status").'),
        },
        "value": {
            "type": "string",
            "description": "The content to post.",
        },
        "entry_type": {
            "type": "string",
            "description": ('One of: "status", "claim", "result", "gap", "prediction".'),
        },
    }
    output_type = "string"

    def __init__(self, context: NetworkedContext, agent_name: str = "", **kwargs):
        super().__init__(**kwargs)
        self._context = context
        self._agent_name = agent_name

    def forward(self, key: str, value: str, entry_type: str) -> str:
        bb = self._context.blackboard
        entry, warning = bb.write(key, value, self._agent_name, entry_type)

        if entry is None:
            # Hard claim rejection.
            return json.dumps(
                {
                    "success": False,
                    "key": key,
                    "entry_type": entry_type,
                    "error": warning,
                }
            )

        return json.dumps(
            {
                "success": True,
                "key": entry.key,
                "entry_type": entry.entry_type,
                "version": entry.version,
                "warning": warning,
            }
        )


class SpawnPeer(Tool):
    """Create a new peer agent when a gap is identified."""

    name = "spawn_peer"
    description = (
        "Spawn a new peer agent to help with unaddressed work. The new "
        "agent gets the same tools and base prompt as all other peers. "
        "Provide a reason explaining why a new agent is needed."
    )
    inputs = {
        "reason": {
            "type": "string",
            "description": ('Why this new agent is needed (e.g., "No agent is handling fillet operations").'),
        },
    }
    output_type = "string"

    def __init__(self, context: NetworkedContext, agent_name: str = "", **kwargs):
        super().__init__(**kwargs)
        self._context = context
        self._agent_name = agent_name

    def forward(self, reason: str) -> str:
        ctx = self._context

        # Check agent limit.
        total_agents = len(ctx.agents)
        if total_agents >= ctx.max_agents:
            return json.dumps(
                {
                    "success": False,
                    "new_agent_name": None,
                    "reason": reason,
                    "total_agents": total_agents,
                    "error": f"Maximum agent limit ({ctx.max_agents}) reached",
                }
            )

        # Generate name.
        ctx.agent_counter += 1
        new_name = f"agent_{ctx.agent_counter}"
        # Ensure uniqueness (shouldn't collide, but be safe).
        while new_name in ctx.agents:
            ctx.agent_counter += 1
            new_name = f"agent_{ctx.agent_counter}"

        # Create agent with full tool set.
        agent = ToolCallingAgent(
            tools=list(ctx.all_tools),
            model=ctx.model,
            name=new_name,
            description=f"Peer agent spawned because: {reason}",
            instructions=ctx.peer_prompt,
            max_steps=ctx.agent_max_steps,
            add_base_tools=False,
            final_answer_checks=with_artifact_checks(),  # B31
        )

        # Register.
        ctx.agents[new_name] = agent
        ctx.spawned_agents.append(new_name)

        # Post gap entry to blackboard documenting why spawned.
        ctx.blackboard.write(
            key=f"spawn_{new_name}",
            value=f"New peer {new_name} spawned by {self._agent_name}: {reason}",
            author=self._agent_name or "system",
            entry_type="gap",
        )

        return json.dumps(
            {
                "success": True,
                "new_agent_name": new_name,
                "reason": reason,
                "total_agents": len(ctx.agents),
                "error": None,
            }
        )


class MarkTaskDone(Tool):
    """Signal that the overall task is fully complete.

    Writes a DONE status to the shared blackboard under the key
    "task_complete".  The NetworkedStrategy's is_complete() checks for
    this entry and breaks the coordinator loop on the next iteration.

    Call this once when you are certain all subtasks are finished and
    the results are on the blackboard.  The first agent to call it
    stops the run — no other agent needs to call it.
    """

    name = "mark_task_done"
    description = (
        "Signal that the overall task is fully complete. Call this when "
        "all subtasks are done and results are posted to the blackboard. "
        "Provide a brief summary of what was accomplished. The first agent "
        "to call this stops the entire run."
    )
    inputs = {
        "summary": {
            "type": "string",
            "description": (
                'Brief summary of what was accomplished (e.g., "STL generated and evaluated: PCD=0.035, eval=success").'
            ),
        },
    }
    output_type = "string"

    def __init__(self, context: NetworkedContext, agent_name: str = "", **kwargs):
        super().__init__(**kwargs)
        self._context = context
        self._agent_name = agent_name

    def forward(self, summary: str) -> str:
        bb = self._context.blackboard
        value = f"DONE: {summary}"
        entry, _ = bb.write("task_complete", value, self._agent_name, "status")
        return json.dumps(
            {
                "success": entry is not None,
                "key": "task_complete",
                "summary": summary,
            }
        )


# -- TODO claim tools (concurrent-blackboard selection mode) ------------------
#
# These tools support the CodeCRDT-style coordination pattern
# (arxiv 2510.18893) used by NetworkedStrategy.selection_mode =
# "concurrent_blackboard". Multiple peer threads race to claim TODOs
# from the shared blackboard. The atomic claim_todo() / complete_todo()
# primitives on Blackboard give at-most-one-winner safety.
#
# Workflow each peer follows in its ReAct loop:
#   1. read_blackboard (or read_todos) -> see what's pending / claimed / done
#   2. claim_todo(name) -> atomic check-and-set
#   3. if claim succeeded: execute the discipline work via MCP tools
#   4. mark_todo_done(name, result) -> record completion
#   5. (loop until no pending TODOs remain)


class ReadTodos(Tool):
    """Render the TODO board so a peer can decide what to claim next."""

    name = "read_todos"
    description = (
        "Show the current TODO board: every TODO, its status (pending / "
        "claimed / done / failed), which peer claimed it (if any), and the "
        "stored result. Use this BEFORE claim_todo so you pick a pending "
        "TODO that no other peer is already doing. The TODO list itself is "
        "seeded once at run start; you cannot create new TODOs."
    )
    inputs: dict = {}
    output_type = "string"

    def __init__(self, context: NetworkedContext, **kwargs):
        super().__init__(**kwargs)
        self._context = context

    def forward(self) -> str:  # type: ignore[override]
        return self._context.blackboard.render_todos()


class ClaimTodo(Tool):
    """Atomically claim a pending TODO for this peer.

    Returns success=true if THIS peer is the one recorded as assigned_to
    after the call; success=false if another peer beat you to it (in
    which case call read_todos and try a different TODO — do NOT retry
    the same one in a loop).
    """

    name = "claim_todo"
    description = (
        "Atomically claim a pending TODO for yourself. Returns success=true "
        "with a confirmation message if you got the claim, success=false "
        "if another peer is already on it. If your claim fails, call "
        "read_todos and pick a different TODO; do NOT retry the same one. "
        "After a successful claim, execute the discipline work and then "
        "call mark_todo_done (or mark_todo_failed if you have to give up)."
    )
    inputs: dict = {
        "todo_name": {
            "type": "string",
            "description": "The name field of a TODO from the board.",
        },
    }
    output_type = "string"

    def __init__(self, context: NetworkedContext, agent_name: str = "", **kwargs):
        super().__init__(**kwargs)
        self._context = context
        self._agent_name = agent_name

    def forward(self, todo_name: str) -> str:  # type: ignore[override]
        from src.tools import work_claims

        # B89: a peer that holds unfinished work does not get to reserve more. agent_1 claimed
        # 'aero' at 22:52 while still doing 'geometry', so it held two and agent_3 could take
        # neither -- agent_3 then lost three claims in a row and did no work in 927 s. The
        # second TODO stays ON THE BOARD, visible to whoever is waiting (Jessica, 2026-09-16).
        # Auto-claim-on-touch is deliberately NOT capped: a claim made by actually running the
        # work means "I am doing this now", and blocking that would strand a peer mid-flow.
        holding = work_claims.held_unfinished(self._agent_name)
        if holding and todo_name not in holding:
            free = work_claims.unclaimed_now()
            return json.dumps({
                "success": False,
                "todo_name": todo_name,
                "attempted_by": self._agent_name,
                "current_owner": None,
                "unclaimed": free,
                "board": work_claims.board_snapshot(),
                "message": (
                    f"You still hold {', '.join(holding)} and it is not finished, so "
                    f"{todo_name!r} was left on the board for a peer who has nothing. Finish "
                    f"yours first: mark_todo_done('{holding[0]}', result='<short summary of what "
                    f"it produced>'), or mark_todo_failed('{holding[0]}') if you cannot. "
                    + (f"Still unclaimed for others: {', '.join(free)}." if free else "")
                ),
            })

        ok, msg = self._context.blackboard.claim_todo(todo_name, self._agent_name)
        # Resolve the current owner from the blackboard so the response
        # carries it as a structured field. Under concurrent stdout
        # interleaving the viz attribution heuristic needs `attempted_by`
        # to distinguish a rejected claim's caller from the winner the
        # message names.
        current_owner: str | None = None
        for todo in self._context.blackboard.read_todos():
            if todo.name == todo_name:
                current_owner = todo.assigned_to or None
                break
        # B89: every claim result carries the board AS IT IS NOW. The peers share one prompt
        # built once per turn, so a rejected claim used to leave the peer with nothing but that
        # stale snapshot and the words "pick a different TODO" -- naming none. Four rejections
        # cost 1,117 s in validate10. The claim is the one place a peer is guaranteed to look,
        # so the true state rides back on it, success or failure.
        free = work_claims.unclaimed_now()
        if not ok and free:
            msg = (f"{msg}. Unclaimed RIGHT NOW: {', '.join(free)} -- "
                   f"call claim_todo('{free[0]}') and start there. Structures and propulsion "
                   "need no aero, so they can run while someone else solves.")
        elif not ok:
            msg = (f"{msg}. Nothing is unclaimed right now, so do not keep trying. If you hold a "
                   "TODO, get on with it. If you hold none, call "
                   "wait_for_board(reason='everything is claimed') -- it costs you no thinking "
                   "time, returns the moment a peer finishes or gives up a TODO, and claims the "
                   "first one that becomes takeable.")
        return json.dumps(
            {
                "success": ok,
                "todo_name": todo_name,
                "attempted_by": self._agent_name,
                "current_owner": current_owner,
                "unclaimed": free,
                "board": work_claims.board_snapshot(),
                "message": msg,
            }
        )



class WaitForBoard(Tool):
    """Wait for the board to move, instead of losing steps to claims you cannot win (B90).

    validate10: agent_3 spent four steps and 1,394 s losing claims and doing nothing, while
    `mission`, `simulation` and `evaluation` sat unclaimed but BLOCKED -- their dependencies
    were still being worked by the other two peers. There was no way for a peer to say "there
    is nothing I can start yet"; the only options were to retry a claim or to stop.

    So waiting is a state a peer can enter (Jessica, 2026-09-16). It costs no generation -- the
    peer is inside a tool call, not thinking -- so the other peers get the whole batch while it
    waits. It returns THE MOMENT the board moves, and it is bounded well under the B85 tool
    watchdog so it can never wedge a run.

    It refuses to waste time: if something is takeable now, or nobody else is working (so
    nothing can change), it returns immediately and says so.
    """

    name = "wait_for_board"
    description = (
        "Wait until another peer changes the TODO board -- claims, finishes or gives up a TODO. "
        "Use this when everything is claimed, or when what is left is BLOCKED because it depends "
        "on work another peer is still doing: it is how you say 'there is nothing I can start "
        "yet' instead of retrying a claim you cannot win. It costs you no thinking time, returns "
        "as soon as the board moves, and tells you what became claimable. If something is "
        "already takeable, or nobody else is working, it returns straight away and says so -- so "
        "it can never trap you. By default it claims the first TODO that becomes takeable; pass "
        "claim_when_free=false if you only want to look."
    )
    inputs: dict = {
        "reason": {
            "type": "string",
            "description": "What you are waiting for, e.g. 'aero is blocked on geometry'.",
            "nullable": True,
        },
        "seconds": {
            "type": "number",
            "description": "How long to wait at most (default 120, capped at 300).",
            "nullable": True,
        },
        "claim_when_free": {
            "type": "boolean",
            "description": "Claim the first TODO that becomes takeable (default true).",
            "nullable": True,
        },
    }
    output_type = "string"

    _POLL_SECONDS = 1.0
    _DEFAULT_SECONDS = 120.0
    _MAX_SECONDS = 300.0        # far below the B85 tool watchdog, so a wait is never a wedge

    def __init__(self, context: NetworkedContext, agent_name: str = "", **kwargs):
        super().__init__(**kwargs)
        self._context = context
        self._agent_name = agent_name

    def forward(self, reason: str | None = None, seconds: float | None = None,
                claim_when_free: bool | None = None) -> str:  # type: ignore[override]
        import time

        from src.tools import work_claims

        budget = min(float(seconds or self._DEFAULT_SECONDS), self._MAX_SECONDS)
        do_claim = True if claim_when_free is None else bool(claim_when_free)

        takeable = work_claims.claimable_now(self._agent_name)
        if work_claims.held_unfinished(self._agent_name):
            return self._answer(False, 0.0, "You already hold unfinished work -- do that instead of "
                                            "waiting. Finish it with mark_todo_done(name, result=...) "
                                            "or give it up with mark_todo_failed(name).")
        if takeable:
            return self._maybe_claim(takeable, do_claim, 0.0, False,
                                     "There is work you can take right now, so there is nothing to "
                                     "wait for.")
        if not work_claims.anyone_working(self._agent_name):
            return self._answer(False, 0.0, "Nobody else is working, so the board cannot change. "
                                            "Nothing here needs you: post what you can help with via "
                                            "write_blackboard(entry_type='gap') and stop.")

        before = work_claims.board_signature()
        started = time.monotonic()
        changed = False
        while time.monotonic() - started < budget:
            time.sleep(self._POLL_SECONDS)
            if work_claims.board_signature() != before:
                changed = True
                break
        waited = round(time.monotonic() - started, 1)
        work_claims.note_wait(self._agent_name, waited, changed)

        takeable = work_claims.claimable_now(self._agent_name)
        if changed and takeable:
            return self._maybe_claim(takeable, do_claim, waited, True,
                                     "The board moved while you waited.")
        if changed:
            return self._answer(True, waited, "The board moved, but nothing is takeable yet -- what "
                                              "is left still depends on work in progress.")
        return self._answer(False, waited, f"Nothing moved in {waited:.0f}s. Wait again if the work "
                                           "you need is still in progress, or stop if you have "
                                           "nothing to contribute.")

    def _maybe_claim(self, takeable: list, do_claim: bool, waited: float, changed: bool,
                     note: str) -> str:
        if not do_claim:
            return self._answer(changed, waited, f"{note} Claimable now: {', '.join(takeable)}.")
        ok, msg = self._context.blackboard.claim_todo(takeable[0], self._agent_name)
        if ok:
            return self._answer(changed, waited,
                                f"{note} Claimed {takeable[0]!r} for you -- start it now. "
                                "read_procedure(role='<name>') lists its tools in order.",
                                claimed=takeable[0])
        return self._answer(changed, waited, f"{note} Claimable now: {', '.join(takeable)} "
                                             f"(the claim on {takeable[0]!r} did not land: {msg}).")

    def _answer(self, changed: bool, waited: float, message: str, claimed: str | None = None) -> str:
        from src.tools import work_claims

        return json.dumps({
            "success": True,
            "waited_seconds": waited,
            "board_changed": changed,
            "claimed": claimed,
            "claimable_now": work_claims.claimable_now(self._agent_name),
            "blocked": work_claims.blocked_now(),
            "board": work_claims.board_snapshot(),
            "message": message,
        })


class MarkTodoDone(Tool):
    """Mark a TODO as completed with its result."""

    name = "mark_todo_done"
    description = (
        "Record a TODO as done. Only the peer that successfully claimed "
        "the TODO can mark it done. The result string is the short summary "
        "that downstream peers will read off the blackboard (e.g. "
        '"CL=0.589, CD=0.0303, L/D=19.4" for an aero TODO).'
    )
    inputs: dict = {
        "todo_name": {
            "type": "string",
            "description": "Name of the TODO you completed.",
        },
        "result": {
            "type": "string",
            "description": "Short result summary to post on the blackboard.",
        },
    }
    output_type = "string"

    def __init__(self, context: NetworkedContext, agent_name: str = "", **kwargs):
        super().__init__(**kwargs)
        self._context = context
        self._agent_name = agent_name

    def forward(self, todo_name: str, result: str) -> str:  # type: ignore[override]
        # B86: releasing is how the work stops being reserved, so it must carry the summary
        # the next peer reads off the board. An empty result releases the claim and tells
        # nobody anything, which is how a peer ends up redoing work that was already done.
        mine = any(t.name == todo_name and t.assigned_to == self._agent_name
                   for t in self._context.blackboard.read_todos())
        if mine and len((result or "").strip()) < 8:
            return json.dumps({
                "success": False,
                "error_code": "SUMMARY_REQUIRED",
                "todo_name": todo_name,
                "error": (
                    f"{todo_name} was not released: mark_todo_done needs a short result summary, "
                    "because that summary is what the other peers read off the blackboard. "
                    "Give the values your tools actually produced, e.g. "
                    "result='CL=0.589, CD=0.0303, L/D=19.4' or result='mesh 660k cells, ref "
                    "generate_volume_mesh__mesh_base64'. Your claim is still yours until you do."
                ),
            })
        ok, msg = self._context.blackboard.complete_todo(
            todo_name, self._agent_name, result
        )
        return json.dumps({"success": ok, "todo_name": todo_name, "message": msg})


class MarkTodoFailed(Tool):
    """Release a claim with a failure reason so another peer can retry.

    Use this when you've claimed a TODO but realize you can't make
    progress (e.g. an MCP tool keeps erroring). The claim is released
    so another peer can pick up the same TODO.
    """

    name = "mark_todo_failed"
    description = (
        "Release a TODO claim with a failure reason. Use this when you "
        "claimed a TODO but cannot complete it (an MCP tool keeps "
        "failing, the data is bad, etc.) and want another peer to try. "
        "Only the peer that claimed it can fail it."
    )
    inputs: dict = {
        "todo_name": {
            "type": "string",
            "description": "Name of the TODO you are giving up.",
        },
        "reason": {
            "type": "string",
            "description": "Short failure reason for the board.",
        },
    }
    output_type = "string"

    def __init__(self, context: NetworkedContext, agent_name: str = "", **kwargs):
        super().__init__(**kwargs)
        self._context = context
        self._agent_name = agent_name

    def forward(self, todo_name: str, reason: str) -> str:  # type: ignore[override]
        ok, msg = self._context.blackboard.fail_todo(
            todo_name, self._agent_name, reason
        )
        return json.dumps({"success": ok, "todo_name": todo_name, "message": msg})
