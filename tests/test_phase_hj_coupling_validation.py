"""Direct (no-LLM) sensitivity tests proving Phase H + J actually
move aviary's fuel burn.

The middleware-injection tests in ``test_phase_h_middleware_live.py``
prove the *plumbing* works — the new keys land in aviary's ``applied``
echo. These tests close the next gap: prove that varying the captured
upstream values produces a *measurable, monotonic* change in
``FUEL_BURNED_KG``. Until this passes, the Phase H/J wiring is
"correct but possibly silent."

Each test runs aviary 3× with different pre-seeded data_store values
(skipping SU2 / pyCycle entirely — those are exercised in earlier
tests). Total wall clock ~3 min, zero LLM cost.

Requires aviary-mcp on :8600 with the Phase H/J branches loaded.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from src.config.loader import AppConfig, LLMConfig, MCPConfig, MCPServerConfig
from src.tools.data_plane import get_design_state
from src.tools.tool_loader import load_tools_for_agent


pytestmark = pytest.mark.live_mcp


def _load_aviary_tools() -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-not-used"
    cfg = AppConfig(
        llm=LLMConfig(
            model_id="anthropic/claude-sonnet-4-20250514",
            backend="litellm", temperature=0.0, max_new_tokens=2048,
        ),
        mcp=MCPConfig(
            mode="real",
            servers=[
                MCPServerConfig(name="aviary",
                                url="http://127.0.0.1:8600/mcp",
                                transport="streamable-http"),
            ],
        ),
    )
    tools = load_tools_for_agent(
        ["create_session", "configure_mission", "set_aircraft_parameters",
         "run_simulation", "get_results"],
        cfg,
    )
    return {t.name: t for t in tools}


def _decode(resp: Any) -> dict:
    return json.loads(resp) if isinstance(resp, str) else resp


def _run_aviary_with_seeded_data(
    tools: dict, seed: dict[str, float]
) -> dict:
    """Pre-seed the data_store with the given Phase H/J values, then run
    a full aviary mission. Returns the get_results payload, which now
    includes ``cruise_cl_avg``, ``cruise_cd_avg``,
    ``cruise_sfc_avg_lb_per_hr_lbf`` and ``fuel_burned_kg``.
    """
    ds = get_design_state()
    assert ds is not None
    # Drop any leftover state from a prior iteration so each run is
    # independent. ``aero_*`` / ``pycycle_*`` keys are what the
    # middleware looks for.
    for k in list(ds.data_store):
        if k.startswith(("aero_", "pycycle_")):
            del ds.data_store[k]
    ds.data_store.update(seed)

    av = _decode(tools["create_session"].forward())
    sid = av["session_id"]
    tools["configure_mission"].forward(
        session_id=sid, range_nmi=1500, num_passengers=162,
        cruise_mach=0.785, cruise_altitude_ft=35000,
    )
    # The Phase H/J middleware fires on this call and auto-merges
    # whatever lives in data_store into the parameters dict.
    tools["set_aircraft_parameters"].forward(
        session_id=sid,
        parameters={
            "Aircraft.Wing.AREA": 130.1,
            "Aircraft.Wing.ASPECT_RATIO": 11.0,
            "Aircraft.Engine.SCALE_FACTOR": 1.3,
        },
    )
    tools["run_simulation"].forward(session_id=sid, timeout_seconds=300)
    return _decode(tools["get_results"].forward(session_id=sid))


def test_phase_h_drag_factor_changes_fuel_burn():
    """Sweep three SU2 CD values at fixed CL. Aviary's
    ``SUBSONIC_DRAG_COEFF_FACTOR`` is computed by the middleware as
    (CD + 0.005) / (0.022 + CL² / (π · AR · e)). Higher CD → larger
    factor → more drag at cruise → more fuel.
    """
    tools = _load_aviary_tools()
    if "run_simulation" not in tools:
        pytest.skip("aviary-mcp not loaded")

    cl_fixed = 0.40
    # Three CDs spanning the realistic inviscid-CFD range
    cd_levels = [0.012, 0.025, 0.040]
    fuel_burns: dict[float, float] = {}
    scalers: dict[float, float] = {}
    cruise_cds: dict[float, float] = {}

    for cd in cd_levels:
        result = _run_aviary_with_seeded_data(
            tools,
            {"aero_cl_cruise": cl_fixed, "aero_cd_cruise": cd},
        )
        fuel_burns[cd] = result.get("fuel_burned_kg")
        cruise_cds[cd] = result.get("cruise_cd_avg")
        params = (result.get("design_parameters", {})
                  .get("aircraft_params", {}))
        scalers[cd] = params.get(
            "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"
        )

    print(f"\n  CD     scaler   cruise_CD   fuel_burn_kg")
    for cd in cd_levels:
        print(f"  {cd:.3f}  {scalers[cd]:.3f}    {cruise_cds[cd]:.4f}     {fuel_burns[cd]:.1f}")

    # Scaler must increase with CD (monotone middleware math)
    assert scalers[cd_levels[0]] < scalers[cd_levels[1]] < scalers[cd_levels[2]], (
        f"SUBSONIC_DRAG_COEFF_FACTOR isn't monotone in CD: {scalers}"
    )
    # And the actual cruise CD aviary flew at must increase too —
    # confirms the scaler is reaching the trajectory.
    assert cruise_cds[cd_levels[0]] < cruise_cds[cd_levels[1]] < cruise_cds[cd_levels[2]], (
        f"cruise_cd_avg isn't responding to scaler: {cruise_cds}"
    )
    # Headline: fuel burn must increase monotonically with CD
    assert fuel_burns[cd_levels[0]] < fuel_burns[cd_levels[1]] < fuel_burns[cd_levels[2]], (
        f"Phase H coupling silent — fuel burn doesn't respond to CD: "
        f"{fuel_burns}"
    )


def test_phase_k_wing_mass_changes_fuel_burn():
    """Sweep three mass-mcp wing-mass values. Middleware injects
    ``Aircraft.Wing.MASS_SCALER = wing_kg / 5998``. Higher wing mass
    → larger scaler → heavier MTOM → more fuel.

    Direct sanity-test of the target knob produced
    MASS_SCALER ∈ {0.5, 1.0, 1.5} → fuel 9.7t/10.0t/10.4t
    on the bench (verified before shipping K-A middleware).
    """
    tools = _load_aviary_tools()
    if "run_simulation" not in tools:
        pytest.skip("aviary-mcp not loaded")

    wing_kg_levels = [3000.0, 6000.0, 9000.0]
    fuel_burns: dict[float, float] = {}
    scalers: dict[float, float] = {}

    for wing_kg in wing_kg_levels:
        result = _run_aviary_with_seeded_data(
            tools, {"mass_wing_kg": wing_kg},
        )
        fuel_burns[wing_kg] = result.get("fuel_burned_kg")
        params = (result.get("design_parameters", {})
                  .get("aircraft_params", {}))
        scalers[wing_kg] = params.get("Aircraft.Wing.MASS_SCALER")

    print(f"\n  wing_kg   scaler   fuel_burn_kg")
    for w in wing_kg_levels:
        print(f"  {w:7.0f}   {scalers[w]:.3f}    {fuel_burns[w]:.1f}")

    assert scalers[wing_kg_levels[0]] < scalers[wing_kg_levels[1]] < scalers[wing_kg_levels[2]], (
        f"MASS_SCALER isn't monotone in wing_kg: {scalers}"
    )
    assert fuel_burns[wing_kg_levels[0]] < fuel_burns[wing_kg_levels[1]] < fuel_burns[wing_kg_levels[2]], (
        f"Phase K-A coupling silent — fuel burn doesn't respond to "
        f"wing mass: {fuel_burns}"
    )


@pytest.mark.xfail(
    reason=(
        "Phase J reverted 2026-05-23. SUBSONIC_FUEL_FLOW_SCALER has no "
        "effect on the FwFm bench's tabular engine deck — middleware "
        "kept injecting silently. Sensitivity sweep proved fuel burn = "
        "7058.4 kg for SFC ∈ {0.40, 0.55, 0.70}. Re-enable this test "
        "when pipeline switches to a GASP analytic engine model or "
        "replaces the engine deck file at runtime."
    ),
    strict=True,
)
def test_phase_j_sfc_changes_fuel_burn():
    """Sweep three pyCycle SFC values. With a *working* coupling,
    middleware would inject ``Aircraft.Engine.SUBSONIC_FUEL_FLOW_SCALER
    = SFC / 0.544`` and aviary's cruise_sfc_avg + fuel_burned_kg would
    move monotonically. xfailed because the scaler is currently a no-op
    on FwFm — see this file's docstring and the CHANGELOG entry.
    """
    tools = _load_aviary_tools()
    if "run_simulation" not in tools:
        pytest.skip("aviary-mcp not loaded")

    sfc_levels = [0.45, 0.55, 0.65]
    fuel_burns: dict[float, float] = {}
    for sfc in sfc_levels:
        result = _run_aviary_with_seeded_data(
            tools, {"pycycle_sfc_cruise_lb_per_hr_lbf": sfc},
        )
        fuel_burns[sfc] = result.get("fuel_burned_kg")
    # Will fail (all fuel_burns equal) — that's the documented gap.
    assert fuel_burns[sfc_levels[0]] < fuel_burns[sfc_levels[1]] < fuel_burns[sfc_levels[2]]
