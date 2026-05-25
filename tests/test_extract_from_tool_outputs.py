"""Regression for the _extract_from_tool_outputs metric extractor in
src/runners/batch_runner.py.

The fix landed 2026-05-25 after wandb clogb51u (the concurrent_blackboard
networked run): the extractor was reading
``set_aircraft_parameters.model_eval.outputs.fuel_burned_kg = 0.0`` —
the inline pre-trajectory placeholder — instead of the real
``run_simulation.summary.fuel_burned_kg`` posted earlier in the run.
The extractor flagged the run as zero-fuel-failed despite the agent
having computed fuel = 12,088 kg.

The fix: treat 0.0 fuel from a set_aircraft_parameters source as
MISSING (a placeholder, not a measurement) so the extractor keeps
scanning for the real value.
"""

from __future__ import annotations

import json

from src.coordination.history import AgentMessage, ToolCallRecord
from src.runners.batch_runner import _extract_from_tool_outputs


def _msg(agent: str, tool_calls: list[ToolCallRecord]) -> AgentMessage:
    return AgentMessage(
        agent_name=agent,
        content="",
        turn_number=1,
        timestamp=0.0,
        tool_calls=tool_calls,
    )


def _tc(name: str, output_dict: dict) -> ToolCallRecord:
    return ToolCallRecord(
        tool_name=name,
        inputs={},
        output=json.dumps(output_dict),
        duration_seconds=0.0,
    )


class TestExtractFromToolOutputs:
    def test_run_simulation_summary_fuel(self):
        """Baseline: run_simulation.summary.fuel_burned_kg is extracted."""
        msg = _msg("agent_1", [
            _tc("run_simulation", {
                "success": True,
                "summary": {"fuel_burned_kg": 12088.1, "gtow_kg": 78948.1, "converged": True},
            }),
        ])
        result = _extract_from_tool_outputs([msg])
        assert result is not None
        assert result["fuel_burned_kg"] == 12088.1
        assert result["gtow_kg"] == 78948.1
        assert result["converged"] is True

    def test_get_results_top_level_fuel(self):
        """get_results returns fuel directly at the top level."""
        msg = _msg("agent_1", [
            _tc("get_results", {
                "success": True,
                "fuel_burned_kg": 11522.6,
                "gtow_kg": 72929.3,
                "converged": True,
            }),
        ])
        result = _extract_from_tool_outputs([msg])
        assert result is not None
        assert result["fuel_burned_kg"] == 11522.6

    def test_set_aircraft_parameters_inline_eval_with_real_fuel(self):
        """set_aircraft_parameters' model_eval CAN carry a real fuel value
        if the agent called it after run_simulation. Non-zero fuel from
        any source is trusted."""
        msg = _msg("agent_1", [
            _tc("set_aircraft_parameters", {
                "success": True,
                "valid": True,
                "model_eval": {
                    "success": True,
                    "outputs": {
                        "fuel_burned_kg": 12500.0,
                        "gtow_kg": 75000.0,
                    },
                },
            }),
        ])
        result = _extract_from_tool_outputs([msg])
        assert result is not None
        assert result["fuel_burned_kg"] == 12500.0

    def test_set_aircraft_parameters_zero_fuel_is_skipped_for_run_sim_value(self):
        """THE REGRESSION CASE — final set_aircraft_parameters with inline
        fuel = 0.0 (placeholder, no trajectory yet) must NOT mask the real
        fuel from an earlier run_simulation call.

        Reproduces wandb clogb51u: run_simulation produced fuel=12088.1
        early, then a subsequent set_aircraft_parameters returned
        model_eval.outputs.fuel_burned_kg = 0.0. The extractor walks
        messages reversed (most-recent first), so without the fix it
        would have seen 0.0 first and stopped looking."""
        messages = [
            _msg("agent_2", [
                _tc("run_simulation", {
                    "success": True,
                    "summary": {"fuel_burned_kg": 12088.1, "gtow_kg": 78948.1, "converged": True},
                }),
            ]),
            _msg("agent_3", [
                _tc("set_aircraft_parameters", {
                    "success": True,
                    "valid": True,
                    "model_eval": {
                        "success": True,
                        "outputs": {
                            "fuel_burned_kg": 0.0,   # placeholder!
                            "gtow_kg": 79560.10,
                            "wing_mass_kg": 8544.6,
                        },
                    },
                }),
            ]),
        ]
        result = _extract_from_tool_outputs(messages)
        assert result is not None
        assert result["fuel_burned_kg"] == 12088.1, (
            "extractor must skip the 0.0 placeholder from set_aircraft_parameters' "
            "inline model_eval and fall back to the real run_simulation value"
        )

    def test_zero_fuel_from_run_simulation_is_kept(self):
        """0.0 fuel from run_simulation is genuinely a failed run; it's
        the SET_AIRCRAFT_PARAMETERS context that's the placeholder, not
        the value itself. If run_simulation actually returns 0, trust it."""
        msg = _msg("agent_1", [
            _tc("run_simulation", {
                "success": True,
                "summary": {"fuel_burned_kg": 0.0, "gtow_kg": 0.0, "converged": False},
            }),
        ])
        result = _extract_from_tool_outputs([msg])
        assert result is not None
        assert result["fuel_burned_kg"] == 0.0

    def test_returns_none_when_no_metric_tool_called(self):
        msg = _msg("agent_1", [
            _tc("create_session", {"success": True, "session_id": "abc"}),
        ])
        assert _extract_from_tool_outputs([msg]) is None

    def test_returns_none_when_only_zero_placeholder_present(self):
        """If the ONLY fuel signal in the run is the
        set_aircraft_parameters placeholder, treat as missing —
        better None than a false zero."""
        msg = _msg("agent_1", [
            _tc("set_aircraft_parameters", {
                "success": True,
                "valid": True,
                "model_eval": {
                    "success": True,
                    "outputs": {"fuel_burned_kg": 0.0, "gtow_kg": 79560.0},
                },
            }),
        ])
        result = _extract_from_tool_outputs([msg])
        # No real fuel anywhere -> extractor reports missing.
        assert result is None
