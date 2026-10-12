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


class TestProseIsNeverAMetric:
    """B179 (Jessica, 2026-10-12): metrics come straight from the tools. The old prose fallback filled
    a gap when a mission tool had run; with the tool output unreadable it read "Gross Mass" as nothing
    (gtow 0.0) and missed fuel lines (false zero-fuel links). Now prose gives nothing at all."""

    def test_a_flown_mission_does_not_license_the_prose(self):
        msgs = [_msg(REAL_MISSION_PROSE, tools=("run_simulation",))]
        assert _extract_aviary_eval_from_messages(msgs) is None

    def test_get_results_does_not_license_it_either(self):
        msgs = [_msg(REAL_MISSION_PROSE, tools=("get_results",))]
        assert _extract_aviary_eval_from_messages(msgs) is None

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


# ---- B179: the knowledge base (the parsed tool results) is the source, per chain link and attempt ----

FIXTURE_KB = __import__("pathlib").Path(__file__).parent / "fixtures_kb_v2nothink_missions.jsonl"


def _configure(monkeypatch, link, attempt, path=FIXTURE_KB):
    from src.tools import knowledge_base as kbm

    monkeypatch.setattr(kbm, "_run_config", {"path": str(path), "chain_link": link, "attempt": attempt, "seed": None})


class TestMetricsFromTheKnowledgeBase:
    def test_link1_was_flown_in_every_attempt_the_runner_called_zero_fuel(self, monkeypatch):
        """aipexws8 v2nothink link 1: three attempts discarded as "zero fuel" had flown 22,998-23,030 kg."""
        expected = {1: 23030.134334151855, 2: 22997.584843876724, 3: 22997.983315263606}
        for attempt, fuel in expected.items():
            _configure(monkeypatch, 0, attempt)
            out = _extract_aviary_eval_from_messages([])
            assert out["fuel_burned_kg"] == pytest.approx(fuel)
            assert out["gtow_kg"] > 100000 and out["converged"] is False

    def test_gtow_and_the_get_results_only_fields_come_from_the_tools(self, monkeypatch):
        """Link 5's eval said gtow 0.0 (the agent wrote "Gross Mass"); the tools said 101,409 kg."""
        _configure(monkeypatch, 4, 1)
        out = _extract_aviary_eval_from_messages([_msg("Gross Mass: 101409.13 kg\nFuel Burned: 1 kg")])
        assert out["fuel_burned_kg"] == pytest.approx(22413.49364206984)
        assert out["gtow_kg"] == pytest.approx(101409.13436013744)
        assert out["reserve_fuel_kg"] == pytest.approx(1360.77711)
        assert out["zero_fuel_weight_kg"] == pytest.approx(77634.86360806761)

    def test_an_attempt_whose_missions_were_refused_did_not_fly(self, monkeypatch):
        """Link 4 attempt 1: every run_simulation refused -- no metrics, even with prose present."""
        _configure(monkeypatch, 3, 1)
        assert _extract_aviary_eval_from_messages([_msg(REAL_MISSION_PROSE, tools=("run_simulation",))]) is None
        _configure(monkeypatch, 3, 2)
        assert _extract_aviary_eval_from_messages([])["fuel_burned_kg"] == pytest.approx(20302.31579250006)

    def test_another_attempt_or_link_is_never_used(self, monkeypatch):
        _configure(monkeypatch, 0, 9)                           # no such attempt in the file
        assert _extract_aviary_eval_from_messages([]) is None
        _configure(monkeypatch, 7, 1)                           # no such chain link
        assert _extract_aviary_eval_from_messages([]) is None

    def test_runner_reconciliation_counts_only_this_attempt(self, tmp_path):
        import json as _json

        from scripts.stat_batch_runner import measured_fuel_from_kb

        lines = [_json.loads(l) for l in FIXTURE_KB.read_text().splitlines()]
        link1 = tmp_path / "kb.jsonl"
        link1.write_text("\n".join(_json.dumps(e) for e in lines if e["chain_link"] == 0))
        assert measured_fuel_from_kb(link1, 1) == pytest.approx(23030.134334151855)
        assert measured_fuel_from_kb(link1, 3) == pytest.approx(22997.983315263606)
        assert measured_fuel_from_kb(link1) == pytest.approx(22997.983315263606)   # unchanged without attempt
