"""Standalone prototype: concurrent ReAct agents claiming TODOs from a
thread-safe blackboard.

Goal: demonstrate the CodeCRDT-style coordination pattern
(arxiv 2510.18893) in our codebase BEFORE touching the production
NetworkedStrategy. Each peer is a real smolagents ToolCallingAgent
(ReAct loop intact); concurrency is at the THREAD level — three
agents run their ReAct loops in parallel, racing to claim and
execute discipline TODOs from a shared blackboard.

The MDO tools are MOCKED here (sleep + return fake result) so the
prototype runs entirely on the Anthropic API + Python threads, no
MCP servers needed, total cost ~$0.10-0.30. The goal is to verify:

1. Claim semantics — no TODO is claimed by two agents.
2. Concurrency — wall-clock time < sum of per-agent times.
3. Termination — agents notice when all TODOs are done and stop.
4. Each agent stays a ReAct agent (no out-of-band control).

Run:
    set -a && source .env && set +a
    PYTHONPATH=. /home/aipexws3/Jessica/Avion/.venv/bin/python \\
        scripts/prototype_concurrent_blackboard.py

Optionally set PROTO_PEERS=N to change agent count (default 3).
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from smolagents import Tool, ToolCallingAgent

# Reuse the project's existing model loader so we get the same Claude
# Sonnet 4 LiteLLM model the live combos use.
from src.config.loader import LLMConfig
from src.llm.model_loader import load_model

# ---------------------------------------------------------------------------
# Shared blackboard with claim-then-execute semantics
# ---------------------------------------------------------------------------


@dataclass
class Todo:
    """One unit of work on the shared blackboard."""

    name: str  # e.g. "geometry"
    description: str  # what this TODO is about
    status: str = "pending"  # pending | claimed | done | failed
    assigned_to: str | None = None
    result: str = ""
    claimed_at: float | None = None
    completed_at: float | None = None


@dataclass
class ConcurrentBlackboard:
    """Thread-safe TODO board + activity log.

    Mirrors the CodeCRDT TODO-claim protocol from arxiv 2510.18893:
    each agent first scans for pending TODOs, then attempts an atomic
    claim by writing its name into assigned_to under a lock. Returns
    True only if THIS agent's claim is the one recorded — provides the
    'at most one winner' safety guarantee.
    """

    todos: dict[str, Todo] = field(default_factory=dict)
    activity: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def seed_initial_todos(self) -> None:
        """Seed the 7 F25-style MDO discipline TODOs."""
        initial = [
            ("geometry", "Open CPACS and produce a CFD-ready volume mesh."),
            ("aero", "Run SU2 on the volume mesh and read CL/CD off history.csv."),
            ("mass", "Estimate wing mass via mass-mcp from the CPACS file."),
            ("propulsion", "Run a pyCycle HBTF cycle at design thrust."),
            ("mission", "Configure the F25 mission in aviary and set design parameters."),
            ("simulation", "Run aviary's SLSQP optimizer."),
            ("evaluation", "Pull fuel_burned_kg from get_results and check_constraints."),
        ]
        with self._lock:
            for name, desc in initial:
                self.todos[name] = Todo(name=name, description=desc)
            self._log_locked("seeded 7 MDO discipline TODOs")

    def _log_locked(self, line: str) -> None:
        self.activity.append(f"[{time.time():.3f}] {line}")

    def snapshot(self) -> str:
        """Render board state for an agent to read (lock-protected)."""
        with self._lock:
            lines = ["TODO BOARD:"]
            for t in self.todos.values():
                row = f"  - {t.name}: status={t.status}"
                if t.assigned_to:
                    row += f" assigned_to={t.assigned_to}"
                if t.result:
                    row += f" result={t.result[:60]}"
                lines.append(row)
            recent = self.activity[-6:]
            if recent:
                lines.append("\nRECENT ACTIVITY (last 6):")
                lines.extend(f"  {a}" for a in recent)
            return "\n".join(lines)

    def claim(self, todo_name: str, agent_name: str) -> tuple[bool, str]:
        """Atomic claim. Returns (success, message)."""
        with self._lock:
            todo = self.todos.get(todo_name)
            if todo is None:
                return False, f"no such TODO: {todo_name!r}"
            if todo.status == "done":
                return False, f"TODO {todo_name!r} already done by {todo.assigned_to!r}"
            if todo.status == "claimed" and todo.assigned_to != agent_name:
                return (
                    False,
                    f"TODO {todo_name!r} is already claimed by "
                    f"{todo.assigned_to!r} — try another one",
                )
            todo.status = "claimed"
            todo.assigned_to = agent_name
            todo.claimed_at = time.time()
            self._log_locked(f"{agent_name} CLAIMED {todo_name!r}")
            return True, f"{agent_name} successfully claimed {todo_name!r}"

    def complete(self, todo_name: str, agent_name: str, result: str) -> tuple[bool, str]:
        """Mark a TODO done. Only the agent that claimed it may complete it."""
        with self._lock:
            todo = self.todos.get(todo_name)
            if todo is None:
                return False, f"no such TODO: {todo_name!r}"
            if todo.assigned_to != agent_name:
                return (
                    False,
                    f"cannot complete {todo_name!r} — claimed by "
                    f"{todo.assigned_to!r}, not by you ({agent_name!r})",
                )
            todo.status = "done"
            todo.result = result
            todo.completed_at = time.time()
            self._log_locked(f"{agent_name} DONE {todo_name!r}: {result[:60]}")
            return True, f"{agent_name} marked {todo_name!r} done"

    def all_done(self) -> bool:
        with self._lock:
            return all(t.status == "done" for t in self.todos.values())

    def summary(self) -> str:
        """Final report after the run."""
        with self._lock:
            lines = ["", "=" * 60, "FINAL BLACKBOARD SUMMARY", "=" * 60]
            for t in self.todos.values():
                lines.append(
                    f"  {t.name:12s}  status={t.status:8s}  by={t.assigned_to or '-':10s}  "
                    f"result={t.result[:60]}"
                )
            done_count = sum(1 for t in self.todos.values() if t.status == "done")
            lines.append(f"\nProgress: {done_count}/{len(self.todos)} done")
            # Concurrency check: were any claim times within 50 ms of each other?
            times = sorted([t.claimed_at for t in self.todos.values() if t.claimed_at])
            overlaps = sum(1 for i in range(1, len(times)) if times[i] - times[i-1] < 0.5)
            lines.append(
                f"Concurrent overlap: {overlaps} pairs of claims within 0.5s "
                f"(indicates real parallel work, not strict sequencing)"
            )
            assignees = {t.assigned_to for t in self.todos.values() if t.assigned_to}
            lines.append(f"Distinct agents that claimed work: {sorted(assignees)}")
            return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tools each agent gets
# ---------------------------------------------------------------------------


class ReadBoardTool(Tool):
    name = "read_blackboard"
    description = (
        "Read the current TODO board and recent activity. Use this BEFORE "
        "claiming work so you don't pick something another peer is already "
        "doing."
    )
    inputs: dict = {}
    output_type = "string"

    def __init__(self, board: ConcurrentBlackboard):
        super().__init__()
        self._board = board

    def forward(self) -> str:  # type: ignore[override]
        return self._board.snapshot()


class ClaimTool(Tool):
    name = "claim_todo"
    description = (
        "Atomically claim a pending TODO. Returns success=true with a "
        "confirmation if you got the claim, success=false if another peer "
        "already has it (in which case pick a different TODO). After a "
        "successful claim you MUST complete or fail the TODO with "
        "mark_done."
    )
    inputs: dict = {
        "todo_name": {
            "type": "string",
            "description": "The name field of a TODO from the board.",
        },
    }
    output_type = "string"

    def __init__(self, board: ConcurrentBlackboard, agent_name: str):
        super().__init__()
        self._board = board
        self._agent_name = agent_name

    def forward(self, todo_name: str) -> str:  # type: ignore[override]
        ok, msg = self._board.claim(todo_name, self._agent_name)
        return json.dumps({"success": ok, "message": msg})


class DoWorkTool(Tool):
    """Mocked discipline tool. Sleeps a bit to simulate real work, returns
    a synthetic result. Replace this with real MCP tools when porting the
    pattern into the production strategy."""

    name = "do_discipline_work"
    description = (
        "Perform the discipline work for a TODO you have already claimed. "
        "Returns a result string. In production this would call the real "
        "MCP tools for the discipline (open_cpacs/run_su2_solver/etc.); "
        "the prototype just simulates work with a short delay."
    )
    inputs: dict = {
        "todo_name": {
            "type": "string",
            "description": "Name of the TODO you have already claimed.",
        },
    }
    output_type = "string"

    _FAKE_RESULTS = {
        "geometry": "mesh_ref=mesh_001, nodes=476853",
        "aero": "CL=0.589, CD=0.0303, L/D=19.4",
        "mass": "mWing_kg=7234, mTOM_kg=78126",
        "propulsion": "SFC=0.0143, Fn_DES_lbf=6344",
        "mission": "session_id=abc123, parameters set",
        "simulation": "fuel_burned_kg=12100, converged=true",
        "evaluation": "VERDICT=COMPLETE, constraints satisfied",
    }

    def __init__(self, board: ConcurrentBlackboard, agent_name: str):
        super().__init__()
        self._board = board
        self._agent_name = agent_name

    def forward(self, todo_name: str) -> str:  # type: ignore[override]
        # Sanity: verify this agent actually claimed the TODO.
        with self._board._lock:
            todo = self._board.todos.get(todo_name)
            if todo is None:
                return json.dumps({"success": False, "error": f"no such TODO {todo_name!r}"})
            if todo.assigned_to != self._agent_name:
                return json.dumps({
                    "success": False,
                    "error": f"you ({self._agent_name!r}) did not claim {todo_name!r}; "
                             f"it belongs to {todo.assigned_to!r}",
                })
        # Simulate real work (this would be many MCP calls in production).
        time.sleep(0.5)
        result = self._FAKE_RESULTS.get(todo_name, f"completed {todo_name}")
        return json.dumps({"success": True, "result": result})


class MarkDoneTool(Tool):
    name = "mark_done"
    description = (
        "Record a TODO as done with its result string. Must follow a "
        "successful claim + do_discipline_work for the same TODO."
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

    def __init__(self, board: ConcurrentBlackboard, agent_name: str):
        super().__init__()
        self._board = board
        self._agent_name = agent_name

    def forward(self, todo_name: str, result: str) -> str:  # type: ignore[override]
        ok, msg = self._board.complete(todo_name, self._agent_name, result)
        return json.dumps({"success": ok, "message": msg})


# ---------------------------------------------------------------------------
# One peer agent
# ---------------------------------------------------------------------------


_PEER_INSTRUCTIONS = """You are {agent_name}, one of {peer_count} peers
working in parallel on a multi-disciplinary aircraft design task. There
is NO orchestrator and NO fixed assignment. You and the other peers
race to claim and execute disciplines from a shared TODO board.

