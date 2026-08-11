"""A run that never flew a mission must not report mission metrics.

Observed TWICE in live runs (.claude/BUGS.md B19). `orchestrated_staged_pipeline`
never called run_simulation / get_results / set_aircraft_parameters, yet recorded

    fuel_burned_kg 24908.495    gtow_kg 78126.0    wing_mass_kg 7560.59

Those are mass-mcp's `mFuel_kg` (structural fuel CAPACITY), `mTOM_kg` and flops
wing mass. The structures_analyst relabelled them in its prose and `_FUEL_RE`
believed it.

The first occurrence was classified sim_fail / converged=False, so it was at
least excludable. The SECOND was classified **converged=True** — a fabricated
row indistinguishable from a legitimate data point, which nothing downstream
would filter out. That is silent corruption of the results table.

The prose fallback is kept for its legitimate case (a REAL mission whose tool
output was truncated) but gated on evidence the mission actually ran.
"""

import pytest

from src.coordination.history import AgentMessage, ToolCallRecord
from src.runners.batch_runner import _extract_aviary_eval_from_messages

# Verbatim from the failing run: the structures_analyst's summary.
FABRICATED_PROSE = (
    "STRUCTURES SUMMARY\n"
    "fuel_burned_kg: 24908.495429590308\n"
    "mtom_kg: 78126.0\n"
    "optimality_gap_pct: 106.67\n"
    "VERDICT: CONTINUE\n"
)

REAL_MISSION_PROSE = "Mission complete. fuel_burned_kg: 12776.3\ngtow_kg: 78126.0\n"


def _msg(content, tools=()):
    return AgentMessage(
        agent_name="structures_analyst",
        content=content,
        turn_number=1,
        timestamp=0.0,
        tool_calls=[
            ToolCallRecord(tool_name=t, inputs={}, output="{}", duration_seconds=0.0)
            for t in tools
        ],
    )


class TestNoMissionMeansNoMetrics:
    def test_the_live_fabrication_is_rejected(self):
        """THE regression: mass numbers relabelled as mission fuel."""
        msgs = [_msg(FABRICATED_PROSE, tools=("estimate_mass", "run_su2_solver"))]
        assert _extract_aviary_eval_from_messages(msgs) is None

    def test_no_tool_calls_at_all_yields_nothing(self):
        assert _extract_aviary_eval_from_messages([_msg(FABRICATED_PROSE)]) is None

    def test_aero_and_geometry_alone_do_not_license_metrics(self):
        msgs = [_msg(FABRICATED_PROSE, tools=("open_cpacs", "generate_volume_mesh",
                                              "run_su2_solver", "estimate_mass"))]
        assert _extract_aviary_eval_from_messages(msgs) is None

    def test_set_aircraft_parameters_alone_is_not_enough(self):
        """It configures the aircraft and returns a validation probe — it does
        not fly a trajectory, so it cannot license a prose metric."""
        msgs = [_msg(FABRICATED_PROSE, tools=("set_aircraft_parameters",))]
        assert _extract_aviary_eval_from_messages(msgs) is None


class TestRealMissionsStillWork:
    def test_run_simulation_licenses_the_prose_fallback(self):
        """The fallback's legitimate purpose: a real mission whose tool output
        was truncated."""
        msgs = [_msg(REAL_MISSION_PROSE, tools=("run_simulation",))]
        out = _extract_aviary_eval_from_messages(msgs)
        assert out is not None
        assert out["fuel_burned_kg"] == pytest.approx(12776.3)

    def test_get_results_also_licenses_it(self):
        msgs = [_msg(REAL_MISSION_PROSE, tools=("get_results",))]
        out = _extract_aviary_eval_from_messages(msgs)
        assert out is not None and out["fuel_burned_kg"] == pytest.approx(12776.3)

    def test_errored_mission_call_does_not_license(self):
        m = _msg(FABRICATED_PROSE)
        m.tool_calls = [
            ToolCallRecord(
                tool_name="run_simulation", inputs={}, output="",
                duration_seconds=0.0, error="boom",
            )
        ]
        assert _extract_aviary_eval_from_messages([m]) is None


class TestToolOutputStillGroundTruth:
    def test_real_tool_output_is_used_regardless_of_prose(self):
        """Tool output is ground truth and must win over any prose."""
        m = _msg("fuel_burned_kg: 99999.0", tools=())
        m.tool_calls = [
            ToolCallRecord(
                tool_name="get_results",
                inputs={},
                output='{"success": true, "fuel_burned_kg": 12345.6, "gtow_kg": 70000.0}',
                duration_seconds=0.0,
            )
        ]
        out = _extract_aviary_eval_from_messages([m])
        assert out["fuel_burned_kg"] == pytest.approx(12345.6)
