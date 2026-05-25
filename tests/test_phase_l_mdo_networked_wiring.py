"""Phase L wiring test: mdo_f25_networked_iterative_feedback (cheap).

Validates the new combination wires together correctly WITHOUT paying
for a full 5-MCP pipeline run (~$0.30-0.50, 12-15 min).

Networked has no setup_only equivalent, so the cheap test caps
``termination.max_turns`` at 5 — the 3 initial peers get one or two
blackboard reads / spawn / claim turns each, enough to prove the
strategy boots correctly and peers receive the right toolset. Cost
~$0.20-0.40, ~2-4 min wall.

What this test covers:
  1. ``_MDO_F25_STRATEGY_CONFIGS["networked"]`` resolves to the
     MDO-F25 networked agents YAML + the new MDO-F25 networked
     coordination YAML.
  2. The coord YAML has NO ``workflow_phases`` (networked is
     intentionally structure-less for the F25 task — peers see all 54
     MCP tools and self-organize via the blackboard).
  3. The peer_template's ``base_system_prompt`` carries the MDO-F25
     markers (TiGL / SU2 / mass-mcp / pyCycle / DLR-F25), the
     dynamic-team-size guidance (``spawn_peer``), and the data-plane
     coupling note.
  4. Three peers are created at startup, each with the four peer
     coordination tools (``read_blackboard``, ``write_blackboard``,
     ``spawn_peer``, ``mark_task_done``) AND access to the discipline
     MCP tools (specifically ``set_aircraft_parameters``,
     ``estimate_mass``, ``run_su2_solver``).

Requires: ANTHROPIC_API_KEY, all 5 MCPs alive on default ports.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live_mcp_llm

# Auto-load .env so the test picks up ANTHROPIC_API_KEY without manual export.
try:
    from dotenv import load_dotenv

    _REPO_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_REPO_ROOT / ".env", override=True)
except ImportError:
    pass

_PEER_COORDINATION_TOOLS = {
    "read_blackboard",
    "write_blackboard",
    "spawn_peer",
    "mark_task_done",
}

_REQUIRED_MCP_TOOLS_VISIBLE_TO_PEERS = {
    # Mission / aviary
    "set_aircraft_parameters",
    "configure_mission",
    "run_simulation",
    # Structures
    "estimate_mass",
    # Aero
    "run_su2_solver",
    "get_valid_config_options",
    # Propulsion
    "get_design_inputs",
    # Geometry
    "generate_volume_mesh",
}


def _check_mcp_servers_alive() -> list[str]:
    """Return list of MCP names that fail an initialize handshake."""
    import json
    import urllib.error
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
                    "clientInfo": {"name": "phase-l-net-test", "version": "0.1"},
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


def _build_networked_coordinator():
    """Build the Coordinator the same way batch_runner does for this combo,
    then cap max_turns very low so the test cannot run away."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — required for live LLM tests")

    down = _check_mcp_servers_alive()
    if down:
        pytest.skip(f"MCP servers not running: {down}")

    from src.config.loader import load_config, load_yaml
    from src.coordination.coordinator import Coordinator
    from src.logging.logger import InstrumentationLogger
    from src.runners.batch_runner import _MDO_F25_STRATEGY_CONFIGS

    # Wiring sanity: combo IS registered.
    assert "networked" in _MDO_F25_STRATEGY_CONFIGS, (
        "Expected _MDO_F25_STRATEGY_CONFIGS['networked'] to be registered "
        "by batch_runner.py — Phase L networked wiring is incomplete."
    )
    agents_path, coord_path = _MDO_F25_STRATEGY_CONFIGS["networked"]
    assert agents_path == "config/mdo_f25_networked_agents.yaml"
    assert coord_path == "config/aviary_mdo_f25_networked.yaml"

    # Coord YAML sanity: structure-less by design (no workflow_phases).
    coord_yaml = load_yaml(coord_path)
    networked_cfg = coord_yaml.get("networked", {})
    assert networked_cfg.get("workflow_phases", []) == [], (
        f"Networked MDO F25 combo must NOT have workflow_phases (the "
        f"networked strategy is intentionally structure-less for this "
        f"task). Found: {networked_cfg.get('workflow_phases')!r}"
    )

    # Coord YAML sanity: concurrent_blackboard selection mode +
    # non-empty todo_seed. This is the post-2026-05-25 CodeCRDT-style
    # design — peers run in parallel threads racing for TODO claims.
    assert networked_cfg.get("selection_mode") == "concurrent_blackboard", (
        "MDO F25 networked combo expects selection_mode='concurrent_blackboard'. "
        f"Found: {networked_cfg.get('selection_mode')!r}"
    )
    todo_seed = networked_cfg.get("todo_seed", [])
    assert len(todo_seed) >= 7, (
        f"Expected ≥7 seeded TODOs covering the F25 disciplines, found "
        f"{len(todo_seed)}: {[t.get('name') if isinstance(t, dict) else t for t in todo_seed]}"
    )

    config = load_config("config/mdo_f25_run_claude.yaml")
    config.agents_config = agents_path
    config.coordination_config = coord_path

    coordinator = Coordinator.from_config(
        config,
        logger=InstrumentationLogger(config={}),
        strategy_override="networked",
    )

    # Cap turns so the test can't run away. 4 turns = each of 3 peers
    # gets ~1 turn, plus a possible blackboard read by the strategy.
    coord_term = coordinator.config.setdefault("termination", {})
    coord_term["max_turns"] = 4
    # Also tighten the per-peer step budget so a single peer's turn
    # can't itself exhaust the budget on heavy MCP work.
    coord_net = coordinator.config.setdefault("networked", {})
    coord_net["agent_max_steps"] = 3

    return coordinator


