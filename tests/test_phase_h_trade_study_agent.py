"""Agent-driven trade study validating Phase H aero coupling.

The direct sensitivity test in ``test_phase_hj_coupling_validation``
proves the wrapped-tool path responds correctly. This test adds the
recurring-coverage layer: a real Claude agent runs multiple aviary
missions across a CD sweep, reports fuel burns, and we assert the
relationship is monotonic.

Cheaper than the full pipeline (~5 min vs ~12 min, ~$0.30 vs ~$0.50)
and isolates the H coupling from the rest of the disciplinary chain.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import pytest

pytestmark = pytest.mark.live_mcp_llm


def _make_trade_study_agent():
    """Build a focused agent with just the aviary mission tools."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    from tests.test_run3_regressions import _make_agent

    return _make_agent(
        tool_names=[
            "create_session",
            "configure_mission",
            "set_aircraft_parameters",
            "run_simulation",
            "get_results",
        ],
        servers=["aviary"],
        max_steps=20,  # 3 candidates × ~5 calls each + margin
        instructions=(
            "You are running a parametric trade study on aviary's "
            "mission. For each of the three candidates you'll be "
            "given (a target cruise CL and CD), you must: "
            "(1) create a fresh aviary session, "
            "(2) configure_mission with range_nmi=1500, "
            "num_passengers=162, cruise_mach=0.785, "
            "cruise_altitude_ft=35000, "
            "(3) call set_aircraft_parameters with parameters "
            "containing exactly Aircraft.Wing.AREA=130.1, "
            "Aircraft.Wing.ASPECT_RATIO=11.0, "
            "Aircraft.Engine.SCALE_FACTOR=1.3 (only these three keys, "
            "the framework injects others automatically), "
            "(4) run_simulation, "
            "(5) get_results and report the fuel_burned_kg + "
            "cruise_cl_avg + cruise_cd_avg. "
            "Report all three candidates in ONE final answer using "
            "this exact format per candidate, one block per row:\n"
            "  CANDIDATE k: target_CL=<x> target_CD=<y> "
            "fuel_burned_kg=<z> cruise_cl=<a> cruise_cd=<b>"
        ),
    )


def test_phase_h_agent_trade_study_responds_to_drag():
    """Drive a real Claude agent through a 3-point CD sweep. Assert
    fuel burn responds monotonically to drag."""
    agent = _make_trade_study_agent()

    from src.tools.data_plane import get_design_state
    ds = get_design_state()
    assert ds is not None

    cl_fixed = 0.40
    candidates = [
        (cl_fixed, 0.015),
        (cl_fixed, 0.025),
        (cl_fixed, 0.040),
    ]

    # Pre-seed BEFORE the agent runs — the middleware will pick up
    # whatever's in data_store when set_aircraft_parameters fires.
    # Trade-study trick: rotate the data_store between candidate runs
    # by editing the agent's instruction to handle one candidate at a
    # time would be cleaner, but smolagents wraps the whole run() so
    # we let the agent loop and switch out the seed mid-flight via a
    # before-each-step monkey patch.

    # Simpler approach: run the agent THREE TIMES, once per candidate,
    # so each run's middleware uses the matching pre-seeded CD.
    results: list[dict[str, Any]] = []
    for idx, (cl, cd) in enumerate(candidates):
        # Reset stale entries
        for k in list(ds.data_store):
            if k.startswith(("aero_", "pycycle_")):
                del ds.data_store[k]
        ds.data_store["aero_cl_cruise"] = cl
        ds.data_store["aero_cd_cruise"] = cd

        sub_agent = _make_trade_study_agent()
        sub_agent.run(
            f"Trade study candidate #{idx+1} of {len(candidates)}: "
            f"the upstream SU2 stage produced CL={cl}, CD={cd}. "
            "Do steps 1–5 from your instructions ONCE for this "
            "candidate and report a single CANDIDATE line."
        )
        # Pull fuel_burned from the agent's get_results call
        for step in sub_agent.memory.steps:
            tool_calls = getattr(step, "tool_calls", None) or []
            for tc in tool_calls:
                if tc.name == "get_results":
                    obs = getattr(step, "observations", "") or ""
                    m = re.search(r'"fuel_burned_kg":\s*([0-9.eE+-]+)', obs)
                    if m:
                        results.append({
                            "cl": cl, "cd": cd,
                            "fuel": float(m.group(1)),
                        })
                        break
            if len(results) > idx:
                break

    assert len(results) == len(candidates), (
        f"Expected {len(candidates)} fuel readings, got {len(results)}. "
        f"Results so far: {results}"
    )

    print("\nAgent-driven Phase H trade study:")
    for r in results:
        print(f"  CL={r['cl']:.3f} CD={r['cd']:.3f}  fuel={r['fuel']:.1f} kg")

    fuels = [r["fuel"] for r in results]
    assert fuels[0] < fuels[1] < fuels[2], (
        f"Agent trade study failed monotonicity check on Phase H "
        f"coupling: fuels = {fuels}. CD increased {candidates[0][1]} → "
        f"{candidates[2][1]} but fuel didn't track."
    )
