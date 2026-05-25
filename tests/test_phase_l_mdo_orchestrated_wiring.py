"""Phase L wiring test: mdo_f25_orchestrated_iterative_feedback (cheap).

Validates that the combination newly registered in batch_runner.py
(`mdo_f25_orchestrated_iterative_feedback`) wires together correctly,
WITHOUT paying for a full 5-MCP pipeline run (~$0.30-0.50, 12-15 min).

What this test covers:
  1. ``_MDO_F25_STRATEGY_CONFIGS["orchestrated"]`` resolves to the
     MDO-F25 orchestrator agents YAML + generic orchestrated coord YAML.
  2. Coordinator.from_config builds an orchestrator agent whose system
     prompt carries the MDO-F25 specific phrases (5 MCPs, DLR-F25 TLARs,
     discipline roles).
  3. With lifecycle_mode forced to "setup_only", the orchestrator
     delegates a team and terminates without firing any actual
     disciplinary tool calls (no SU2 solve, no aviary run_simulation).
  4. The orchestrator's delegation honours the MDO discipline roles —
     at least one of {geometry_engineer, aerodynamics_analyst,
     structures_analyst, propulsion_analyst, mission_architect,
     simulation_executor, mdo_integrator} is created.

Cost: a single orchestrator delegation cycle (~3-6 LLM calls, no worker
LLM calls). ~$0.05-0.15, ~1-2 min wall-clock.

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


_MDO_DISCIPLINE_ROLES = {
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
                    "clientInfo": {"name": "phase-l-test", "version": "0.1"},
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


def _build_orchestrated_coordinator():
    """Build the Coordinator the same way batch_runner does for this combo,
    then force setup_only lifecycle so workers never actually execute."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — required for live LLM tests")

    down = _check_mcp_servers_alive()
    if down:
        pytest.skip(f"MCP servers not running: {down}")

    from src.config.loader import load_config
    from src.coordination.coordinator import Coordinator
    from src.logging.logger import InstrumentationLogger
    from src.runners.batch_runner import _MDO_F25_STRATEGY_CONFIGS

    # Sanity: combo IS registered (this is part of what we're testing).
    assert "orchestrated" in _MDO_F25_STRATEGY_CONFIGS, (
        "Expected _MDO_F25_STRATEGY_CONFIGS['orchestrated'] to be registered "
        "by batch_runner.py — Phase L wiring is incomplete."
    )
    agents_path, coord_path = _MDO_F25_STRATEGY_CONFIGS["orchestrated"]
    assert agents_path == "config/mdo_f25_orchestrated_agents.yaml"
    assert coord_path == "config/orchestrated.yaml"

    config = load_config("config/mdo_f25_run_claude.yaml")
    # Swap to the orchestrated-specific configs (mirrors batch_runner._execute_combination).
    config.agents_config = agents_path
    config.coordination_config = coord_path

    coordinator = Coordinator.from_config(
        config,
        logger=InstrumentationLogger(config={}),
        strategy_override="orchestrated",
    )

    # Force setup_only + tight bounds so the test cannot run any worker LLM
    # calls or any real disciplinary tool calls.  Strategy reads these
    # values in initialize(), which fires inside coordinator.run().
    coord_overrides = coordinator.config.setdefault("orchestrated", {})
    coord_overrides["lifecycle_mode"] = "setup_only"
    coord_overrides["max_orchestrator_turns"] = 6
    coord_overrides["worker_max_steps"] = 1  # placeholder workers won't loop
    term = coordinator.config.setdefault("termination", {})
    term["max_turns"] = 10

    return coordinator


def _orchestrator_tool_calls(coordinator) -> list[dict]:
    """Return the orchestrator's tool calls from the run history."""
    orch_name = getattr(coordinator.strategy, "_orchestrator_name", "orchestrator")
    out: list[dict] = []
    for msg in coordinator.history.get_all():
        if msg.agent_name != orch_name:
            continue
        for tc in msg.tool_calls:
            out.append({"name": tc.tool_name, "inputs": tc.inputs, "error": tc.error})
    return out


