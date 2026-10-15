"""Graph-routed v2 for networked (D8): the board starts at SIMPLE and the peers change it.
No GPU, no servers: pre-init is off and the peers' tool calls are made directly."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.coordination import graph_router as gr
from src.coordination.blackboard import Blackboard
from src.coordination.board_graph import TODO_OF_NODE, BoardGraph
from src.coordination.design_state import DesignState
from src.tools import recommendation_tools as rt
from src.tools.agent_context import agent_scope
from src.tools.data_plane import init_data_plane


@pytest.fixture(autouse=True)
def _state():
    init_data_plane(DesignState(), {})
    yield
    init_data_plane(DesignState(), {})


def _graph():
    bb = Blackboard(claiming_mode="soft")
    bg = BoardGraph(bb, preinit=False)
    bg.start(None)
    return bg, bb


def _finish(bb, name, agent="agent_1"):
    ok, msg = bb.claim_todo(name, agent)
    assert ok, msg
    bb.complete_todo(name, agent, f"{name} DESIGN_STATE")


def _names(bb, status=None):
    return {t.name for t in bb.read_todos() if status is None or t.status == status}


def test_starts_at_simple_with_the_mission_chain_only():
    bg, bb = _graph()
    assert bg.level == gr.SIMPLE
    assert _names(bb) == {"MISSION_CONFIG", "SIMULATION_RUN", "RESULTS_REVIEW"}
    assert [t.name for t in bb.read_available_todos()] == ["MISSION_CONFIG"]
    assert "SIMPLE" in bb.get("graph_level").value


def test_a_peer_request_adds_the_level_and_reopens_downstream():
    bg, bb = _graph()
    _finish(bb, "MISSION_CONFIG")
    with agent_scope("agent_2"):
        rt.RequestComplexity().forward("moderate", "the drag looks wrong")
    assert bg.step()
    assert bg.level == gr.MODERATE
    assert {"AERO_ANALYSIS", "MASS_ESTIMATION", "PROPULSION_SIZING"} <= _names(bb)
    assert "GEOMETRY_SETUP" not in _names(bb)
    # mission ran on the old results, so it opens again, now after aero and mass
    mission = next(t for t in bb.read_todos() if t.name == "MISSION_CONFIG")
    assert mission.status == "pending" and set(mission.depends_on) == {"AERO_ANALYSIS", "MASS_ESTIMATION"}
    assert {t.name for t in bb.read_available_todos()} == {"AERO_ANALYSIS", "MASS_ESTIMATION"}
    assert "agent_2 reclassified" in bb.get("graph_level").value


def test_the_integrator_recommendation_reopens_its_producer_inside_the_link():
    bg, bb = _graph()
    for name in ("MISSION_CONFIG", "SIMULATION_RUN", "RESULTS_REVIEW"):
        _finish(bb, name)
    with agent_scope("mdo_integrator"):
        rt.RecordRecommendation().forward("CONTINUE", "geometry_engineer", "Wing1 aspect_ratio 9.4 -> 10.5")
    assert bg.step()
    assert bg.level == gr.COMPLEX                       # the change needs the geometry
    assert [t.name for t in bb.read_available_todos()] == ["GEOMETRY_SETUP"]
    assert _names(bb, "done") == set()                  # everything after the geometry runs again
    assert "aspect_ratio" in bb.get("graph_route").value


def test_complete_verdict_stops_the_link():
    bg, bb = _graph()
    for name in ("MISSION_CONFIG", "SIMULATION_RUN", "RESULTS_REVIEW"):
        _finish(bb, name)
    with agent_scope("mdo_integrator"):
        rt.RecordRecommendation().forward("COMPLETE")
    assert not bg.step()
    assert "COMPLETE" in bg.stop_reason


def test_all_done_without_a_recommendation_stops():
    bg, bb = _graph()
    for name in ("MISSION_CONFIG", "SIMULATION_RUN", "RESULTS_REVIEW"):
        _finish(bb, name)
    assert not bg.step()
    assert "every TODO is done" in bg.stop_reason


def test_budget_counts_role_runs_and_the_board_refuses_claims_once_spent():
    bg, bb = _graph()
    for _ in range(gr.BUDGET[gr.SIMPLE]):
        _finish(bb, "MISSION_CONFIG")
        bb.reopen_todos(["MISSION_CONFIG"])
    ok, msg = bb.claim_todo("MISSION_CONFIG", "agent_2")      # mid-cycle: refused by the board itself
    assert not ok and "budget of 8 role runs at simple is spent" in msg
    assert not bg.step() and "budget" in bg.stop_reason
    assert bg.role_runs == gr.BUDGET[gr.SIMPLE]


def test_a_higher_level_raises_the_claim_limit():
    bg, bb = _graph()
    _finish(bb, "MISSION_CONFIG")
    with agent_scope("agent_2"):
        rt.RequestComplexity().forward("complex", "the wing has to change")
    assert bg.step() and bg.level == gr.COMPLEX
    assert bb._claim_limit == bg.base_completions + gr.BUDGET[gr.COMPLEX]


def test_board_names_map_to_roles_for_the_gates():
    assert rt._TODO_ROLE["RESULTS_REVIEW"] == "mdo_integrator"
    assert set(TODO_OF_NODE.values()) <= set(rt._TODO_ROLE)


def test_strategy_dispatches_the_board_graph(monkeypatch):
    from src.coordination.strategies.networked import NetworkedStrategy
    from tests.test_networked_strategy import _make_config
    from src.tools.mock_tools import EchoTool

    config = _make_config({"echo_tool": EchoTool()}, initial_agents=3, selection_mode="concurrent_blackboard",
                          graph_router="board", graph_preinit=False)
    config["_graph_def"] = SimpleNamespace(initial_state="MISSION_CONFIG", states={}, terminal_states=[])
    strategy = NetworkedStrategy()
    agents: dict = {}
    strategy.initialize(agents, config)
    assert "request_complexity" in agents["agent_1"].tools
    action = strategy.next_step([], {"task": "t"})
    assert action.action_type == "parallel_run"
    assert action.metadata["peers"] == ["agent_1"]            # one TODO available -> one peer
    assert action.metadata["graph_level"] == gr.SIMPLE


def test_the_routing_notices_are_in_every_peers_message():
    from src.coordination import prompt_assembly

    bg, bb = _graph()
    text = prompt_assembly.board_block(bb, show_blocking=True)
    assert "complexity level SIMPLE" in text and "request_complexity" in text
    assert text.index("TODO BOARD") < text.index("GRAPH ROUTING")
