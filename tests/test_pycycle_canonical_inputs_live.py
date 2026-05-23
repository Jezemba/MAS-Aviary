"""Live integration test: agent uses pycycle-mcp's new get_design_inputs.

After the Phase I addition of ``get_design_inputs`` on the pycycle-mcp
``feat/canonical-design-inputs`` branch, a real Claude agent given
the propulsion task should:

1. Create a turbofan cycle model.
2. Call get_design_inputs to discover the canonical paths.
3. Call set_inputs with names that ARE in the canonical list — most
   importantly ``splitter.BPR`` (the path Run #17's agent kept
   guessing wrong as ``fan.BPR`` / ``fan.map.design.BPR``).
4. Run the cycle and get positive thrust + sensible TSFC.

This catches the contract before a full $0.40 pipeline run.

Requires pycycle-mcp on :8400 running on the new branch.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live_mcp_llm


def test_agent_uses_get_design_inputs_and_sets_real_paths():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    from tests.test_run3_regressions import _collect_tool_calls, _make_agent

    agent = _make_agent(
        tool_names=[
            "create_cycle_model",
            "get_design_inputs",
            "set_inputs",
            "run_cycle",
            "get_outputs",
        ],
        servers=["pycycle"],
        max_steps=8,
        instructions=(
            "You are sizing a high-bypass turbofan for a 150-pax "
            "narrow-body at cruise (Mach 0.78, FL330). Use the "
            "pycycle MCP to: 1) create a turbofan design-mode model, "
            "2) discover the design dials via the appropriate tool, "
            "3) set bypass ratio to 11.0, fan pressure ratio to 1.45, "
            "HPC pressure ratio to 40.0, cruise altitude to 33000 ft "
            "and cruise Mach to 0.78, 4) run the cycle, 5) read net "
            "thrust and TSFC. Report all numbers in your final answer."
        ),
    )
    agent.run(
        "Size the engine and report the cruise performance. The "
        "goal is positive thrust and TSFC in [0.5, 0.65] lb/hr/lbf."
    )

    calls = _collect_tool_calls(agent)
    call_names = [c["name"] for c in calls]

    # The new tool should be in the agent's flow somewhere before
    # set_inputs — that's the whole point of exposing it.
    assert "get_design_inputs" in call_names, (
        "Agent did not call get_design_inputs. Without it the agent "
        "is back to guessing names against list_variables's 900+ "
        "promoted entries (the Run #17 failure mode). "
        f"Calls: {call_names}"
    )

    # Whatever names the agent passed to set_inputs, splitter.BPR
    # must appear because that's the only path that actually sets
    # bypass ratio on the HBTF model.
    set_input_calls = [c for c in calls if c["name"] == "set_inputs"]
    assert set_input_calls, "Agent never called set_inputs"

    all_values_set: dict[str, object] = {}
    for c in set_input_calls:
        args = c.get("arguments") or {}
        values = args.get("values") or {}
        # values may arrive as a JSON string (mcpadapt anyOf gap)
        if isinstance(values, str):
            import json

            try:
                values = json.loads(values)
            except Exception:
                values = {}
        if isinstance(values, dict):
            all_values_set.update(values)

    assert "splitter.BPR" in all_values_set, (
        "Agent passed bypass ratio under a guessed name instead of "
        "splitter.BPR — get_design_inputs's metadata didn't steer "
        "the choice. Keys actually set: "
        f"{sorted(all_values_set.keys())}"
    )

    # run_cycle's output should not contain runaway negative thrust
    # (the Run #17 symptom). Any successful run_cycle with a finite
    # result is a pass — we don't pin the exact value.
    run_calls = [c for c in calls if c["name"] == "run_cycle"]
    if run_calls:
        last_obs = run_calls[-1].get("observation") or ""
        assert "e+19" not in last_obs and "e-19" not in last_obs, (
            "run_cycle output contains runaway exponents — the cycle "
            "didn't converge. This is the Run #17 failure mode. "
            f"Observation: {last_obs[:400]}"
        )