def test_mdo_f25_orchestrated_wiring_setup_only():
    """End-to-end: build the combo, force setup_only, run a tiny task,
    and assert the MDO-aware orchestrator delegated to discipline roles
    without firing any real disciplinary tool calls.
    """
    coordinator = _build_orchestrated_coordinator()

    # Pre-run: the orchestrator agent must exist before .run() (created by
    # the agent factory from the MDO-F25 YAML).
    assert "orchestrator" in coordinator.agents, (
        f"orchestrator agent missing from {list(coordinator.agents)}"
    )

    orch = coordinator.agents["orchestrator"]

    # System-prompt sanity: confirms the MDO-F25 YAML loaded (not the
    # aviary-only one).  Both YAMLs define an 'orchestrator' agent so the
    # only way to distinguish is via discipline-specific text.
    system_prompt = ""
    if hasattr(orch, "memory") and hasattr(orch.memory, "system_prompt"):
        sp = orch.memory.system_prompt
        system_prompt = sp.system_prompt if hasattr(sp, "system_prompt") else str(sp or "")
    # Fall back to instructions attr (smolagents sometimes mirrors there).
    if not system_prompt:
        system_prompt = getattr(orch, "instructions", "") or ""

    mdo_markers = ("TiGL", "SU2", "mass-mcp", "pyCycle", "DLR-F25")
    missing = [m for m in mdo_markers if m not in system_prompt]
    assert not missing, (
        f"orchestrator system prompt missing MDO-F25 markers {missing} — "
        f"wrong YAML loaded? prompt length={len(system_prompt)}"
    )

    # Run a tight delegation-only task. The orchestrator will:
    #   1. list_available_tools
    #   2. create_agent (one or more discipline workers)
    #   3. assign_task
    #   4. final_answer "DELEGATION_COMPLETE"
    # setup_only mode means workers are run by PlaceholderExecutor which
    # does not call the LLM or any MCP tool — the test ends after delegation.
    task = (
        "Design a DLR-F25 aircraft: range 2500 nmi, 239 passengers, "
        "cruise Mach 0.78, FL330. For this dry-run delegation test, "
        "create the smallest team that covers geometry, mass, and mission "
        "(at most 3 workers). Assign each worker a one-sentence task. "
        "Then call final_answer with 'DELEGATION_COMPLETE'."
    )
    coordinator.run(task)

    calls = _orchestrator_tool_calls(coordinator)
    call_names = [c["name"] for c in calls]

    # Orchestrator should have discovered tools and delegated.
    assert "list_available_tools" in call_names, (
        f"orchestrator never discovered tools; calls={call_names}"
    )

    create_calls = [c for c in calls if c["name"] == "create_agent"]
    assign_calls = [c for c in calls if c["name"] == "assign_task"]
    assert create_calls, (
        f"orchestrator never created any worker; calls={call_names}"
    )
    assert assign_calls, (
        f"orchestrator never assigned a task; calls={call_names}"
    )

    # At least one created agent must use a recognized MDO discipline role.
    # The MDO-F25 orchestrator prompt explicitly maps roles → tools, so a
    # correctly-wired Claude orchestrator should pick from those names.
    created_names: set[str] = set()
    for c in create_calls:
        inputs = c["inputs"] or {}
        if isinstance(inputs, dict):
            name = inputs.get("name")
            if isinstance(name, str):
                created_names.add(name)
    mdo_hits = created_names & _MDO_DISCIPLINE_ROLES
    assert mdo_hits, (
        f"orchestrator did not create any recognized MDO discipline role. "
        f"Created: {sorted(created_names)}. "
        f"Expected at least one of: {sorted(_MDO_DISCIPLINE_ROLES)}"
    )

    # Negative assertion: in setup_only mode, no real disciplinary MCP tool
    # should have been invoked anywhere in the history. The orchestrator's
    # own list_available_tools is a meta-tool, not a discipline tool.
    forbidden_disciplinary_tools = {
        "run_su2_solver",
        "run_simulation",
        "estimate_mass",
        "run_cycle",
        "generate_volume_mesh",
    }
    for msg in coordinator.history.get_all():
        for tc in msg.tool_calls:
            assert tc.tool_name not in forbidden_disciplinary_tools, (
                f"setup_only mode leaked a real disciplinary tool call: "
                f"{tc.tool_name} from agent {msg.agent_name!r}"
            )
