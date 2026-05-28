"""Phase L wiring test: mdo_f25_orchestrated_staged_pipeline (cheap).

Validates the orchestrated × staged_pipeline wiring without paying for
a full 5-MCP pipeline run. The orchestrator's create_agent loop costs
~$0.10-0.30 by itself; we don't actually invoke it here — we just
confirm the registration + config plumbing line up.

What this test covers:
  1. The combo `mdo_f25_orchestrated_staged_pipeline` is registered
     in `ALL_COMBINATIONS` with `org_structure="orchestrated"`,
     `handler="staged_pipeline"`, `lifecycle_mode="setup_only"`, and
     the shared `_MDO_F25_STAGED_HANDLER_CONFIG` handler config.
  2. The orchestrator agents YAML names the 7 role names that
     match the stage names in the pipeline YAML — without this
     match the staged_pipeline handler would skip stages whose
     names don't appear in the orchestrator's created workers.
  3. The shared `_MDO_F25_STAGED_HANDLER_CONFIG` resolves to the
     same 7-stage pipeline used by the sequential combo (no
     divergence between sequential and orchestrated paths).

This file is `live_mcp_llm`-tagged for grouping with the other Phase L
wiring tests, but the tests below are pure config inspection and run
in <1 s with no LLM / MCP calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.live_mcp_llm

# Auto-load .env (cheap — no LLM is invoked here, but downstream
# helpers may expect the env to be primed).
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


def test_combo_registered_with_per_stage_lifecycle():
    # Combo now uses per_stage lifecycle (2026-05-26). Earlier
    # setup_only attempt hit a content cascade — see CHANGELOG.
    from src.runners.batch_runner import (
        _MDO_F25_STAGED_HANDLER_CONFIG,
        ALL_COMBINATIONS,
    )

    names = [c.name for c in ALL_COMBINATIONS]
    assert "mdo_f25_orchestrated_staged_pipeline" in names

    combo = next(
        c for c in ALL_COMBINATIONS
        if c.name == "mdo_f25_orchestrated_staged_pipeline"
    )
    assert combo.org_structure == "orchestrated"
    assert combo.handler == "staged_pipeline"

    # per_stage: orchestrator delegates one stage at a time with
    # per-stage feedback. See OrchestratedStrategy._per_stage_execution.
    assert combo.strategy_config.get("orchestrated", {}).get(
        "lifecycle_mode"
    ) == "per_stage"

    # Reuses the same handler config as the sequential combo.
    assert combo.handler_config is _MDO_F25_STAGED_HANDLER_CONFIG
    assert combo.handler_config["pipeline_path"] == (
        "config/mdo_f25_staged_pipeline.yaml"
    )


def test_orchestrator_role_names_match_pipeline_stages():
    """The staged_pipeline handler matches pipeline stages to
    assignments by name. The orchestrator agents YAML must therefore
    instruct the orchestrator to create workers with EXACTLY the same
    role names as the pipeline stages — otherwise the handler will
    skip stages whose names don't appear in the created workers."""
    with open("config/mdo_f25_orchestrated_agents.yaml") as f:
        cfg = yaml.safe_load(f)

    # Walk the orchestrator's role definitions (the "AGENT ROLE →
    # TOOL MAPPING" section). The role-name listing is in the
    # orchestrator's system prompt; we'd find it by searching the
    # serialized text for each expected stage name.
    serialized = yaml.dump(cfg)
    missing = [
        stage for stage in _EXPECTED_STAGE_NAMES if stage not in serialized
    ]
    assert not missing, (
        f"The orchestrator agents YAML must mention the role names "
        f"that match the staged_pipeline stages so the orchestrator "
        f"creates workers with the correct names. Missing: {missing}"
    )


def test_per_stage_first_call_has_stage_specific_context():
    """No LLM cost, no MCP cost: build the Coordinator the way
    batch_runner does, call strategy.next_step() ONCE to confirm
    per_stage mode delivers stage-specific context to the
    orchestrator (geometry_engineer first) along with the tool hint."""
    import os

    from src.config.loader import load_config
    from src.coordination.coordinator import Coordinator
    from src.logging.logger import InstrumentationLogger
    from src.runners.batch_runner import (
        _MDO_F25_STAGED_HANDLER_CONFIG,
        _MDO_F25_STRATEGY_CONFIGS,
        _build_handler,
    )

    if not os.environ.get("OPENAI_API_KEY") and not os.environ.get(
        "ANTHROPIC_API_KEY"
    ):
        pytest.skip("API key not set")

    agents_path, coord_path = _MDO_F25_STRATEGY_CONFIGS["orchestrated"]
    config = load_config("config/mdo_f25_run_claude.yaml")
    config.agents_config = agents_path
    config.coordination_config = coord_path
    coord = Coordinator.from_config(
        config,
        logger=InstrumentationLogger(config={}),
        strategy_override="orchestrated",
    )
    # Apply the combo's strategy_config override (batch_runner does
    # this in _execute_combination; replicating here).
    coord.config.setdefault("orchestrated", {})["lifecycle_mode"] = "per_stage"
    # Re-initialize so the strategy sees per_stage.
    coord.strategy.initialize(coord.agents, coord.config)

    # Wire staged_pipeline handler so _pipeline_stage_names is loaded.
    handler = _build_handler("staged_pipeline", _MDO_F25_STAGED_HANDLER_CONFIG)
    assert handler is not None
    coord.execution_handler = handler
    pipeline = handler._resolve_pipeline()
    stage_names = [s.name for s in pipeline.stages]
    coord.config["_pipeline_stage_names"] = stage_names
    coord.strategy._pipeline_stage_names = stage_names

    # First call — should target Stage 1 (geometry_engineer).
    action = coord.strategy.next_step([], {"task": "Design F25"})
    assert action.action_type == "invoke_agent"
    assert action.agent_name == "orchestrator"
    # Stage-specific context is present.
    assert "geometry_engineer" in action.input_context
    # Tool hint should mention TiGL geometry tools.
    assert "open_cpacs" in action.input_context or "generate_volume_mesh" in action.input_context
    # The original task is preserved on the first turn.
    assert "Design F25" in action.input_context
    # Stage cursor is at 0.
    assert coord.strategy._current_stage_idx == 0


def test_staged_handler_resolves_pipeline_from_handler_config():
    """Construct a fresh StagedPipelineHandler from the same
    handler_config the combo uses, confirm it resolves to 7 stages
    in the expected order."""
    from src.coordination.staged_pipeline_handler import StagedPipelineHandler
    from src.runners.batch_runner import _MDO_F25_STAGED_HANDLER_CONFIG

    handler = StagedPipelineHandler(_MDO_F25_STAGED_HANDLER_CONFIG)
    pipeline = handler._resolve_pipeline()
    assert pipeline is not None
    assert [s.name for s in pipeline.stages] == _EXPECTED_STAGE_NAMES