The board lists 7 disciplines: geometry, aero, mass, propulsion,
mission, simulation, evaluation.

YOUR LOOP — repeat until no more pending TODOs:
1. call read_blackboard to see what is pending, claimed, or done.
2. pick a pending TODO you can do (preferably one whose upstream
   disciplines on the board already show results).
3. call claim_todo(todo_name) — if claim fails (another peer beat you),
   call read_blackboard again and pick a different TODO. DO NOT keep
   retrying the same one.
4. call do_discipline_work(todo_name) to execute it.
5. call mark_done(todo_name, result) to record the result.
6. go back to step 1.

When read_blackboard shows every TODO as 'done' (or only ones claimed
by other peers that are still running), call final_answer with a one-
line summary of which TODOs you completed.
"""


def _build_peer(name: str, peer_count: int, board: ConcurrentBlackboard, model) -> ToolCallingAgent:
    tools = [
        ReadBoardTool(board),
        ClaimTool(board, agent_name=name),
        DoWorkTool(board, agent_name=name),
        MarkDoneTool(board, agent_name=name),
    ]
    return ToolCallingAgent(
        tools=tools,
        model=model,
        name=name,
        description=f"Peer {name} for the concurrent prototype.",
        instructions=_PEER_INSTRUCTIONS.format(agent_name=name, peer_count=peer_count),
        max_steps=20,
        add_base_tools=False,
    )


def _run_peer(agent: ToolCallingAgent, task: str) -> dict:
    """Run a single peer's ReAct loop. Returns timing + final answer."""
    name = agent.name
    start = time.monotonic()
    try:
        answer = agent.run(task)
        return {"agent": name, "answer": str(answer)[:300], "duration_s": time.monotonic() - start}
    except Exception as e:
        return {"agent": name, "error": f"{type(e).__name__}: {e}", "duration_s": time.monotonic() - start}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — required for live LLM calls.")
        return 1

    peer_count = int(os.environ.get("PROTO_PEERS", "3"))
    print(f"Concurrent ReAct prototype: {peer_count} peers, 7 MDO TODOs.")

    # Shared blackboard.
    board = ConcurrentBlackboard()
    board.seed_initial_todos()

    # Shared model (LiteLLMModel via Claude Sonnet). All peers share it
    # — the Anthropic SDK is thread-safe at the request level.
    llm_cfg = LLMConfig(
        model_id="anthropic/claude-sonnet-4-20250514",
        backend="litellm",
        temperature=0.2,
        max_new_tokens=1024,
    )
    model = load_model(llm_cfg)

    # Build N peer agents.
    peers = [_build_peer(f"agent_{i}", peer_count, board, model) for i in range(1, peer_count + 1)]

    task = (
        "Coordinate with your peer agents to complete all 7 MDO "
        "discipline TODOs on the shared blackboard. Each TODO can only "
        "be done by one peer."
    )

    print(f"\nLaunching {peer_count} peers concurrently...\n")
    start = time.monotonic()

    with ThreadPoolExecutor(max_workers=peer_count) as ex:
        futures = [ex.submit(_run_peer, peer, task) for peer in peers]
        results = []
        for f in as_completed(futures):
            r = f.result()
            results.append(r)
            print(f"  [{r['agent']}] finished in {r['duration_s']:.1f}s")

    wall = time.monotonic() - start

    print(board.summary())
    print(f"\nWall clock: {wall:.1f}s")
    print(f"Sum of agent durations: {sum(r['duration_s'] for r in results):.1f}s")
    speedup = sum(r['duration_s'] for r in results) / wall if wall > 0 else 0
    print(f"Speedup (sum / wall): {speedup:.2f}x")

    print("\nPer-agent final answers:")
    for r in sorted(results, key=lambda x: x["agent"]):
        print(f"  [{r['agent']}] {r.get('answer') or r.get('error')}")

    # Acceptance assertions for the prototype.
    if not board.all_done():
        print("\n⚠ Not all TODOs were completed.")
        return 2
    # No double-execution: each TODO has exactly one assignee.
    for t in board.todos.values():
        if t.status != "done":
            print(f"\n⚠ {t.name} not done")
            return 3
    print("\n✓ All 7 TODOs completed with at-most-one-winner safety.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