def _read_system_prompt(agent) -> str:
    mem = getattr(agent, "memory", None)
    if mem is None:
        return ""
    sp = getattr(mem, "system_prompt", None)
    if sp is None:
        return ""
    return sp.system_prompt if hasattr(sp, "system_prompt") else str(sp)


def test_mdo_f25_networked_wiring_capped_turns():
    """Build the combo, run it with max_turns=4, and assert that the
    networked strategy booted with the right peer count, peer tools,
    and MDO discipline knowledge in the prompt."""
    coordinator = _build_networked_coordinator()

    # Pre-run: the networked strategy creates initial_agents peers
    # inside Coordinator.run() (via strategy.initialize()). We need to
    # run first to populate coordinator.agents.
    task = (
        "Read the blackboard and post a one-line entry noting your "
        "name. Do not run any disciplinary tool. This is a "
        "wiring-check task only."
    )
    coordinator.run(task)

    # All initial peers should now exist. networked names them agent_1,
    # agent_2, agent_3 per the strategy's _create_peer_agent convention.
    peer_names = sorted(n for n in coordinator.agents if n.startswith("agent_"))
    assert len(peer_names) >= 3, (
        f"Expected at least 3 peer agents (initial_agents=3 in "
        f"config/aviary_mdo_f25_networked.yaml). Found: {peer_names}"
    )

    # Each peer should have the four peer coordination tools AND a
    # representative selection of MCP tools across all 5 disciplines.
    for name in peer_names[:3]:  # check just the initial 3
        peer = coordinator.agents[name]
        peer_tool_names = set(peer.tools.keys())

        missing_peer_tools = _PEER_COORDINATION_TOOLS - peer_tool_names
        assert not missing_peer_tools, (
            f"Peer {name!r} missing peer-coordination tools: "
            f"{missing_peer_tools}. Has: {sorted(peer_tool_names)[:10]}..."
        )

        missing_mcp_tools = _REQUIRED_MCP_TOOLS_VISIBLE_TO_PEERS - peer_tool_names
        assert not missing_mcp_tools, (
            f"Peer {name!r} missing MCP tools (networked should expose "
            f"ALL 54 tools to every peer): {missing_mcp_tools}"
        )

    # System prompt sanity: confirms the MDO peer_template loaded.
    first_peer = coordinator.agents[peer_names[0]]
    sp = _read_system_prompt(first_peer)
    mdo_markers = ("TiGL", "SU2", "mass-mcp", "pyCycle", "DLR-F25")
    missing = [m for m in mdo_markers if m not in sp]
    assert not missing, (
        f"First peer's system prompt missing MDO markers {missing} — "
        f"wrong peer_template loaded? prompt length={len(sp)}"
    )

    # The new dynamic-team-size guidance must be present (this is
    # the user-requested correction about spawn_peer).
    assert "spawn_peer" in sp, (
        "Peer prompt should mention spawn_peer (peers can grow the "
        "team beyond initial 3 up to max_agents). Found prompt of "
        f"length {len(sp)}."
    )

    # Data-plane coupling note must be present (informs peers that
    # CL/CD/mWing inject automatically — see Phase H/K writeups).
    assert "DATA-PLANE COUPLING" in sp, (
        "Peer prompt should explain the data-plane coupling so peers "
        "don't try to pass CL/CD/MASS_SCALER through the blackboard "
        "manually."
    )
