"""Any verdict ends the run, and the verdict is recorded.

Before 2026-08-16 RESULTS_REVIEW looped minor/major_issues back to
GEOMETRY_SETUP, and that loop could not terminate on its own: COMPLETE needs
VERDICT: PASSED, which needs fuel <= 15000, and coupled runs land at
17,900-20,500 kg because real SU2 drag is in the mission. The better the
coupling, the more certain the non-termination -- every coupled graph_routed run
died on the transition cap or the wall clock instead of finishing.

Refinement is not lost: it happens across cumulative chain links, where link k+1
starts from link k's end-state design. The within-run loop was redundant with it,
and it also meant combos differed in how many cycles they could fit before being
cut off -- so part of what the fuel figures measured was "how many cycles fit",
not coordination quality.
"""

import pytest

from src.coordination.graph_definition import load_graph_from_yaml, validate_graph_strict
from src.logging.design_ledger import build_record, _integrator_verdict

GRAPH = "config/mdo_f25_graph.yaml"


@pytest.fixture(scope="module")
def graph():
    return load_graph_from_yaml(GRAPH)


class TestEveryVerdictReachesTheTerminalState:
    def test_all_three_verdicts_go_to_complete(self, graph):
        targets = {tr.condition: tr.target
                   for tr in graph.states["RESULTS_REVIEW"].transitions
                   if "verdict" in (tr.condition or "")}
        assert set(targets.values()) == {"COMPLETE"}, targets

    def test_all_three_verdicts_are_still_handled(self, graph):
        conds = " ".join(tr.condition or "" for tr in graph.states["RESULTS_REVIEW"].transitions)
        for verdict in ("passed", "minor_issues", "major_issues"):
            assert verdict in conds, f"{verdict} no longer has a transition"

    def test_complete_is_terminal(self, graph):
        assert "COMPLETE" in graph.terminal_states


class TestTheGraphIsStillValid:
    """The change must not orphan a state or break reachability."""

    def test_strict_validation_passes(self, graph):
        validate_graph_strict(graph)   # raises if invalid

    def test_geometry_setup_is_still_reachable(self, graph):
        """It is the entry target for moderate/complex tasks and the retry target
        for execution failures -- removing the verdict loop must not orphan it."""
        sources = [n for n, s in graph.states.items()
                   for tr in s.transitions if tr.target == "GEOMETRY_SETUP"]
        assert sources, "GEOMETRY_SETUP became unreachable"
        assert "TASK_CLASSIFIED" in sources

    def test_error_retry_paths_are_untouched(self, graph):
        """execution_success == false must still route back for a retry."""
        conds = [tr.condition for s in graph.states.values() for tr in s.transitions
                 if tr.target == "GEOMETRY_SETUP"]
        assert any("execution_success" in (c or "") for c in conds)


class TestTheVerdictSurvivesAsData:
    """With every verdict terminal, the verdict IS the quality signal."""

    def test_it_is_recorded_in_the_ledger(self):
        tr = {"w": {"steps": [{"output": "'VERDICT': 'MAJOR_ISSUES'"}]}}
        assert build_record({}, tr)["objective"]["integrator_verdict"] == "MAJOR_ISSUES"

    @pytest.mark.parametrize("raw,expected", [
        ("VERDICT: PASSED", "PASSED"),
        ("'VERDICT': 'MINOR_ISSUES'", "MINOR_ISSUES"),
        ('"VERDICT": "MAJOR_ISSUES"', "MAJOR_ISSUES"),
        ("verdict: passed", "PASSED"),
    ])
    def test_it_parses_the_shapes_the_agent_actually_emits(self, raw, expected):
        assert _integrator_verdict({"w": {"steps": [{"output": raw}]}}) == expected

    def test_never_reaching_review_is_None_not_a_failing_verdict(self):
        """A run that never got to the review stage is a DIFFERENT outcome from
        one that reviewed and judged the design inadequate."""
        assert build_record({}, {})["objective"]["integrator_verdict"] is None

    def test_the_last_verdict_wins(self):
        tr = {"w": {"steps": [{"output": "VERDICT: MAJOR_ISSUES"},
                              {"output": "VERDICT: PASSED"}]}}
        assert _integrator_verdict(tr) == "PASSED"
