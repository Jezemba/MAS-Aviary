"""Tests for the Coordinator runner.

Uses a FinalAnswerModel so ToolCallingAgents created by the strategy
complete in one step with controlled output. No GPU needed.
"""

import json
import time

from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    Model,
)

from src.coordination.coordinator import Coordinator
from src.coordination.strategies.sequential import SequentialStrategy
from src.coordination.strategy import CoordinationResult
from src.tools.mock_tools import CalculatorTool, EchoTool, StateTool


class _FinalAnswerModel(Model):
    """Model that immediately returns final_answer with configurable output."""

    def __init__(self, answer: str = "dummy output"):
        super().__init__(model_id="final-answer-model")
        self._answer = answer
        self._call_count = 0

    def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kwargs):
        self._call_count += 1
        answer = f"{self._answer} [call {self._call_count}]"
        tc = ChatMessageToolCall(
            id=f"call_{self._call_count}",
            type="function",
            function=ChatMessageToolCallFunction(
                name="final_answer",
                arguments=json.dumps({"answer": answer}),
            ),
        )
        return ChatMessage(role="assistant", content="", tool_calls=[tc])


class _ErrorModel(Model):
    """Model that always raises an exception."""

    def __init__(self):
        super().__init__(model_id="error-model")

    def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kwargs):
        raise RuntimeError("model crashed")


def _worker_tools():
    return {
        "echo_tool": EchoTool(),
        "calculator_tool": CalculatorTool(),
        "state_tool": StateTool(),
    }


def _make_config(model, pipeline_template="linear", max_turns=20, **overrides):
    seq = {
        "decomposition_mode": "human",
        "pipeline_template": pipeline_template,
        "validate_interfaces": False,
        "stage_max_steps": 2,
    }
    seq.update(overrides)
    return {
        "sequential": seq,
        "termination": {
            "keyword": "TASK_COMPLETE",
            "max_turns": max_turns,
            "max_consecutive_errors": 3,
        },
        "_worker_tools": _worker_tools(),
        "_model": model,
        "stage_defaults": {
            "base_instructions": "You are one stage in a sequential pipeline.",
        },
    }


# ---- Basic coordination loop ---------------------------------------------------


class TestCoordinatorSequential:
    def test_runs_sequential_pipeline(self):
        model = _FinalAnswerModel("stage output")
        config = _make_config(model)
        agents = {}
        strategy = SequentialStrategy()
        coord = Coordinator(agents, strategy, config)
        result = coord.run("Calculate 2+2")

        assert isinstance(result, CoordinationResult)
        assert len(result.history) == 3
        assert result.history[0].agent_name == "planner"
        assert result.history[1].agent_name == "executor"
        assert result.history[2].agent_name == "reviewer"

    def test_terminates_on_keyword(self):
        model = _FinalAnswerModel("TASK_COMPLETE")
        config = _make_config(model)
        agents = {}
        strategy = SequentialStrategy()
        coord = Coordinator(agents, strategy, config)
        result = coord.run("test")

        # First agent (planner) returns TASK_COMPLETE, termination checker
        # stops the loop
        assert len(result.history) == 1
        assert result.history[0].agent_name == "planner"
        assert "TASK_COMPLETE" in result.history[0].content

    def test_terminates_on_max_turns(self):
        model = _FinalAnswerModel("still working")
        config = _make_config(model, pipeline_template="v_model", max_turns=3)
        agents = {}
        strategy = SequentialStrategy()
        coord = Coordinator(agents, strategy, config)
        result = coord.run("test")

        # v_model has 5 stages, max_turns=3 stops after 3
        assert len(result.history) == 3


# ---- Error handling -----------------------------------------------------------


class TestCoordinatorErrors:
    def test_agent_exception_logged_in_message(self):
        model = _ErrorModel()
        config = _make_config(model, max_turns=5)
        config["termination"]["max_consecutive_errors"] = 1
        agents = {}
        strategy = SequentialStrategy()
        coord = Coordinator(agents, strategy, config)
        result = coord.run("test")

        assert len(result.history) >= 1
        err_msg = result.history[0]
        assert err_msg.error is not None
        assert "crashed" in err_msg.error

    def test_consecutive_errors_terminate(self):
        model = _ErrorModel()
        config = _make_config(model, pipeline_template="v_model", max_turns=20)
        config["termination"]["max_consecutive_errors"] = 3
        agents = {}
        strategy = SequentialStrategy()
        coord = Coordinator(agents, strategy, config)
        result = coord.run("test")

        # Should stop after 3 consecutive errors
        assert len(result.history) == 3
        assert all(m.error is not None for m in result.history)


# ---- Message structure ---------------------------------------------------------


class TestCoordinatorMessages:
    def test_turn_numbers_sequential(self):
        model = _FinalAnswerModel("out")
        config = _make_config(model)
        agents = {}
        strategy = SequentialStrategy()
        coord = Coordinator(agents, strategy, config)
        result = coord.run("test")

        assert result.history[0].turn_number == 1
        assert result.history[1].turn_number == 2

    def test_timestamps_are_recent(self):
        model = _FinalAnswerModel("out")
        config = _make_config(model)
        agents = {}
        strategy = SequentialStrategy()
        coord = Coordinator(agents, strategy, config)
        before = time.time()
        result = coord.run("test")
        after = time.time()

        assert before <= result.history[0].timestamp <= after

    def test_duration_is_positive(self):
        model = _FinalAnswerModel("out")
        config = _make_config(model)
        agents = {}
        strategy = SequentialStrategy()
        coord = Coordinator(agents, strategy, config)
        result = coord.run("test")

        assert result.history[0].duration_seconds >= 0


