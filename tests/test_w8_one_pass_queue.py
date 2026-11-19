"""W8 (validation 2, O_IF it2): the orchestrated iterative-feedback link ran a second pass on stale tasks.

What the record shows (tests/fixtures/w8_o_if_it2_records.json, from the O_IF it2 step log): the orchestrator's first
turn queued all 7 roles and ended at max steps ("Reached max steps.", no DELEGATION_COMPLETE); the strategy invoked it
again with the same opening message and it queued all 7 a second time (DELEGATION_COMPLETE). setup_only runs the queue
once, in order, so the link ran 14 assignments: pass 1 with turn 1's tasks, then -- after the integrator's CONTINUE
"Wing1 aspect_ratio 9.9999 -> 10.5" -- pass 2 with turn 2's tasks, written at 5 min, before any work, from the
iteration-1 recommendation. The handler did not re-send an old task; the queue was doubled.

Fix: in a setup_only creation phase a later orchestrator turn that assigns an agent already queued by an earlier turn
replaces that agent's queued task (one pass, one task per agent, the latest), and says so in the reply. Graph-routed and
per-stage orchestrations, and assignments made during execution, are unchanged. DummyModel, no GPU, no MCP, no API.
"""
import json
from pathlib import Path

from src.coordination.history import AgentMessage
from src.coordination.strategies.orchestrated import DELEGATION_COMPLETE
from src.tools.orchestrator_tools import AssignTask
from tests.test_orchestrated_strategy import (  # noqa: F401 (fixtures)
    base_config, dummy_model, orchestrator_agent, strategy_with_tools, worker_tools)

REC = json.loads((Path(__file__).parent / "fixtures" / "w8_o_if_it2_records.json").read_text())
TURN1, TURN2 = (t["assigned"] for t in REC["orchestrator_turns"])
ROLES = [a["agent_name"] for a in TURN1]


def _orch(content):
    return AgentMessage(agent_name="orchestrator", content=content, turn_number=1, timestamp=1.0)


def _run_creation(strategy):
    ctx = strategy.context
    for name in ROLES:
        ctx.created_agents.append(name)
        ctx.agents[name] = f"worker_{name}"
    tool = AssignTask(ctx)
    history = []
    assert strategy.next_step(history, {"task": "T"}).agent_name == "orchestrator"       # turn 1
    replies = [json.loads(tool.forward(a["agent_name"], a["task"])) for a in TURN1]
    history.append(_orch(REC["orchestrator_turns"][0]["ended"]))                       # "Reached max steps."
    assert strategy.next_step(history, {"task": "T"}).agent_name == "orchestrator"       # turn 2, as recorded
    replies += [json.loads(tool.forward(a["agent_name"], a["task"])) for a in TURN2]
    history.append(_orch(DELEGATION_COMPLETE))
    return history, replies


def test_w8_record_is_two_full_queues():
    assert REC["orchestrator_turns"][0]["ended"] == "Reached max steps."
    assert [a["agent_name"] for a in TURN2] == ROLES and len(ROLES) == 7
    runs = [r["agent"] for r in REC["worker_runs"]]
    assert runs.count("geometry_engineer") == 2 and runs.count("mdo_integrator") == 2       # two passes ran
    second_geo = [r for r in REC["worker_runs"] if r["agent"] == "geometry_engineer"][1]
    assert second_geo["input_starts"] == TURN2[0]["task"][:160]                            # turn 2's stale task


def test_w8_the_second_turn_replaces_the_queue_and_the_link_runs_one_pass(strategy_with_tools):
    s = strategy_with_tools
    history, replies = _run_creation(s)
    assert [a["agent_name"] for a in s.context.assignments] == ROLES                       # 7, not 14
    assert [a["task"] for a in s.context.assignments] == [a["task"] for a in TURN2]        # the latest tasks
    assert all(r["replaced"] == a["task"] for r, a in zip(replies[7:], TURN1))
    ran = []
    action = s.next_step(history, {"task": "T"})
    while action.action_type == "invoke_agent":
        ran.append(action.agent_name)
        history.append(AgentMessage(agent_name=action.agent_name, content="DESIGN_STATE: ok", turn_number=len(history),
                                    timestamp=1.0))
        action = s.next_step(history, {"task": "T"})
    assert ran == ROLES                                                                     # once each
    assert action.action_type == "terminate" and action.metadata["reason"] == "all_assignments_executed"


def test_w8_within_one_turn_and_after_creation_assignments_still_append(strategy_with_tools):
    s = strategy_with_tools
    ctx = s.context
    ctx.created_agents.append("geometry_engineer")
    ctx.agents["geometry_engineer"] = "w"
    s.next_step([], {"task": "T"})
    tool = AssignTask(ctx)
    tool.forward("geometry_engineer", "first")
    tool.forward("geometry_engineer", "second")                    # same turn: the orchestrator's own order
    assert [a["task"] for a in ctx.assignments] == ["first", "second"]
    s._transition_to_execution([_orch(DELEGATION_COMPLETE)], {"task": "T"})
    ctx.turn_counter += 5
    tool.forward("geometry_engineer", "in-link")                   # during execution: queued as before
    assert [a["task"] for a in ctx.assignments][-1] == "in-link" and len(ctx.assignments) == 3


def test_w8_not_for_per_stage_or_graph_routed(strategy_with_tools):
    s = strategy_with_tools
    s._lifecycle_mode = "per_stage"
    s.next_step([], {"task": "T"})
    assert s.context.replace_earlier_turn_assignments is False
    s._lifecycle_mode, s._graph_roles = "setup_only", ["geometry_engineer"]
    s._orchestrator_turns_used = 0
    s.next_step([], {"task": "T"})
    assert s.context.replace_earlier_turn_assignments is False
