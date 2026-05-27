"""Phase L wiring test: mdo_f25_orchestrated_graph_routed (cheap).

The fourth Job 3 combination after sequential_staged_pipeline
(step 1, wandb u77aj8bg), orchestrated_staged_pipeline (step 2,
wandb 7ngitswj), and sequential_graph_routed (step 3, wandb
81w57h3g).

Differs from step 3 only in the org structure: orchestrator
builds a team of 7 MDO specialists at startup (one per agent
named in config/mdo_f25_graph.yaml's per-state ``agent`` field),
then the graph_routed handler dispatches each state to its named
agent. The graph YAML itself is shared with step 3.

What this test covers (mostly shape — no LLM required):
  1. The combo `mdo_f25_orchestrated_graph_routed` is registered
     in ALL_COMBINATIONS with org_structure="orchestrated" +
     handler="graph_routed" + predefined_graph="mdo_f25".
  2. The shared config/mdo_f25_graph.yaml still loads cleanly
     (regression guard against the wrapper-key fix landed in
     step 3).
  3. The GraphRoutedHandler resolves the predefined graph by name
     under this combo's wiring.
  4. (live LLM) The Coordinator builds end-to-end with the
     orchestrated strategy + graph_routed handler.

Requires ANTHROPIC_API_KEY + 5 MCPs only for the live build path,
which is skipped if env / MCPs are missing.
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


def _check_mcp_servers_alive() -> list[str]:
    """Return list of MCP names that fail an initialize handshake."""
    import json
    import urllib.request

    down = []
    for name, port in [
        ("tigl", 8500),
        ("su2", 8200),
        ("mass", 8700),
        ("pycycle", 8400),
        ("aviary", 8600),
    ]:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "id": 1,
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "phase-l-orch-gr-test", "version": "0.1"},
                },
            }
        ).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=3):
                pass
        except Exception:
            down.append(name)
    return down


class TestOrchestratedGraphRoutedShape:
    """Pure YAML / handler-config checks. No LLM, no MCP required."""

    def test_combination_registered(self):
        from src.runners.batch_runner import ALL_COMBINATIONS

        names = [c.name for c in ALL_COMBINATIONS]
        assert "mdo_f25_orchestrated_graph_routed" in names

        combo = next(
            c for c in ALL_COMBINATIONS
            if c.name == "mdo_f25_orchestrated_graph_routed"
        )
        assert combo.org_structure == "orchestrated"
        assert combo.handler == "graph_routed"
        assert combo.handler_config["predefined_graph"] == "mdo_f25"
        # Lock in the setup_only lifecycle override. Default
        # ``active`` re-wakes the orchestrator after each graph
        # state and triggers a 7-agent team rebuild that prevents
        # the pipeline from ever reaching GEOMETRY_SETUP — observed
        # in the v1 dry-run before this guard was added.
        assert (
            combo.strategy_config["orchestrated"]["lifecycle_mode"]
            == "setup_only"
        ), (
            "mdo_f25_orchestrated_graph_routed must pin "
            "lifecycle_mode=setup_only. Default 'active' causes the "
            "orchestrator to re-build the team after every graph state."
        )

    def test_shared_graph_yaml_still_loads(self):
        """Regression guard: the wrapper-key fix from step 3 must
        still work, since step 4 shares config/mdo_f25_graph.yaml."""
        from src.coordination.graph_definition import load_graph_from_yaml

        graph = load_graph_from_yaml("config/mdo_f25_graph.yaml")
        assert graph.initial_state == "TASK_CLASSIFIED"
        assert set(graph.states.keys()) == _EXPECTED_GRAPH_STATES

    def test_handler_resolves_predefined_graph(self):
        from src.coordination.graph_routed_handler import GraphRoutedHandler

        handler = GraphRoutedHandler({"predefined_graph": "mdo_f25"})
        graph = handler._load_graph({})
        assert set(graph.states.keys()) == _EXPECTED_GRAPH_STATES


def _build_orch_graph_coordinator():
    """Build the Coordinator the same way batch_runner does for this
    combo. Skipped if env / MCPs unavailable."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — required for live LLM tests")

    down = _check_mcp_servers_alive()
    if down:
        pytest.skip(f"MCP servers not running: {down}")

    from src.config.loader import load_config
    from src.coordination.coordinator import Coordinator
    from src.logging.logger import InstrumentationLogger
    from src.runners.batch_runner import (
        _MDO_F25_STRATEGY_CONFIGS,
        _build_handler,
    )

    agents_path, coord_path = _MDO_F25_STRATEGY_CONFIGS["orchestrated"]
    config = load_config("config/mdo_f25_run_claude.yaml")
    config.agents_config = agents_path
    config.coordination_config = coord_path

    coordinator = Coordinator.from_config(
        config,
        logger=InstrumentationLogger(config={}),
        strategy_override="orchestrated",
    )

    handler = _build_handler("graph_routed", {"predefined_graph": "mdo_f25"})
    assert handler is not None
    coordinator.execution_handler = handler
    coordinator.config["execution_handler"] = "graph_routed"

    # Cap turns so the test can't run away.
    coord_term = coordinator.config.setdefault("termination", {})
    coord_term["max_turns"] = 2

    return coordinator


def test_coordinator_loads_with_orchestrated_graph_routed_handler():
    """Build the coordinator and confirm the orchestrator strategy
    and graph_routed handler combine cleanly. The orchestrator
    creates worker agents dynamically at runtime (via create_agent),
    so the only static-check we can do here is the handler /
    strategy / config shape."""
    coordinator = _build_orch_graph_coordinator()

    handler = coordinator.execution_handler
    assert handler is not None
    assert getattr(handler, "_predefined_graph_name", None) == "mdo_f25"

    strategy = coordinator.strategy
    assert strategy is not None
    # Orchestrated strategy class name guard — protects against an
    # accidental swap to a different strategy by the override.
    assert "Orchestrated" in type(strategy).__name__, (
        f"Expected an orchestrated strategy; got {type(strategy).__name__}"
    )
