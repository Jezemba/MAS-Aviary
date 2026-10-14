"""B205 (spawned peers' tools carry their own name; graph mode dispatches them) and B207
(networked + iterative_feedback actually retries each peer with feedback). No GPU, no servers."""
from __future__ import annotations

import json
from types import SimpleNamespace

from src.coordination.coordinator import Coordinator
from src.coordination.iterative_feedback_handler import IterativeFeedbackHandler
from src.coordination.strategies.networked import NetworkedStrategy
from src.coordination.strategy import CoordinationAction
from tests.test_networked_strategy import _make_config, dummy_model, worker_tools  # noqa: F401

# -- B205 ----------------------------------------------------------------------------------------------


def _owner(agent, tool_name):
    return getattr(agent.tools[tool_name], "_agent_name", None)


def test_spawned_peer_tools_are_bound_to_its_own_name(worker_tools):  # noqa: F811
    strategy = NetworkedStrategy()
    agents: dict = {}
    strategy.initialize(agents, _make_config(worker_tools, initial_agents=2))
    out = json.loads(agents["agent_1"].tools["spawn_peer"].forward(reason="nobody does propulsion"))
    assert out["success"], out
    new = agents[out["new_agent_name"]]
    for tool in ("write_blackboard", "claim_todo", "mark_todo_done", "spawn_peer"):
        assert _owner(new, tool) == out["new_agent_name"], tool
    # the existing peers' tools are untouched
    assert _owner(agents["agent_1"], "claim_todo") == "agent_1"


def test_graph_mode_dispatches_spawned_peers_but_not_role_aliases(worker_tools):  # noqa: F811
    strategy = NetworkedStrategy()
    agents: dict = {}
    strategy.initialize(agents, _make_config(worker_tools, initial_agents=2))
    agents["mission_architect"] = agents["agent_1"]  # graph-mode role alias (batch_runner)
    agents["agent_9"] = agents["agent_2"]            # stands in for a spawned peer
    strategy._graph = SimpleNamespace()               # graph branch taken ...
    seen = {}
    strategy._graph_driven_next_step = lambda h, s: (seen.setdefault("order", list(strategy._agent_order)),
                                                     CoordinationAction(action_type="terminate", agent_name=None,
                                                                        input_context=""))[1]
    strategy.next_step([], {"task": "t"})
    assert seen["order"] == ["agent_1", "agent_2", "agent_9"]


# -- B207 ----------------------------------------------------------------------------------------------


class _FlakyPeer:
    """Raises on its first run, succeeds afterwards; records every message it was given."""

    def __init__(self, name, fail_first=True):
        self.name, self.fail_first, self.calls = name, fail_first, []
        self.tools = {"some_tool": object()}
        self.memory = SimpleNamespace(steps=[])

    def run(self, task):
        self.calls.append(task)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("solver blew up")
        return f"{self.name} done"


class _OneCycle:
    def __init__(self, peers, board):
        self.peers, self.board, self.sent = peers, board, False

    def initialize(self, agents, config):
        pass

    def is_complete(self, history, state):
        return self.sent

    def next_step(self, history, state):
        if self.sent:
            return CoordinationAction(action_type="terminate", agent_name=None, input_context="")
        self.sent = True
        return CoordinationAction(
            action_type="parallel_run", agent_name=None, input_context="",
            metadata={"peers": list(self.peers), "cycle": 1,
                      "inputs": {p: f"{p} message, board v1" for p in self.peers},
                      "input_fn": lambda p: f"{p} message, board v{self.board['v']}"})


def _run(handler, peers):
    board = {"v": 1}
    agents = {p.name: p for p in peers}
    strategy = _OneCycle(list(agents), board)
    coord = Coordinator(agents=agents, strategy=strategy,
                        config={"termination": {"max_turns": 50, "max_consecutive_errors": 10}},
                        execution_handler=handler)
    board["v"] = 2  # the board moves on while the cycle runs
    return coord.run("t")


def test_networked_iterative_retries_a_failing_peer_with_feedback():
    flaky, steady = _FlakyPeer("agent_1"), _FlakyPeer("agent_2", fail_first=False)
    result = _run(IterativeFeedbackHandler({"max_retries": 3}), [flaky, steady])
    assert len(flaky.calls) == 2 and len(steady.calls) == 1
    assert flaky.calls[0] == "agent_1 message, board v1"
    # the retry is rebuilt from the current board, then the handler's own feedback follows
    assert flaky.calls[1].startswith("agent_1 message, board v2")
    assert "solver blew up" in flaky.calls[1]
    assert [m.agent_name for m in result.history].count("agent_1") == 2


def test_without_the_handler_a_failing_peer_runs_once():
    flaky = _FlakyPeer("agent_1")
    _run(None, [flaky])
    assert len(flaky.calls) == 1


def test_for_peer_is_a_fresh_as_given_copy():
    base = IterativeFeedbackHandler({"max_retries": 5, "stuck_threshold": 2})
    peer = base.for_peer()
    assert peer is not base and peer._worker_input == "as_given"
    assert peer._max_retries == 5 and peer._stuck_threshold == 2
    assert peer.attempt_histories == [] and peer._last_successful_output is None
