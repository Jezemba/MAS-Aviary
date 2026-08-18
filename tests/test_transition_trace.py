"""Every graph transition decision must be traceable.

On 2026-08-17 the handler chose transitions and recorded nothing about why, so a
routing loop could only be reconstructed by grepping state names out of the log --
and those names also appear in prompt text. That produced three confident wrong
diagnoses in one evening: a phantom AERO->GEOMETRY oscillation, a misattributed
substring classifier, and B57's guard "never firing" when the guarded path was
never the one executing.

The trace answers, per decision: which state, which condition matched, what
execution_success was, and what was tried when nothing matched.
"""

import pytest

from src.coordination.graph_routed_handler import GraphRoutedHandler


class _T:
    def __init__(self, condition, target):
        self.condition = condition
        self.target = target


class _S:
    def __init__(self, name, transitions):
        self.name = name
        self.transitions = transitions


@pytest.fixture
def handler():
    h = GraphRoutedHandler({})
    h._state_dict = {"execution_success": True}
    return h


class TestItRecordsTheDecision:
    def test_a_matched_transition_is_traced(self, handler, capsys):
        s = _S("GEOMETRY_SETUP", [_T("execution_success == true", "AERO_ANALYSIS")])
        assert handler._evaluate_transitions(s) == "AERO_ANALYSIS"
        out = capsys.readouterr().out
        assert "GEOMETRY_SETUP -> AERO_ANALYSIS" in out

    def test_it_records_the_condition_that_matched(self, handler, capsys):
        s = _S("GEOMETRY_SETUP", [_T("execution_success == true", "AERO_ANALYSIS")])
        handler._evaluate_transitions(s)
        assert "execution_success == true" in capsys.readouterr().out

    def test_it_records_execution_success(self, handler, capsys):
        """The value that drove the routing -- the thing three diagnoses guessed at."""
        handler._state_dict["execution_success"] = False
        s = _S("GEOMETRY_SETUP", [_T("execution_success == false", "GEOMETRY_SETUP")])
        handler._evaluate_transitions(s)
        assert "execution_success=False" in capsys.readouterr().out

    def test_a_self_loop_is_visible_as_such(self, handler, capsys):
        """17 mesh rebuilds would have been one grep away."""
        handler._state_dict["execution_success"] = False
        s = _S("GEOMETRY_SETUP", [_T("execution_success == false", "GEOMETRY_SETUP")])
        handler._evaluate_transitions(s)
        assert "GEOMETRY_SETUP -> GEOMETRY_SETUP" in capsys.readouterr().out


class TestItRecordsNonDecisions:
    def test_no_match_is_traced_with_what_was_tried(self, handler, capsys):
        handler._state_dict["execution_success"] = None
        s = _S("AERO_ANALYSIS", [_T("execution_success == true", "MASS_ESTIMATION")])
        assert handler._evaluate_transitions(s) is None
        out = capsys.readouterr().out
        assert "(none matched)" in out and "execution_success == true" in out

    def test_no_transitions_at_all_is_traced(self, handler, capsys):
        assert handler._evaluate_transitions(_S("COMPLETE", [])) is None
        assert "(none matched)" in capsys.readouterr().out


class TestItDoesNotChangeRouting:
    """Purely observational -- the returned target must be unaffected."""

    def test_first_match_still_wins(self, handler):
        s = _S("X", [_T("execution_success == true", "FIRST"),
                     _T("always", "SECOND")])
        assert handler._evaluate_transitions(s) == "FIRST"

    def test_unparseable_conditions_are_still_skipped(self, handler, capsys):
        s = _S("X", [_T("!!! not a condition !!!", "BAD"),
                     _T("execution_success == true", "GOOD")])
        assert handler._evaluate_transitions(s) == "GOOD"
