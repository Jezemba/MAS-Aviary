"""Phase L wiring test: mdo_f25_networked_graph_routed (cheap).

The fifth and final Job 3 combination after sequential_staged_pipeline
(step 1), orchestrated_staged_pipeline (step 2), sequential_graph_routed
(step 3, wandb 81w57h3g), and orchestrated_graph_routed (step 4, wandb
r4svbo5u).

Unlike the other graph_routed combos, the networked strategy drives
the state machine ITSELF (networked.py _graph_driven_next_step, one
state per turn, bypass_handler=True) rather than delegating to the
graph_routed handler. It builds each worker's context from the
per-state agent_prompt ONLY (no full-task text), so there's no
task-pollution / SESSION_ID leak. The graph YAML is shared with
steps 3-4.

What this covers (mostly shape — no LLM required):
  1. The combo `mdo_f25_networked_graph_routed` is registered with
     org_structure="networked" + handler="graph_routed" +
     predefined_graph="mdo_f25" + workflow_phases disabled.
  2. The shared config/mdo_f25_graph.yaml still loads (regression
     guard for the structural wrapper-key fix from step 3).
  3. The networked strategy, given the mdo_f25 graph as _graph_def,
     adopts it (initial_state TASK_CLASSIFIED) and seeds its
     graph_state_dict with the recommended_changes feedback key.
  4. The {recommended_changes} placeholder in MISSION_CONFIG resolves
     (default on first pass; the captured recommendation afterward)
     even though the prompt's literal braces defeat str.format.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live_mcp_llm

try:
    from dotenv import load_dotenv

    _REPO_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_REPO_ROOT / ".env", override=True)
except ImportError:
    pass


_EXPECTED_GRAPH_STATES = {
    "TASK_CLASSIFIED",
    "GEOMETRY_SETUP",
    "AERO_ANALYSIS",
    "MASS_ESTIMATION",
    "PROPULSION_SIZING",
    "MISSION_CONFIG",
    "SIMULATION_RUN",
    "RESULTS_REVIEW",
    "ERROR_CLASSIFICATION",
    "COMPLEXITY_ESCALATION",
    "COMPLETE",
}


class TestNetworkedGraphRoutedShape:
    """Pure shape checks. No LLM, no MCP required."""

    def test_combination_registered(self):
        from src.runners.batch_runner import ALL_COMBINATIONS

        names = [c.name for c in ALL_COMBINATIONS]
        assert "mdo_f25_networked_graph_routed" in names

        combo = next(
            c for c in ALL_COMBINATIONS
            if c.name == "mdo_f25_networked_graph_routed"
        )
        assert combo.org_structure == "networked"
        assert combo.handler == "graph_routed"
        assert combo.handler_config["predefined_graph"] == "mdo_f25"
        # workflow_phases disabled — the graph manages the workflow.
        assert combo.strategy_config["networked"]["workflow_phases"] == []
        # selection_mode=concurrent_blackboard activates the DAG-executor
        # (without it the graph short-circuits to the serial path).
        assert (
            combo.strategy_config["networked"]["selection_mode"]
            == "concurrent_blackboard"
        )

    def test_graph_derives_parallel_dag_todos(self):
        """The DAG-executor uses the graph's explicit depends_on to build
        a PARALLEL work DAG: the 7 disciplines (classifier excluded), with
        AERO and MASS both depending only on GEOMETRY (so they run in
        parallel), PROPULSION on MASS, MISSION on AERO+MASS, RESULTS on
        SIMULATION+PROPULSION."""
        from src.coordination.graph_definition import load_graph_from_yaml
        from src.coordination.strategies.networked import NetworkedStrategy

        graph = load_graph_from_yaml("config/mdo_f25_graph.yaml")
        strat = NetworkedStrategy()
        strat._graph = graph
        todos = strat._derive_graph_todos()
        names = {name for name, _desc, _dep in todos}
        # The 7 disciplines participate; the TASK_CLASSIFIED classifier
        # (no depends_on) is excluded — GEOMETRY is the DAG root.
        assert names == {
            "GEOMETRY_SETUP",
            "AERO_ANALYSIS",
            "MASS_ESTIMATION",
            "PROPULSION_SIZING",
            "MISSION_CONFIG",
            "SIMULATION_RUN",
            "RESULTS_REVIEW",
        }
        assert "TASK_CLASSIFIED" not in names
        deps = {name: dep for name, _desc, dep in todos}
        assert deps["GEOMETRY_SETUP"] == []
        # AERO and MASS are PARALLEL — both depend only on GEOMETRY.
        assert deps["AERO_ANALYSIS"] == ["GEOMETRY_SETUP"]
        assert deps["MASS_ESTIMATION"] == ["GEOMETRY_SETUP"]
        assert deps["PROPULSION_SIZING"] == ["MASS_ESTIMATION"]
        assert set(deps["MISSION_CONFIG"]) == {"AERO_ANALYSIS", "MASS_ESTIMATION"}
        assert set(deps["RESULTS_REVIEW"]) == {"SIMULATION_RUN", "PROPULSION_SIZING"}

    def test_concurrent_dag_unlocks_aero_and_mass_in_parallel(self):
        """initialize() seeds the DAG; only GEOMETRY is claimable at start,
        and completing GEOMETRY makes BOTH AERO and MASS available at once
        (the parallelism the design unlocks)."""
        from src.coordination.graph_definition import load_graph_from_yaml
        from src.coordination.strategies.networked import NetworkedStrategy

        graph = load_graph_from_yaml("config/mdo_f25_graph.yaml")
        strat = NetworkedStrategy()
        strat.initialize({}, {
            "_graph_def": graph,
            "networked": {
                "workflow_phases": [],
                "selection_mode": "concurrent_blackboard",
            },
        })
        bb = strat._blackboard
        assert bb is not None
        assert {t.name for t in bb.read_available_todos()} == {"GEOMETRY_SETUP"}
        bb.claim_todo("GEOMETRY_SETUP", "agent_1")
        bb.complete_todo("GEOMETRY_SETUP", "agent_1", "mesh ready")
        # Both independent disciplines become claimable simultaneously.
        assert {t.name for t in bb.read_available_todos()} == {
            "AERO_ANALYSIS",
            "MASS_ESTIMATION",
        }

    def test_release_claimed_todos_self_heals_dropped_claim(self):
        """A node claimed but not completed (peer cut off mid-node) is
        released back to pending between cycles so it can be retried."""
        from src.coordination.graph_definition import load_graph_from_yaml
        from src.coordination.strategies.networked import NetworkedStrategy

        graph = load_graph_from_yaml("config/mdo_f25_graph.yaml")
        strat = NetworkedStrategy()
        strat.initialize({}, {
            "_graph_def": graph,
            "networked": {
                "workflow_phases": [],
                "selection_mode": "concurrent_blackboard",
            },
        })
        bb = strat._blackboard
        bb.claim_todo("GEOMETRY_SETUP", "agent_1")  # claimed, never completed
        assert {t.name for t in bb.read_available_todos()} == set()  # blocked
        released = bb.release_claimed_todos()
        assert "GEOMETRY_SETUP" in released
        assert {t.name for t in bb.read_available_todos()} == {"GEOMETRY_SETUP"}

    def test_shared_graph_yaml_still_loads(self):
        from src.coordination.graph_definition import load_graph_from_yaml

        graph = load_graph_from_yaml("config/mdo_f25_graph.yaml")
        assert graph.initial_state == "TASK_CLASSIFIED"
        assert set(graph.states.keys()) == _EXPECTED_GRAPH_STATES

    def test_networked_strategy_adopts_graph_and_seeds_feedback(self):
        """When given the mdo_f25 graph as _graph_def, the networked
        strategy must adopt it (initial state TASK_CLASSIFIED) and
        initialize its graph_state_dict with the recommended_changes
        feedback key so the {recommended_changes} placeholder resolves."""
        from src.coordination.graph_definition import load_graph_from_yaml
        from src.coordination.strategies.networked import NetworkedStrategy

        graph = load_graph_from_yaml("config/mdo_f25_graph.yaml")
        strat = NetworkedStrategy()
        # Minimal config: one peer + the graph def. No agents needed for
        # the state-dict seeding path.
        config = {
            "_graph_def": graph,
            "networked": {"workflow_phases": []},
        }
        strat.initialize({}, config)
        assert strat._graph is not None
        assert strat._graph_current_state == "TASK_CLASSIFIED"
        assert "recommended_changes" in strat._graph_state_dict
        assert strat._graph_state_dict["recommended_changes"] == ""

    def test_mission_config_placeholder_resolves_for_networked(self):
        """MISSION_CONFIG's {recommended_changes} placeholder must
        resolve in the networked path despite the prompt's literal
        JSON braces (which defeat str.format). The networked strategy
        does a targeted replace, like the handler."""
        from src.coordination.graph_definition import load_graph_from_yaml
        from src.coordination.strategies.networked import NetworkedStrategy

        graph = load_graph_from_yaml("config/mdo_f25_graph.yaml")
        strat = NetworkedStrategy()
        strat.initialize({}, {"_graph_def": graph, "networked": {"workflow_phases": []}})
        # Simulate a captured recommendation from a prior review pass.
        strat._graph_state_dict["recommended_changes"] = "Aircraft.Wing.AREA -> decrease"
        strat._graph_current_state = "MISSION_CONFIG"
        action = strat._graph_driven_next_step([], {"task": "T"})
        ctx = action.input_context
        assert "{recommended_changes}" not in ctx
        assert "Aircraft.Wing.AREA -> decrease" in ctx
