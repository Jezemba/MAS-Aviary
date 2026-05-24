"""Agent-driven regression for the merged set_aircraft_parameters tool.

When ``validate_parameters`` was removed (2026-05-23) and its work folded
into ``set_aircraft_parameters``, three things had to hold for the change
to be useful:

  1. ``set_aircraft_parameters`` returns the merged response shape:
     applied / warnings / valid / summary / violations / model_eval /
     runtime_seconds.
  2. A real LLM agent reads those new fields and reasons over them — it
     does not look for a separate validate tool that no longer exists.
  3. When the agent sees warnings/invalid, it adjusts and re-calls
     ``set_aircraft_parameters`` (no separate validate retry).

This test exercises all three with a live Claude Sonnet 4 driver against
the running aviary-mcp on :8600. Runtime ~1-2 min, cost ~$0.10.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import pytest

pytestmark = pytest.mark.live_mcp_llm


def _make_merge_check_agent():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    from tests.test_run3_regressions import _make_agent

    return _make_agent(
        tool_names=[
            "create_session",
            "configure_mission",
            "set_aircraft_parameters",
        ],
        servers=["aviary"],
        max_steps=8,
        instructions=(
            "You are checking that the merged set_aircraft_parameters tool "
            "returns its validation result inline. Always read the full tool "
            "response (it includes a 'valid' boolean, a 'summary' string, a "
            "'violations' list, and a 'model_eval' object). If valid is false "
            "or warnings appear, adjust the parameter values and call "
            "set_aircraft_parameters again. Do NOT look for a separate "
            "validate_parameters tool — it no longer exists."
        ),
    )


def _scan_set_calls(agent) -> list[dict[str, Any]]:
    """Parse the structured response dicts from every set_aircraft_parameters
    call the agent made."""
    out: list[dict[str, Any]] = []
    for step in agent.memory.steps:
        for tc in getattr(step, "tool_calls", None) or []:
            if tc.name != "set_aircraft_parameters":
                continue
            obs = getattr(step, "observations", "") or ""
            m = re.search(r"\{.*\}", obs, re.DOTALL)
            if not m:
                continue
            try:
                out.append(json.loads(m.group(0)))
            except json.JSONDecodeError:
                pass
    return out


def test_good_params_return_valid_inline():
    """Single set_aircraft_parameters call with good params must return
    valid:true and a populated model_eval — proving validation runs
    inline without a separate validate step."""
    agent = _make_merge_check_agent()
    agent.run(
        "Create an aviary session, configure_mission with range_nmi=1500, "
        "num_passengers=162, cruise_mach=0.785, cruise_altitude_ft=35000, "
        "then call set_aircraft_parameters ONCE with: "
        "Aircraft.Wing.ASPECT_RATIO=11.0, Aircraft.Wing.AREA=130.1, "
        "Aircraft.Engine.SCALE_FACTOR=1.0. "
        "Report the values of the 'valid', 'summary', and 'runtime_seconds' "
        "fields from the response."
    )
    set_calls = _scan_set_calls(agent)
    assert len(set_calls) >= 1, "agent did not call set_aircraft_parameters"
    resp = set_calls[0]
    assert resp.get("valid") is True, (
        f"good params should be valid, got: {resp.get('valid')!r} "
        f"summary={resp.get('summary')!r}"
    )
    assert resp.get("summary", "").startswith("VALID"), resp.get("summary")
    assert resp.get("model_eval", {}).get("success") is True, (
        f"model_eval did not run: {resp.get('model_eval')!r}"
    )
    assert resp.get("runtime_seconds") is not None


def test_agent_reads_warnings_and_retries():
    """When the agent sees warnings on its first call, it must adjust
    parameters and call set_aircraft_parameters again — proving the
    agent understands the merged surface (no separate validate retry)."""
    agent = _make_merge_check_agent()
    agent.run(
        "Create an aviary session, configure_mission with range_nmi=1500, "
        "num_passengers=162, cruise_mach=0.785, cruise_altitude_ft=35000. "
        "Then call set_aircraft_parameters with Aircraft.Wing.ASPECT_RATIO=20.0 "
        "(deliberately OUT of the [7.0, 17.0] advisory range). Read the "
        "response. If warnings appear, adjust AR to 10.5 and call "
        "set_aircraft_parameters again. Repeat until you see valid:true "
        "with no warnings about AR."
    )
    set_calls = _scan_set_calls(agent)
    assert len(set_calls) >= 2, (
        f"agent made only {len(set_calls)} set_aircraft_parameters calls; "
        "expected ≥2 (one with warnings, one clean)"
    )
    first, last = set_calls[0], set_calls[-1]
    assert first.get("warnings"), (
        f"first call (AR=20) should have warnings, got: {first.get('warnings')!r}"
    )
    assert last.get("valid") is True, (
        f"final call should be valid, got: {last.get('valid')!r} "
        f"summary={last.get('summary')!r}"
    )
    assert not last.get("warnings"), (
        f"final call should have no warnings, got: {last.get('warnings')!r}"
    )
