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


def test_combo_registered_with_setup_only_lifecycle():
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

    # setup_only is load-bearing: without it the orchestrator would
    # re-invoke after every worker pass and triple wall-clock cost.
    assert combo.strategy_config.get("orchestrated", {}).get(
        "lifecycle_mode"
    ) == "setup_only"

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