# ---- parallel_run action (concurrent_blackboard mode) -----------------------


class _RecordingAgent:
    """Minimal agent stub for testing the Coordinator's parallel_run
    path. Records every .run() call; lets the test assert which peers
    were invoked and that they ran concurrently."""

    def __init__(self, name: str, answer: str = "done", delay_seconds: float = 0.0):
        self.name = name
        self._answer = answer
        self._delay = delay_seconds
        self.calls: list[str] = []
        self.tools: dict = {}
        # smolagents-shaped memory so _extract_tool_calls returns [].
        from types import SimpleNamespace

        self.memory = SimpleNamespace(steps=[])

    def run(self, task: str) -> str:
        if self._delay:
            time.sleep(self._delay)
        self.calls.append(task)
        return f"{self.name}: {self._answer}"


class _SingleParallelRunStrategy:
    """Strategy stub that returns one parallel_run action then terminates."""

    def __init__(self, peer_names: list[str]):
        self._peer_names = peer_names
        self._dispatched = False

    def initialize(self, agents, config):
        pass

    def is_complete(self, history, current_state):
        return self._dispatched

    def next_step(self, history, current_state):
        from src.coordination.strategy import CoordinationAction

        if self._dispatched:
            return CoordinationAction(action_type="terminate", agent_name=None, input_context="")
        self._dispatched = True
        return CoordinationAction(
            action_type="parallel_run",
            agent_name=None,
            input_context=current_state.get("task", ""),
            metadata={"peers": list(self._peer_names), "cycle": 1},
        )


class TestCoordinatorParallelRun:
    """Coordinator route for action_type='parallel_run' — required by
    NetworkedStrategy.selection_mode='concurrent_blackboard'."""

    def test_launches_all_named_peers(self):
        agents = {
            "agent_1": _RecordingAgent("agent_1"),
            "agent_2": _RecordingAgent("agent_2"),
            "agent_3": _RecordingAgent("agent_3"),
        }
        strategy = _SingleParallelRunStrategy(list(agents.keys()))
        coord = Coordinator(
            agents=agents,
            strategy=strategy,
            config={"termination": {"max_turns": 20, "max_consecutive_errors": 3}},
        )
        result = coord.run("the task")

        # Every peer ran exactly once with the task input.
        for name, agent in agents.items():
            assert agent.calls == ["the task"], (
                f"peer {name} ran {agent.calls!r}, expected exactly one call"
            )

        # The history captured one AgentMessage per peer.
        peer_msgs = {m.agent_name: m for m in result.history if m.agent_name in agents}
        assert set(peer_msgs.keys()) == set(agents.keys())
        for name, msg in peer_msgs.items():
            assert msg.content == f"{name}: done"

    def test_runs_concurrently_not_serially(self):
        """If each peer sleeps for X seconds and we have N peers running
        in parallel, wall-clock should be ~X, not ~N*X."""
        delay = 0.3
        agents = {
            "agent_1": _RecordingAgent("agent_1", delay_seconds=delay),
            "agent_2": _RecordingAgent("agent_2", delay_seconds=delay),
            "agent_3": _RecordingAgent("agent_3", delay_seconds=delay),
        }
        strategy = _SingleParallelRunStrategy(list(agents.keys()))
        coord = Coordinator(
            agents=agents,
            strategy=strategy,
            config={"termination": {"max_turns": 20, "max_consecutive_errors": 3}},
        )
        start = time.monotonic()
        coord.run("t")
        wall = time.monotonic() - start
        # Should be ~delay (parallel) plus overhead, not 3*delay (serial).
        # Generous bound: < 2*delay confirms real parallelism.
        assert wall < 2 * delay, (
            f"Expected parallel execution to take ~{delay}s, got {wall:.2f}s "
            f"— would be ~{3*delay}s if serial."
        )

    def test_missing_peer_yields_error_message(self):
        """A peer named in action.metadata['peers'] but missing from
        agents dict should produce an error AgentMessage, not crash."""
        agents = {
            "agent_1": _RecordingAgent("agent_1"),
        }
        strategy = _SingleParallelRunStrategy(["agent_1", "agent_2_missing"])
        coord = Coordinator(
            agents=agents,
            strategy=strategy,
            config={"termination": {"max_turns": 20, "max_consecutive_errors": 3}},
        )
        result = coord.run("t")
        names = [m.agent_name for m in result.history]
        assert "agent_1" in names
        assert "agent_2_missing" in names
        missing_msg = next(m for m in result.history if m.agent_name == "agent_2_missing")
        assert missing_msg.error
        assert "not found" in missing_msg.error

    def test_peer_exception_caught_not_propagated(self):
        """If one peer raises during agent.run(), the Coordinator should
        record an error AgentMessage for it but still return results from
        the other peers."""

        class _CrashingAgent(_RecordingAgent):
            def run(self, task: str) -> str:
                raise RuntimeError("simulated crash")

        agents = {
            "agent_1": _RecordingAgent("agent_1"),
            "agent_2": _CrashingAgent("agent_2"),
            "agent_3": _RecordingAgent("agent_3"),
        }
        strategy = _SingleParallelRunStrategy(list(agents.keys()))
        coord = Coordinator(
            agents=agents,
            strategy=strategy,
            config={"termination": {"max_turns": 20, "max_consecutive_errors": 3}},
        )
        result = coord.run("t")
        by_name = {m.agent_name: m for m in result.history}
        assert by_name["agent_1"].content == "agent_1: done"
        assert by_name["agent_3"].content == "agent_3: done"
        assert by_name["agent_2"].error and "simulated crash" in by_name["agent_2"].error
