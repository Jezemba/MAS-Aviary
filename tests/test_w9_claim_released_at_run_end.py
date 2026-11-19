"""W9 (validation 2, N_IF it2): a peer whose run ended still held the 'evaluation' TODO; the others deadlocked on it.

Claim history (ws8 final_val2 val2_net_iter repeat_001 step log): agent_3 claimed 'evaluation' (72.5 min), marked it
failed at 96.4 min; agent_2's wait_for_board took it in the same second ("claimed": "evaluation"); agent_2 then ran
run_simulation, record_recommendation (113.4, 159.7) and final_answer (115.3, 123.6, 161.4) but never marked it done or
failed. Its run ended at 161.4 min; agent_1 and agent_3, still running in the same parallel cycle, called claim_todo /
mark_todo_done / mark_todo_failed on it 30 times ("claimed by 'agent_2', not by you") and wait_for_board until the
link stopped at max cycles (212.9 min). Not releasing a finished peer's claim is the framework's part (a holder that
never runs again, as B86/B89); agent_2 not closing its TODO is agent behaviour.

Fix: when a peer's run ends, the coordinator puts the TODOs it still holds back on the board (recorded). Stub agents
through Coordinator._execute_parallel_run, as test_b85; no model, no MCP server, no API.
"""
import threading
import time

from src.coordination.blackboard import TODO_STATUS_CLAIMED, TODO_STATUS_PENDING, Blackboard
from src.coordination.coordinator import Coordinator

TODOS = [("geometry", ""), ("aero", ""), ("mass", ""), ("propulsion", ""), ("mission", ""), ("simulation", ""),
         ("evaluation", "")]


class _Strategy:
    def __init__(self, bb):
        self.blackboard = bb


def _coordinator(agents, bb):
    from src.llm import batch_generation as bg

    bg.shutdown()
    c = object.__new__(Coordinator)
    c.agents, c._turn_counter, c.history, c.strategy = agents, 0, [], _Strategy(bb)
    return c


def _board():
    bb = Blackboard(claiming_mode="hard")
    bb.seed_todos(TODOS)
    for name in ("geometry", "aero", "mass", "propulsion", "mission", "simulation"):
        assert bb.claim_todo(name, "agent_2")[0] and bb.complete_todo(name, "agent_2", "done")[0]
    return bb


def _status(bb, name):
    return next(t for t in bb.read_todos() if t.name == name)


def test_w9_a_finished_peers_claim_goes_back_on_the_board_while_the_others_still_run():
    bb = _board()
    seen: dict = {}
    agent2_done = threading.Event()

    class Agent2:
        """Takes 'evaluation' (as its wait_for_board did at 96.4 min) and ends its run without closing it."""
        name, memory = "agent_2", type("M", (), {"steps": []})()

        def run(self, task):
            assert bb.claim_todo("evaluation", "agent_2")[0]
            time.sleep(0.05)
            agent2_done.set()
            return "DESIGN_STATE: ... (final_answer, evaluation not marked)"

    class Waiter:
        """agent_1 / agent_3: still running in the same cycle, waiting for 'evaluation'."""

        def __init__(self, name):
            self.name, self.memory = name, type("M", (), {"steps": []})()

        def run(self, task):
            assert agent2_done.wait(10)
            for _ in range(200):                       # wait_for_board polls the board
                t = _status(bb, "evaluation")
                if t.status == TODO_STATUS_PENDING:
                    break
                time.sleep(0.01)
            ok, msg = bb.claim_todo("evaluation", self.name)
            seen[self.name] = ok
            if ok:
                bb.complete_todo("evaluation", self.name, "MDO assessment")
            return msg

    agents = {"agent_1": Waiter("agent_1"), "agent_2": Agent2(), "agent_3": Waiter("agent_3")}
    c = _coordinator(agents, bb)
    action = type("A", (), {"metadata": {"peers": list(agents), "cycle": 1}, "input_context": "work"})()
    msgs = Coordinator._execute_parallel_run(c, action)
    assert len(msgs) == 3
    assert sum(seen.values()) == 1                     # exactly one waiting peer got it (atomic claim)
    assert _status(bb, "evaluation").status == "done"
    assert c.released_at_run_end[0]["peer"] == "agent_2" and c.released_at_run_end[0]["todos"] == ["evaluation"]
    assert any(e.key == "released_at_run_end_agent_2" for e in bb.read_all())


def test_w9_release_claims_of_only_touches_that_peers_unfinished_claims():
    bb = _board()
    bb.seed_todos([("extra", "")])
    assert bb.claim_todo("evaluation", "agent_2")[0] and bb.claim_todo("extra", "agent_1")[0]
    assert bb.release_claims_of("agent_2") == ["evaluation"]
    assert _status(bb, "evaluation").status == TODO_STATUS_PENDING and _status(bb, "evaluation").assigned_to is None
    assert _status(bb, "extra").status == TODO_STATUS_CLAIMED and _status(bb, "extra").assigned_to == "agent_1"
    assert _status(bb, "geometry").status == "done"                  # done work is never reopened
    assert bb.release_claims_of("agent_2") == []


def test_w9_the_release_is_posted_on_the_board():
    bb = _board()
    assert bb.claim_todo("evaluation", "agent_2")[0]
    c = _coordinator({}, bb)
    Coordinator._release_claims_at_run_end(c, "agent_2")
    entry = next(e for e in bb.read_all() if e.key == "released_at_run_end_agent_2")
    assert entry.author == "system" and entry.entry_type == "status" and "neither done nor failed" in entry.value
    assert c.released_at_run_end[0]["todos"] == ["evaluation"]


def test_w9_no_strategy_or_no_board_is_a_no_op():
    c = object.__new__(Coordinator)
    Coordinator._release_claims_at_run_end(c, "agent_2")              # no strategy attribute (test_b85 style)
    c.strategy = object()
    Coordinator._release_claims_at_run_end(c, "agent_2")              # sequential / orchestrated: no board
