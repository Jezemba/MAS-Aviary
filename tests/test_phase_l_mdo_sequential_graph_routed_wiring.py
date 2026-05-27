"""Phase L wiring test: mdo_f25_sequential_graph_routed (cheap).

Validates the new combination wires together correctly WITHOUT paying
for a full 5-MCP graph_routed pipeline run.

The sequential org structure is already proven by
`mdo_f25_sequential_iterative_feedback` (wandb iepdeu70) and
`mdo_f25_sequential_staged_pipeline` (wandb u77aj8bg). This combo
differs only in the execution handler — graph_routed drives a
state-machine defined in `config/mdo_f25_graph.yaml` (11 states)
rather than the linear 7-stage staged_pipeline.

What this test covers:
  1. ``config/mdo_f25_graph.yaml`` loads through the structural
     wrapper-key detection (`mdo_f25_graph:` top-level key).
  2. The combo `mdo_f25_sequential_graph_routed` is registered in
     `ALL_COMBINATIONS` with `org_structure="sequential"` +
     `handler="graph_routed"` + `predefined_graph="mdo_f25"`.
  3. The GraphRoutedHandler resolves the predefined graph by name
     and exposes the 11 expected states + TASK_CLASSIFIED initial
     + COMPLETE terminal.
  4. Each state's `agent` field (when non-null) names one of the
     7 MDO agents (geometry_engineer, aerodynamics_analyst, ...,
     mdo_integrator) that exist in
     `config/mdo_f25_sequential_agents.yaml`.

Most checks are pure YAML / handler-config — no LLM required.
Requires ANTHROPIC_API_KEY + 5 MCPs only for the build-coordinator
path, which is skipped if the env / MCPs are missing.
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

_EXPECTED_AGENT_NAMES = {
    "geometry_engineer",
    "aerodynamics_analyst",
    "structures_analyst",
    "propulsion_analyst",
    "mission_architect",
    "simulation_executor",
    "mdo_integrator",
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
                    "clientInfo": {"name": "phase-l-gr-test", "version": "0.1"},
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


class TestGraphYamlShape:
    """Pure YAML / handler-config checks. No LLM, no MCP required."""

    def test_graph_yaml_loads_via_structural_wrapper(self):
        """`mdo_f25_graph.yaml` uses ``mdo_f25_graph:`` as the top-level
        wrapper. The structural detection in load_graph_from_yaml must
        find it without naming it explicitly."""
        from src.coordination.graph_definition import load_graph_from_yaml

        graph = load_graph_from_yaml("config/mdo_f25_graph.yaml")
        assert graph.initial_state == "TASK_CLASSIFIED"
        assert "COMPLETE" in graph.terminal_states
        assert set(graph.states.keys()) == _EXPECTED_GRAPH_STATES

    def test_every_agent_state_names_a_known_mdo_agent(self):
        """Each state with a non-null ``agent`` field must reference one
        of the 7 MDO agents from mdo_f25_sequential_agents.yaml.
        Otherwise the strategy can't dispatch the state."""
        from src.coordination.graph_definition import load_graph_from_yaml

        graph = load_graph_from_yaml("config/mdo_f25_graph.yaml")
        unknown_refs = {}
        for name, state in graph.states.items():
            agent = state.agent
            if agent is None:
                continue
            if agent not in _EXPECTED_AGENT_NAMES:
                unknown_refs[name] = agent
        assert not unknown_refs, (
            f"Graph states reference unknown agents: {unknown_refs}. "
            f"Valid agents: {sorted(_EXPECTED_AGENT_NAMES)}"
        )

    def test_combination_registered(self):
        from src.runners.batch_runner import ALL_COMBINATIONS

        names = [c.name for c in ALL_COMBINATIONS]
        assert "mdo_f25_sequential_graph_routed" in names

        combo = next(
            c for c in ALL_COMBINATIONS
            if c.name == "mdo_f25_sequential_graph_routed"
        )
        assert combo.org_structure == "sequential"
        assert combo.handler == "graph_routed"
        assert combo.handler_config["predefined_graph"] == "mdo_f25"
        assert combo.strategy_config["pipeline_template"] == "mdo_f25"

    def test_handler_resolves_predefined_graph(self):
        """GraphRoutedHandler with ``predefined_graph: mdo_f25`` must
        load config/mdo_f25_graph.yaml and expose the 11 states."""
        from src.coordination.graph_routed_handler import GraphRoutedHandler

        handler = GraphRoutedHandler({"predefined_graph": "mdo_f25"})
        # _load_graph requires an agents dict but only uses it for
        # validation in the strategy path; passing an empty mapping is
        # safe for shape inspection.
        graph = handler._load_graph({})
        assert graph.initial_state == "TASK_CLASSIFIED"
        assert set(graph.states.keys()) == _EXPECTED_GRAPH_STATES


def _build_graph_routed_coordinator():
    """Build a Coordinator the same way batch_runner does for this
    combo. Used by the live LLM test below; skipped if env / MCPs
    not available."""
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

    agents_path, coord_path = _MDO_F25_STRATEGY_CONFIGS["sequential"]
    config = load_config("config/mdo_f25_run_claude.yaml")
    config.agents_config = agents_path
    config.coordination_config = coord_path

    coordinator = Coordinator.from_config(
        config,
        logger=InstrumentationLogger(config={}),
        strategy_override="sequential",
    )

    handler = _build_handler("graph_routed", {"predefined_graph": "mdo_f25"})
    assert handler is not None
    coordinator.execution_handler = handler
    coordinator.config["execution_handler"] = "graph_routed"

    # Cap the agent step budget so the test can't run away. The graph
    # handler drives state transitions; we only need to confirm the
    # coordinator + handler load cleanly, not run the full graph.
    coord_term = coordinator.config.setdefault("termination", {})
    coord_term["max_turns"] = 2

    return coordinator


def test_coordinator_loads_with_graph_routed_handler():
    """End-to-end shape check: building the coordinator the way
    batch_runner does, then driving ``strategy.initialize()`` (the
    point at which the sequential strategy instantiates per-stage
    agents from mdo_f25_sequential_agents.yaml), must yield a
    handler whose ``_predefined_graph_name`` is ``mdo_f25`` and
    expose all 7 MDO agents on the coordinator."""
    coordinator = _build_graph_routed_coordinator()

    handler = coordinator.execution_handler
    assert handler is not None
    assert getattr(handler, "_predefined_graph_name", None) == "mdo_f25"

    coordinator.strategy.initialize(coordinator.agents, coordinator.config)

    missing_agents = _EXPECTED_AGENT_NAMES - set(coordinator.agents.keys())
    assert not missing_agents, (
        f"sequential strategy did not instantiate all 7 MDO agents. "
        f"Missing: {missing_agents}. "
        f"Has: {sorted(coordinator.agents.keys())}"
    )
