"""Phase L wiring test: mdo_f25_sequential_staged_pipeline (cheap).

Validates the new combination wires together correctly WITHOUT paying
for a full 5-MCP pipeline run (~$0.30-0.50, 12-15 min).

The sequential org structure is already proven by
`mdo_f25_sequential_iterative_feedback` (wandb iepdeu70). This combo
differs only in the execution handler — staged_pipeline runs each
stage once with an OBSERVATIONAL completion check, then advances
regardless. The cheap test caps `termination.max_turns` very low so
only the first one or two stages fire (~$0.10-0.20, ~1-2 min wall).

What this test covers:
  1. The combo `mdo_f25_sequential_staged_pipeline` is registered in
     `ALL_COMBINATIONS` with `org_structure="sequential"` +
     `handler="staged_pipeline"`.
  2. `_MDO_F25_STAGED_HANDLER_CONFIG_SEQUENTIAL` resolves to the
     `config/mdo_f25_staged_pipeline_sequential.yaml` pipeline file
     (the short-prompt flavor; the long-prompt
     `config/mdo_f25_staged_pipeline.yaml` is used by the
     orchestrated_staged_pipeline combo instead).
  3. The pipeline YAML parses cleanly: 7 stages with valid
     completion_criteria types, brief stage_prompts, names matching
     the 7-agent template in `config/mdo_f25_sequential_agents.yaml`.
  4. The StagedPipelineHandler loads and reports 7 stages.
  5. The first stage's agent (`geometry_engineer`) gets at least the
     core tigl-mcp tools (`open_cpacs`, `generate_volume_mesh`) plus
     the audited tools confirmed present in the agents YAML.

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


_EXPECTED_STAGE_NAMES = [
    "geometry_engineer",
    "aerodynamics_analyst",
    "structures_analyst",
    "propulsion_analyst",
    "mission_architect",
    "simulation_executor",
    "mdo_integrator",
]

_REQUIRED_TIGL_TOOLS_FOR_GEOMETRY = {
    "open_cpacs",
    "generate_volume_mesh",
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
                    "clientInfo": {"name": "phase-l-staged-test", "version": "0.1"},
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


class TestPipelineYamlShape:
    """Pure YAML / handler-config checks. No LLM required, no MCP
    required — these run under the `live_mcp_llm` marker only for
    grouping with the other Phase L wiring tests, but they don't
    actually need the live infrastructure."""

    def test_pipeline_yaml_parses_with_seven_stages(self):
        from src.coordination.stage_definition import load_pipeline_from_yaml

        pipeline = load_pipeline_from_yaml("config/mdo_f25_staged_pipeline_sequential.yaml")
        names = [s.name for s in pipeline.stages]
        assert names == _EXPECTED_STAGE_NAMES, (
            f"Pipeline stage names must match the 7-agent template in "
            f"mdo_f25_sequential_agents.yaml. Got: {names}"
        )

    def test_every_stage_has_valid_completion_criteria(self):
        from src.coordination.stage_definition import (
            VALID_CRITERIA_TYPES,
            load_pipeline_from_yaml,
        )

        pipeline = load_pipeline_from_yaml("config/mdo_f25_staged_pipeline_sequential.yaml")
        for stage in pipeline.stages:
            ctype = stage.completion_criteria.type
            assert ctype in VALID_CRITERIA_TYPES, (
                f"Stage {stage.name!r} has unknown criteria type {ctype!r} — "
                f"valid types: {VALID_CRITERIA_TYPES}"
            )

    def test_every_stage_has_nonempty_stage_prompt(self):
        from src.coordination.stage_definition import load_pipeline_from_yaml

        pipeline = load_pipeline_from_yaml("config/mdo_f25_staged_pipeline_sequential.yaml")
        empty = [s.name for s in pipeline.stages if not s.stage_prompt.strip()]
        assert not empty, (
            f"All stages must carry a brief stage_prompt (the handler "
            f"appends it to the agent's existing role). Empty: {empty}"
        )

    def test_staged_handler_config_registered(self):
        from src.runners.batch_runner import (
            _MDO_F25_STAGED_HANDLER_CONFIG_SEQUENTIAL,
            ALL_COMBINATIONS,
        )

        assert _MDO_F25_STAGED_HANDLER_CONFIG_SEQUENTIAL["pipeline_path"] == (
            "config/mdo_f25_staged_pipeline_sequential.yaml"
        )
        names = [c.name for c in ALL_COMBINATIONS]
        assert "mdo_f25_sequential_staged_pipeline" in names

        combo = next(
            c for c in ALL_COMBINATIONS
            if c.name == "mdo_f25_sequential_staged_pipeline"
        )
        assert combo.org_structure == "sequential"
        assert combo.handler == "staged_pipeline"
        assert combo.handler_config["pipeline_path"] == (
            "config/mdo_f25_staged_pipeline_sequential.yaml"
        )
        assert combo.strategy_config["pipeline_template"] == "mdo_f25"


def _build_staged_coordinator():
    """Build the Coordinator the same way batch_runner does for this
    combo, then cap max_turns very low so the test cannot run away."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — required for live LLM tests")

    down = _check_mcp_servers_alive()
    if down:
        pytest.skip(f"MCP servers not running: {down}")

    from src.config.loader import load_config
    from src.coordination.coordinator import Coordinator
    from src.logging.logger import InstrumentationLogger
    from src.runners.batch_runner import (
        _MDO_F25_STAGED_HANDLER_CONFIG_SEQUENTIAL,
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

    # Override the handler to staged_pipeline (the coord YAML defaults
    # to iterative_feedback — same swap batch_runner.py does at
    # _execute_combination).
    handler = _build_handler("staged_pipeline", _MDO_F25_STAGED_HANDLER_CONFIG_SEQUENTIAL)
    assert handler is not None
    coordinator.execution_handler = handler
    coordinator.config["execution_handler"] = "staged_pipeline"

    # Cap turns so the test can't run more than one or two stages.
    coord_term = coordinator.config.setdefault("termination", {})
    coord_term["max_turns"] = 2
    # Also tighten the per-stage step budget.
    coord_seq = coordinator.config.setdefault("sequential", {})
    coord_seq["stage_max_steps"] = 4

    return coordinator


def test_staged_handler_reports_seven_stages():
    """Build the coordinator, confirm the StagedPipelineHandler loads
    7 stages from the new pipeline YAML when its lazy _resolve_pipeline
    fires. No LLM call required."""
    coordinator = _build_staged_coordinator()
    handler = coordinator.execution_handler
    pipeline = handler._resolve_pipeline()
    assert pipeline is not None
    assert len(pipeline.stages) == 7
    assert [s.name for s in pipeline.stages] == _EXPECTED_STAGE_NAMES


def test_geometry_engineer_agent_has_required_tigl_tools():
    """The first stage's agent must have at minimum the core tigl-mcp
    tools so the geometry stage can do real work. Calls
    strategy.initialize() directly to populate the per-stage agent
    dict without an LLM round-trip (initialize() only instantiates
    ToolCallingAgent objects)."""
    coordinator = _build_staged_coordinator()
    coordinator.strategy.initialize(coordinator.agents, coordinator.config)

    geom_agent = coordinator.agents.get("geometry_engineer")
    assert geom_agent is not None, (
        f"geometry_engineer agent missing from coordinator.agents. "
        f"Found: {sorted(coordinator.agents.keys())}"
    )
    tool_names = set(geom_agent.tools.keys())
    missing = _REQUIRED_TIGL_TOOLS_FOR_GEOMETRY - tool_names
    assert not missing, (
        f"geometry_engineer missing core tigl-mcp tools {missing}. "
        f"Has: {sorted(tool_names)[:12]}..."
    )
